"""One approved public scan, free-policy admission, durable upload/poll lifecycle.

This validation never publishes a ready scientific parse or enables general upload.
"""

from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import stat
import time
from uuid import UUID, uuid5, NAMESPACE_URL
import zipfile

import httpx
from pypdf import PdfReader
import sqlalchemy as sa

from scholarship.adapters.providers import ContractError, safe_url, SIGNED_UPLOAD_HOSTS, RESULT_HOSTS
from scholarship.adapters.providers import MAX_PAGES as MINERU_MAX_PAGES
from scholarship.models.catalog import credential, configuration
from scholarship.models.contracts import ProviderFailure
from scholarship.models.configuration import fingerprint
from scholarship.models.transports import network_transport, download_with_dns_override
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.config import PROJECT_ROOT

# Provider policy checked 2026-11-14; local preview limits are separate.
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class PdfPart:
    first: int
    last: int

    def source_page(self, local_page):
        if type(local_page) is not int or not 1 <= local_page <= self.last - self.first + 1:
            raise ContractError("invalid_part_page")
        return self.first + local_page - 1


def plan_parts(total_pages, limit=MINERU_MAX_PAGES):
    if type(total_pages) is not int or total_pages < 1 or type(limit) is not int or not 1 <= limit <= MINERU_MAX_PAGES:
        raise ContractError("invalid_partition_plan")
    return [PdfPart(start, min(start + limit - 1, total_pages)) for start in range(1, total_pages + 1, limit)]


def scan_scope(source, policy):
    source = Path(source)
    raw = source.read_bytes()
    pages = len(PdfReader(BytesIO(raw)).pages)
    if source.name != "1706.03762v1_img.pdf" or pages != 15 or len(raw) > 128 * 1024 * 1024:
        raise ContractError("scan_outside_user_approval")
    if (
        policy.get("commercial_charging") is not False
        or policy.get("source_url") != "https://mineru.net/apiManage/limit"
    ):
        raise ContractError("free_policy_not_verified")
    return {
        "filename": source.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "pages": pages,
        "byte_size": len(raw),
        "maximum_files": 1,
        "free_policy": policy,
        "model": "vlm",
        "ocr": True,
    }


def issue_scan_approval(db, *, key, scope, owner_id, project_id, approved=False):
    if not approved:
        raise ContractError("scan_approval_required")
    run = uuid5(NAMESPACE_URL, "scholarship:" + key)
    with db.begin() as conn:
        old = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == run)).mappings().first()
        if old:
            if old["input_json"] != scope or old["owner_id"] != UUID(str(owner_id)):
                raise ContractError("scan_approval_conflict")
            return dict(old)
        if not conn.scalar(
            sa.select(T["projects"].c.id).where(T["projects"].c.id == project_id, T["projects"].c.owner_id == owner_id)
        ):
            raise ContractError("scan_project_not_owned")
        row = (
            conn.execute(
                T["runs"]
                .insert()
                .values(
                    id=run,
                    owner_id=owner_id,
                    project_id=project_id,
                    kind="mineru_validation",
                    status="queued",
                    idempotency_key=key,
                    request_sha256=fingerprint(scope),
                    input_json=scope,
                    config_json={"validation_only": True, "free_policy_verified": True},
                    input_scope_json={},
                    next_event_seq=1,
                    event_floor=1,
                )
                .returning(T["runs"])
            )
            .mappings()
            .one()
        )
        conn.execute(
            T["audit_events"]
            .insert()
            .values(
                owner_id=owner_id,
                project_id=project_id,
                action="public_scan_free_validation_approved",
                subject_json={"run_id": str(run)},
                safe_payload={"sha256": scope["sha256"], "pages": 15, "maximum_files": 1, "free_policy_verified": True},
            )
        )
        return dict(row)


def inspect_scan_archive(raw, expected_pages):
    files = {}
    total = 0
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        infos = archive.infolist()
        if len(infos) > 2000:
            raise ContractError("mineru_archive_too_many_members")
        for item in infos:
            path = PurePosixPath(item.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in item.filename
                or ":" in item.filename
                or stat.S_ISLNK(item.external_attr >> 16)
                or item.flag_bits & 1
                or item.filename in files
            ):
                raise ContractError("unsafe_mineru_archive")
            total += item.file_size
            if (
                item.file_size > 32 * 1024 * 1024
                or total > 256 * 1024 * 1024
                or item.file_size > max(1, item.compress_size) * 200
            ):
                raise ContractError("mineru_archive_expansion_limit")
            if not item.is_dir() and path.suffix in {".json", ".md"}:
                files[item.filename] = archive.read(item)
    markdown = [name for name in files if name.endswith("full.md")]
    if not markdown:
        markdown = [name for name in files if name.endswith(".md")]
    if len(markdown) != 1:
        raise ContractError("mineru_markdown_ambiguous")
    text = files[markdown[0]].decode("utf-8")
    middle = [name for name in files if name.endswith("middle.json")]
    if not middle:
        middle = [name for name in files if PurePosixPath(name).name == "layout.json"]
    page_numbers = []
    producer_version = None
    layout_backend = None
    if len(middle) == 1:
        doc = json.loads(files[middle[0]])
        pages = doc.get("pdf_info", []) if isinstance(doc, dict) else []
        producer_version = doc.get("_version_name") if isinstance(doc, dict) else None
        layout_backend = doc.get("_backend") if isinstance(doc, dict) else None
        page_numbers = [p["page_idx"] + 1 for p in pages if isinstance(p, dict) and type(p.get("page_idx")) is int]
    if sorted(page_numbers) != list(range(1, expected_pages + 1)):
        raise ContractError("mineru_page_coverage_unverified")
    import re

    normalized = " ".join(text.split())
    return {
        "pages": len(page_numbers),
        "layout_source": middle[0],
        "producer_version": producer_version,
        "layout_backend": layout_backend,
        "geometry_mapping_verified": False,
        "markdown_characters": len(text),
        "members": sorted(files),
        "hardware_sample_found": bool(re.search(r"8\s+NVIDIA\s+P100", normalized, re.I)),
        "adam_09_found": bool(re.search(r"0\.9(?!\d)", normalized)),
        "adam_098_found": "0.98" in normalized,
        "publishable": False,
        "markdown": text,
    }


class MinerUScanValidation:
    def __init__(self, owner, run, source, *, poll_limit=40, poll_interval=3, transport=None):
        self.owner, self.run, self.source = owner, UUID(str(run)), Path(source)
        with tenant_transaction(owner) as conn:
            self.record = dict(conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == self.run)).mappings().one())
        self.scope = self.record["input_json"]
        if (
            self.record["kind"] != "mineru_validation"
            or scan_scope(self.source, self.scope["free_policy"]) != self.scope
        ):
            raise ContractError("scan_scope_changed")
        self.poll_limit, self.poll_interval = poll_limit, poll_interval
        self.injected_transport = transport
        self.client = httpx.Client(
            transport=transport or network_transport("mineru_cloud"),
            timeout=120,
            follow_redirects=False,
            trust_env=False,
        )
        self.polls = 0

    def close(self):
        self.client.close()

    def _json(self, method, url, **kwargs):
        key = credential("mineru_cloud")
        if not key:
            raise ContractError("mineru_key_missing")
        response = self.client.request(method, url, headers={"Authorization": "Bearer " + key}, **kwargs)
        if response.status_code != 200:
            raise ProviderFailure("mineru_http_error", response.status_code, unknown=response.status_code >= 500)
        data = response.json()
        if data.get("code") != 0:
            raise ProviderFailure("mineru_application_rejected", 200)
        return data["data"]

    def _once(self, name, payload, fn, *, readonly=False):
        with tenant_transaction(self.owner) as conn:
            old = (
                conn.execute(
                    sa.select(T["run_steps"])
                    .where(T["run_steps"].c.run_id == self.run, T["run_steps"].c.step_key == name)
                    .order_by(T["run_steps"].c.attempt_no.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if old:
                if old["input_sha256"] != fingerprint(payload):
                    raise ContractError("mineru_step_input_changed")
                if old["status"] == "succeeded":
                    return old["output_json"]
                if not readonly or name != "mineru_download" or old["attempt_no"] >= 3:
                    raise ProviderFailure("mineru_step_requires_reconciliation", unknown=True)
            step = conn.scalar(
                T["run_steps"]
                .insert()
                .values(
                    owner_id=self.owner,
                    project_id=self.record["project_id"],
                    run_id=self.run,
                    step_key=name,
                    attempt_no=old["attempt_no"] + 1 if old else 1,
                    tool_name="mineru_live_validation",
                    status="running",
                    input_sha256=fingerprint(payload),
                )
                .returning(T["run_steps"].c.id)
            )
        try:
            result = fn()
        except Exception as exc:
            known = isinstance(exc, ProviderFailure) and not exc.unknown
            with tenant_transaction(self.owner) as conn:
                conn.execute(
                    T["run_steps"]
                    .update()
                    .where(T["run_steps"].c.id == step)
                    .values(
                        status="failed" if known else "unknown",
                        output_json={
                            "exception_type": type(exc).__name__,
                            "error_code": exc.code if isinstance(exc, ProviderFailure) else "mineru_outcome_unknown",
                        },
                    )
                )
            raise
        with tenant_transaction(self.owner) as conn:
            conn.execute(
                T["run_steps"]
                .update()
                .where(T["run_steps"].c.id == step)
                .values(status="succeeded", output_json=result)
            )
        return result

    def execute(self, progress=None):
        if self.record["status"] == "succeeded":
            if self.record.get("error_json"):
                with tenant_transaction(self.owner) as conn:
                    conn.execute(
                        T["audit_events"]
                        .insert()
                        .values(
                            owner_id=self.owner,
                            project_id=self.record["project_id"],
                            action="mineru_validation_error_resolved",
                            subject_json={"run_id": str(self.run)},
                            safe_payload={
                                "previous_error": self.record["error_json"],
                                "resolution": "verified_download_and_inspection",
                            },
                        )
                    )
                    conn.execute(T["runs"].update().where(T["runs"].c.id == self.run).values(error_json=None))
            return self.record["result_json"]
        with tenant_transaction(self.owner) as lock:
            if not lock.scalar(
                sa.text("SELECT pg_try_advisory_xact_lock(hashtextextended(:id,0))"), {"id": str(self.run)}
            ):
                raise ContractError("mineru_validation_already_running")
            return self._execute(progress)

    def _execute(self, progress):
        result = {"validation_only": True, "source": self.scope, "publishable": False, "pdf_uploads": 0}

        def update(phase, **extra):
            result.update(phase=phase, **extra)
            with tenant_transaction(self.owner) as conn:
                conn.execute(
                    T["runs"].update().where(T["runs"].c.id == self.run).values(status="running", result_json=result)
                )
            if progress:
                progress(result)

        data_id = "scan_" + self.run.hex
        payload = {
            "model_version": "vlm",
            "enable_formula": True,
            "enable_table": True,
            "files": [{"name": self.scope["filename"], "data_id": data_id, "is_ocr": True}],
        }

        def prepare():
            data = self._json("POST", "https://mineru.net/api/v4/file-urls/batch", json=payload)
            if not isinstance(data.get("batch_id"), str) or len(data.get("file_urls", [])) != 1:
                raise ContractError("mineru_upload_admission_invalid")
            return {
                "batch_id": data["batch_id"],
                "upload_url": safe_url(data["file_urls"][0], SIGNED_UPLOAD_HOSTS),
                "data_id": data_id,
            }

        try:
            prepared = self._once("mineru_prepare", payload, prepare)
            update(
                "free_admission_confirmed",
                batch_id=prepared["batch_id"],
                admission={
                    "files_accepted": 1,
                    "pages_requested": 15,
                    "commercial_charging": False,
                    "remaining_priority_pages_not_reported": True,
                },
            )

            def upload():
                response = self.client.put(prepared["upload_url"], content=self.source.read_bytes())
                if response.status_code not in (200, 201, 204):
                    raise ProviderFailure(
                        "mineru_upload_http_error", response.status_code, unknown=response.status_code >= 500
                    )
                return {"uploaded": True}

            self._once("mineru_upload", {"sha256": self.scope["sha256"], "batch_id": prepared["batch_id"]}, upload)
            update("uploaded", pdf_uploads=1)
            row = None
            for index in range(self.poll_limit):
                data = self._json("GET", "https://mineru.net/api/v4/extract-results/batch/" + prepared["batch_id"])
                matches = [r for r in data.get("extract_result", []) if r.get("data_id") == data_id]
                if len(matches) != 1:
                    raise ContractError("mineru_poll_data_id_mismatch")
                row = matches[0]
                self.polls += 1
                if row["state"] not in {"waiting-file", "pending", "running", "converting", "done", "failed"}:
                    raise ContractError("mineru_state_unknown")
                if row["state"] != result.get("provider_state"):
                    update("polling", provider_state=row["state"], polls=self.polls)
                if row["state"] == "failed":
                    raise ProviderFailure("mineru_parse_failed", 200)
                if row["state"] == "done":
                    break
                if index + 1 < self.poll_limit:
                    time.sleep(self.poll_interval)
            if row is None or row["state"] != "done":
                with tenant_transaction(self.owner) as conn:
                    conn.execute(
                        T["runs"]
                        .update()
                        .where(T["runs"].c.id == self.run)
                        .values(status="waiting_user", result_json=result)
                    )
                return {**result, "waiting": True}
            url = safe_url(row["full_zip_url"], RESULT_HOSTS)
            target = PROJECT_ROOT / "var/mineru-validation" / self.run.hex / "result.zip"

            def download():
                raw = bytearray()
                from urllib.parse import urlsplit

                config = configuration()
                override = config.get("DOWNLOAD_DNS_OVERRIDES", {}).get(urlsplit(url).hostname)
                if override and self.injected_transport is None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_suffix(".part")
                    download_with_dns_override(
                        url,
                        tmp,
                        address=override,
                        proxy=config.get("LIVE_PROXY_URLS", {}).get("mineru_download"),
                        max_bytes=MAX_ARCHIVE_BYTES,
                    )
                    tmp.replace(target)
                    raw = target.read_bytes()
                    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "dns_override": True}
                with httpx.Client(
                    transport=self.injected_transport or network_transport("mineru_download"),
                    timeout=120,
                    follow_redirects=False,
                    trust_env=False,
                ) as download_client:
                    with download_client.stream("GET", url) as response:
                        if response.status_code != 200:
                            raise ProviderFailure("mineru_download_http_error", response.status_code)
                        for block in response.iter_bytes():
                            raw.extend(block)
                            if len(raw) > MAX_ARCHIVE_BYTES:
                                raise ContractError("mineru_archive_too_large")
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(".part")
                tmp.write_bytes(raw)
                tmp.replace(target)
                return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

            archive = self._once("mineru_download", {"batch_id": prepared["batch_id"]}, download, readonly=True)
            raw = target.read_bytes()
            if hashlib.sha256(raw).hexdigest() != archive["sha256"]:
                raise ContractError("mineru_download_hash_changed")
            inspected = inspect_scan_archive(raw, 15)
            text = inspected.pop("markdown")
            (target.parent / "full.md").write_text(text)
            passed = inspected["markdown_characters"] > 100 and all(
                inspected[k] for k in ("hardware_sample_found", "adam_09_found", "adam_098_found")
            )
            update(
                "inspected",
                artifact=archive,
                inspection=inspected,
                polls=self.polls,
                passed=passed,
                technical_completed=True,
            )
            if hashlib.sha256(self.source.read_bytes()).hexdigest() != self.scope["sha256"]:
                raise ContractError("original_scan_changed")
            with tenant_transaction(self.owner) as conn:
                conn.execute(
                    T["runs"]
                    .update()
                    .where(T["runs"].c.id == self.run)
                    .values(
                        status="succeeded" if passed else "partially_succeeded",
                        result_json=result,
                        error_json=None if passed else {"code": "mineru_sample_quality_check_failed"},
                        finished_at=sa.func.now(),
                    )
                )
            return result
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, ProviderFailure)
                else (str(exc) if isinstance(exc, ContractError) else "mineru_transport_or_schema_failure")
            )
            result.update(passed=False, error_code=code, exception_type=type(exc).__name__, polls=self.polls)
            with tenant_transaction(self.owner) as conn:
                conn.execute(
                    T["runs"]
                    .update()
                    .where(T["runs"].c.id == self.run)
                    .values(status="failed", result_json=result, error_json={"code": code}, finished_at=sa.func.now())
                )
            if progress:
                progress(result)
            return result
