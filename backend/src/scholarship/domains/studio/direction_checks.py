"""Source-anchored follow-up search; metadata matches never prove a problem unsolved."""

from datetime import datetime, timezone
from uuid import UUID
import sqlalchemy as sa
from scholarship.adapters.providers import ContractError
from scholarship.domains.deep_parse import version_scope
from scholarship.domains.retrieval_profile import canonical, lexical
from scholarship.domains.studio.discovery import search
from scholarship.domains.studio.artifacts import publish
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T

MAX_RESULTS = 10


def check(owner, project, run, inputs, *, transport=None):
    query = inputs.get("search_query", "").strip()
    ids = [UUID(p) for p in inputs.get("parse_ids", [])]
    if not 3 <= len(query) <= 500 or not 1 <= len(set(ids)) <= 30:
        raise ContractError("explicit_followup_query_and_sources_required")
    anchors = []
    with tenant_transaction(owner) as conn:
        for pid in ids:
            p = (
                conn.execute(
                    sa.select(T["document_parses"]).where(
                        T["document_parses"].c.id == pid,
                        T["document_parses"].c.project_id == project,
                        T["document_parses"].c.status == "ready",
                    )
                )
                .mappings()
                .first()
            )
            if not p:
                raise ContractError("followup_source_not_available")
            version_scope(conn, p["version_id"])
            for b in conn.execute(
                sa.select(T["document_blocks"], T["document_pages"].c.page_no)
                .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
                .where(T["document_blocks"].c.parse_id == pid)
            ).mappings():
                text = b["canonical_text"]
                if any(
                    term in text.casefold()
                    for term in ["future work", "in the future", "we plan", "limitation", "未来", "局限"]
                ):
                    anchors.append(
                        {
                            "parse_id": str(pid),
                            "version_id": str(p["version_id"]),
                            "block_id": str(b["id"]),
                            "page": b["page_no"],
                            "quote": text[:1500],
                            "start": 0,
                            "end": min(1500, len(text)),
                            "method": "literal_future_work_keyword_candidate",
                        }
                    )
    records = search(owner, project, "arxiv", query, MAX_RESULTS, transport=transport)
    terms = set(lexical(query).split())
    candidates = [
        {
            **r,
            "lexical_overlap": len(terms & set(lexical(r["title"] + " " + r.get("abstract", "")).split())),
            "assessment": "metadata_only_requires_fulltext",
            "problem_solved": None,
        }
        for r in records
    ]
    result = {
        "query": query,
        "provider": "arxiv",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "query_scope": "explicit user query only; no local PDF or source paragraphs sent",
        "source_anchors": anchors[:30],
        "candidates": candidates,
        "status": "followup_candidates_found" if candidates else "no_hits_in_this_sample",
        "problem_solved": None,
        "scientific_quality_verified": False,
        "model_requests": 0,
        "coverage": {"max_results": MAX_RESULTS, "metadata_only": True, "original_sources": len(ids)},
        "limitations_notice": "只检索到标题/摘要候选，不判定原问题已经解决或仍未解决。需阅读后续全文并逐条比较方法、假设及结果；无命中也不是新颖性证明。",
    }
    with tenant_transaction(owner) as conn:
        result["metadata_evidence_ids"] = [
            str(
                conn.scalar(
                    T["evidence_items"]
                    .insert()
                    .values(
                        owner_id=owner,
                        project_id=project,
                        run_id=run,
                        evidence_kind="external_record",
                        source_record_id=UUID(r["source_record_id"]),
                        locator_json={"scope": r["scope"], "provider": "arxiv", "metadata_only": True},
                        retrieval_json={"query": query, "checked_at": result["checked_at"]},
                    )
                    .returning(T["evidence_items"].c.id)
                )
            )
            for r in records
        ]
        aid = publish(
            conn,
            owner,
            project,
            run,
            "direction_check",
            "followup-evidence.json",
            canonical(result).encode(),
            mime="application/json",
            suffix="json",
            metadata={
                "candidates": [{k: r[k] for k in ("title", "url", "assessment")} for r in candidates],
                "limitations_notice": result["limitations_notice"],
                "status": result["status"],
                "checked_at": result["checked_at"],
                "query": query,
                "model_requests": 0,
            },
            inputs=[{"role": "source", "source_parse_id": pid} for pid in ids],
        )
    return {
        **result,
        "artifact_version_id": str(aid),
        "external_metadata_requests": "public search; cached if available",
        "model_simulated": False,
    }
