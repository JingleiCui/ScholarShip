"""Explicit public scholarly metadata; bounded HTTPS, no arbitrary-URL fetches."""

from datetime import datetime, timezone
import json
import re
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import httpx
import sqlalchemy as sa
from scholarship.adapters.providers import ContractError
from scholarship.domains.retrieval_profile import digest, canonical, lexical
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T

MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_RESULTS = 20
ARXIV_ID = re.compile(r"\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?")


class DiscoveryClient:
    def __init__(self, transport=None):
        self.synthetic_transport = transport is not None
        self.client = httpx.Client(
            transport=transport,
            timeout=25,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "ScholarShip/0.1 (academic metadata discovery)"},
        )

    def close(self):
        self.client.close()

    def get(self, url, params=None, limit=MAX_METADATA_BYTES):
        try:
            if url.startswith("https://export.arxiv.org/api/") and not self.synthetic_transport:
                from scholarship.infrastructure.database import engine

                # Shared across API and worker processes; at least 3 s between
                # dispatches, with a short wait limit rather than an unbounded queue.
                with engine().begin() as conn:
                    conn.execute(sa.text("SET LOCAL lock_timeout='4s'"))
                    conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended('scholarship-arxiv-api',0))"))
                    conn.execute(sa.text("SELECT pg_sleep(3)"))
            return self._get(url, params, limit)
        except httpx.HTTPError:
            raise ContractError("metadata_network_unavailable") from None
        except sa.exc.DBAPIError:
            raise ContractError("metadata_queue_busy") from None

    def _get(self, url, params, limit):
        with self.client.stream("GET", url, params=params) as response:
            if response.status_code != 200:
                raise ContractError("metadata_service_unavailable")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > limit:
                    raise ContractError("metadata_response_limit")
            return bytes(body)

    def search(self, provider, query, limit=10):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500 or not 1 <= limit <= MAX_RESULTS:
            raise ContractError("invalid_metadata_query")
        if provider == "arxiv":
            raw = self.get(
                "https://export.arxiv.org/api/query",
                {
                    "search_query": "all:" + query,
                    "start": 0,
                    "max_results": limit,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                },
            )
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise ContractError("unsafe_metadata_xml")
            try:
                feed = ET.fromstring(raw)
                ns = {"a": "http://www.w3.org/2005/Atom"}
                items = []
                for entry in feed.findall("a:entry", ns):
                    url = entry.findtext("a:id", "", ns)
                    aid = urlsplit(url).path.removeprefix("/abs/")
                    if not ARXIV_ID.fullmatch(aid):
                        raise ValueError()
                    items.append(
                        {
                            "external_id": aid,
                            "title": " ".join(entry.findtext("a:title", "", ns).split()),
                            "authors": [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)],
                            "abstract": entry.findtext("a:summary", "", ns)[:20000],
                            "published": entry.findtext("a:published", "", ns),
                            "updated": entry.findtext("a:updated", "", ns),
                            "url": "https://arxiv.org/abs/" + aid,
                            "pdf_url": "https://arxiv.org/pdf/" + aid,
                            "scope": "abstract",
                            "provider": "arxiv",
                        }
                    )
                return items
            except (ValueError, ET.ParseError):
                raise ContractError("invalid_arxiv_response") from None
        if provider == "crossref":
            try:
                data = json.loads(
                    self.get("https://api.crossref.org/works", {"query.bibliographic": query, "rows": limit})
                )
                return [
                    {
                        "external_id": item["DOI"].lower(),
                        "title": item.get("title", [""])[0],
                        "authors": [
                            " ".join([a.get("given", ""), a.get("family", "")]).strip() for a in item.get("author", [])
                        ],
                        "abstract": item.get("abstract", "")[:20000],
                        "published": str(item.get("published", {}).get("date-parts", [])),
                        "url": "https://doi.org/" + item["DOI"],
                        "pdf_url": None,
                        "scope": "metadata",
                        "provider": "crossref",
                    }
                    for item in data["message"]["items"]
                ]
            except (ValueError, KeyError, TypeError, IndexError):
                raise ContractError("invalid_crossref_response") from None
        raise ContractError("unknown_metadata_provider")

    def pdf(self, record):
        aid = record.get("external_id", "")
        if record.get("provider") != "arxiv" or not ARXIV_ID.fullmatch(aid):
            raise ContractError("only_explicit_arxiv_pdf_import_supported")
        raw = self.get("https://arxiv.org/pdf/" + aid, limit=32 * 1024 * 1024)
        if not raw.startswith(b"%PDF-"):
            raise ContractError("source_did_not_return_pdf")
        return raw


def store_records(owner, project, query, records, *, subscription_id=None):
    from fastapi import HTTPException

    found = []
    with tenant_transaction(owner) as conn:
        if not conn.scalar(sa.select(T["projects"].c.id).where(T["projects"].c.id == project)):
            raise HTTPException(404, "project_not_found")
        for record in records:
            h = digest(canonical(record))
            q = digest(canonical({"query": query, "provider": record["provider"]}))
            sid = conn.scalar(
                sa.select(T["source_records"].c.id).where(
                    T["source_records"].c.project_id == project,
                    T["source_records"].c.provider == record["provider"],
                    T["source_records"].c.external_id == record["external_id"],
                    T["source_records"].c.request_fingerprint == q,
                    T["source_records"].c.content_sha256 == h,
                )
            )
            if not sid:
                sid = conn.scalar(
                    T["source_records"]
                    .insert()
                    .values(
                        owner_id=owner,
                        project_id=project,
                        provider=record["provider"],
                        external_id=record["external_id"],
                        request_fingerprint=q,
                        retrieved_at=sa.func.now(),
                        record_json=record,
                        content_sha256=h,
                        access_scope=record["scope"],
                    )
                    .returning(T["source_records"].c.id)
                )
            key = record["provider"] + ":" + re.sub(r"v\d+$", "", record["external_id"])
            rec = conn.scalar(
                sa.select(T["recommendations"].c.id).where(
                    T["recommendations"].c.project_id == project,
                    T["recommendations"].c.canonical_work_key == key,
                    T["recommendations"].c.algorithm_revision == "lexical-interest-v1",
                )
            )
            if not rec:
                score = len(
                    set(lexical(query).split())
                    & set(lexical(record["title"] + " " + record.get("abstract", "")).split())
                )
                rec = conn.scalar(
                    T["recommendations"]
                    .insert()
                    .values(
                        owner_id=owner,
                        project_id=project,
                        source_record_id=sid,
                        subscription_id=subscription_id,
                        canonical_work_key=key,
                        score_json={"lexical_overlap": score},
                        reason_json={"query": query, "basis": "title_abstract_lexical_match", "scope": record["scope"]},
                        code_state_json={"status": "not_checked", "reproduction_verified": False},
                        algorithm_revision="lexical-interest-v1",
                    )
                    .returning(T["recommendations"].c.id)
                )
            found.append({"source_record_id": str(sid), "recommendation_id": str(rec), **record})
    return found


def search(owner, project, provider, query, limit=10, *, transport=None, subscription_id=None):
    if transport is None:
        fingerprint = digest(canonical({"query": query, "provider": provider}))
        with tenant_transaction(owner) as conn:
            cached = (
                conn.execute(
                    sa.select(T["source_records"].c.record_json)
                    .where(
                        T["source_records"].c.project_id == project,
                        T["source_records"].c.request_fingerprint == fingerprint,
                        T["source_records"].c.retrieved_at > sa.func.now() - sa.text("interval '15 minutes'"),
                    )
                    .order_by(T["source_records"].c.retrieved_at.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        if len(cached) >= limit:
            return store_records(owner, project, query, cached, subscription_id=subscription_id)
    client = DiscoveryClient(transport)
    try:
        return store_records(
            owner, project, query, client.search(provider, query, limit), subscription_id=subscription_id
        )
    finally:
        client.close()


def personalize(rows):
    """Explainable, bounded feedback influence; no claim of semantic relevance."""

    def tokens(row):
        record = row["record_json"]
        return set(lexical(record["title"] + " " + record.get("abstract", "")[:2000]).split())

    interests = [(tokens(r), r["vote"]) for r in rows if r.get("vote") in {-1, 1}]
    result = []
    for row in rows:
        words = tokens(row)
        influence = sum(v * len(words & terms) / max(1, len(words | terms)) for terms, v in interests)
        influence = max(-2, min(2, influence))
        score = float(row["score_json"].get("lexical_overlap", 0)) + influence
        result.append(
            {
                **row,
                "score_json": {**row["score_json"], "feedback_influence": influence, "rank_score": score},
                "reason_json": {
                    **row["reason_json"],
                    "feedback_basis": "title_abstract_token_overlap",
                    "feedback_samples": len(interests),
                },
            }
        )
    return sorted(result, key=lambda r: (-r["score_json"]["rank_score"], str(r["id"])))


def run_subscription(owner, project, sid, *, transport=None):
    from datetime import timedelta
    from uuid import uuid4

    token = uuid4()
    now = datetime.now(timezone.utc)
    with tenant_transaction(owner) as conn:
        sub = (
            conn.execute(
                sa.select(T["subscriptions"])
                .where(T["subscriptions"].c.id == sid, T["subscriptions"].c.project_id == project)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if (
            not sub
            or not sub["enabled"]
            or sub["next_due_at"] > now
            or (sub["lease_until"] and sub["lease_until"] > now)
        ):
            return None
        conn.execute(
            T["subscriptions"]
            .update()
            .where(T["subscriptions"].c.id == sid)
            .values(lease_token=token, lease_until=now + timedelta(minutes=3))
        )
    try:
        result = search(
            owner, project, "arxiv", sub["query_json"]["query"], 10, transport=transport, subscription_id=sid
        )
        state = {"checked_at": now.isoformat(), "found": len(result), "status": "completed"}
    except Exception:
        state = {"checked_at": now.isoformat(), "status": "metadata_service_unavailable"}
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["subscriptions"]
            .update()
            .where(T["subscriptions"].c.id == sid, T["subscriptions"].c.lease_token == token)
            .values(
                cursor_state=state,
                next_due_at=now + timedelta(hours=max(6, sub["schedule_json"].get("hours", 24))),
                lease_token=None,
                lease_until=None,
            )
        )
        conn.execute(
            T["audit_events"]
            .insert()
            .values(
                owner_id=owner,
                project_id=project,
                action="subscription_checked",
                subject_json={"subscription_id": str(sid)},
                safe_payload=state,
            )
        )
    return state


def verify_code(owner, recommendation, *, transport=None):
    from fastapi import HTTPException

    with tenant_transaction(owner) as conn:
        record = (
            conn.execute(
                sa.select(T["recommendations"], T["source_records"].c.record_json)
                .join(T["source_records"], T["source_records"].c.id == T["recommendations"].c.source_record_id)
                .where(T["recommendations"].c.id == recommendation)
            )
            .mappings()
            .first()
        )
        if not record:
            raise HTTPException(404, "recommendation_not_found")
    source = record["record_json"]
    urls = re.findall(
        r"https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9_.-]{1,100})(?![A-Za-z0-9_.-])",
        source.get("abstract", ""),
    )
    candidates = list(dict.fromkeys((a, b.rstrip(".,;").removesuffix(".git")) for a, b in urls))[:3]
    state = {
        "status": "no_explicit_repository_in_metadata" if not candidates else "checked",
        "reproduction_verified": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "scope": "explicit links in retrieved metadata only",
        "repositories": [],
    }
    client = DiscoveryClient(transport)
    try:
        for account, repo in candidates:
            data = json.loads(client.get(f"https://api.github.com/repos/{account}/{repo}"))
            if (
                data.get("private") is not False
                or str(data.get("full_name", "")).casefold() != (account + "/" + repo).casefold()
            ):
                raise ContractError("repository_identity_unverified")
            state["repositories"].append(
                {
                    "url": f"https://github.com/{account}/{repo}",
                    "exists": True,
                    "archived": bool(data.get("archived")),
                    "license_spdx": (data.get("license") or {}).get("spdx_id"),
                    "stars": data.get("stargazers_count"),
                    "reproduction_verified": False,
                    "paper_code_correspondence": "metadata_link_only_not_independently_verified",
                }
            )
    except (ContractError, ValueError, TypeError):
        state["status"] = "repository_check_incomplete"
    finally:
        client.close()
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["recommendations"]
            .update()
            .where(T["recommendations"].c.id == recommendation)
            .values(code_state_json=state, updated_at=sa.func.now())
        )
    return state
