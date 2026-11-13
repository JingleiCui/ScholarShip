"""One approved five-request synthetic validation; reruns never reset its grant.

Default status is read-only. Current approval is stopped after a 403 preflight.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid5, NAMESPACE_URL
import argparse
import json

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from scholarship.adapters.providers import Evidence
from scholarship.models.configuration import load_selection, fingerprint
from scholarship.models.live import make_manifest, LiveExecutor
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T

# USER CONFIGURATION: this key identifies the user's single approval, not a retry.
ROOT = Path(__file__).resolve().parent
ACTION = "status"  # status / record-stop / run
APPROVAL_KEY = "2026-11-14-scholarship-synthetic-five-v1"
REPORT = ROOT / "var/reports/live-validation.json"
PREFLIGHT = ROOT / "var/reports/live-preflight-shape.json"
REFERENCE = ROOT / "var/reports/live-approval-reference.json"
REQUESTS = [
    (
        "chat_plain",
        "chat",
        "generate",
        {
            "messages": [
                {"role": "system", "content": "Reply exactly SCHOLARSHIP_OK."},
                {"role": "user", "content": "This is a synthetic connectivity test."},
            ]
        },
    ),
    (
        "embed_documents",
        "embedding",
        "embed",
        {
            "texts": ["Synthetic Alpha accuracy is 91 percent.", "Synthetic Beta accuracy is 87 percent."],
            "input_kind": "document",
        },
    ),
    (
        "embed_query",
        "embedding",
        "embed",
        {"texts": ["Which synthetic method has 91 percent accuracy?"], "input_kind": "query"},
    ),
    (
        "rerank",
        "rerank",
        "rank",
        {
            "query": "Which method has 91 percent accuracy?",
            "documents": ["Synthetic Alpha accuracy is 91 percent.", "Synthetic Beta accuracy is 87 percent."],
            "top_n": 2,
        },
    ),
    (
        "chat_evidence",
        "chat",
        "answer",
        {
            "question": "What accuracy is stated for Alpha?",
            "evidence": [Evidence("synthetic-alpha", "Synthetic Alpha accuracy is 91 percent.", 1)],
        },
    ),
]


def selection():
    return replace(load_selection("free_api"), timeout_seconds=30, max_output_tokens=256, max_requests_per_run=5)


class RuntimeApprovalDatabase:
    """Data-plane reads use the runtime role; no admin credentials are loaded."""

    def __init__(self, owner):
        self.owner = UUID(str(owner))

    def connect(self):
        return tenant_transaction(self.owner)

    def dispose(self):
        pass


def find_approval(db):
    with db.connect() as conn:
        return (
            conn.execute(sa.text("SELECT * FROM model_runtime.grants WHERE approval_key=:key"), {"key": APPROVAL_KEY})
            .mappings()
            .first()
        )


def record_stopped_approval(db, sel, manifest):
    preflight = json.loads(PREFLIGHT.read_text())
    if (
        preflight.get("provider") != "chatanywhere"
        or preflight.get("http_status") != 403
        or preflight.get("inference_requests") != 0
    ):
        raise RuntimeError("this_record_action_only_accepts_observed_403_preflight")
    with db.begin() as conn:
        actor = conn.execute(
            sa.select(T["users"].c.id).where(T["users"].c.username == "researcher", T["users"].c.disabled_at.is_(None))
        ).scalar_one()
        project = conn.scalar(
            sa.select(T["projects"].c.id)
            .where(T["projects"].c.owner_id == actor)
            .order_by(T["projects"].c.created_at)
            .limit(1)
        )
        run_id = uuid5(NAMESPACE_URL, "scholarship:" + APPROVAL_KEY)
        conn.execute(
            insert(T["runs"])
            .values(
                id=run_id,
                owner_id=actor,
                project_id=project,
                kind="provider_validation",
                status="failed",
                idempotency_key=APPROVAL_KEY,
                request_sha256=fingerprint(manifest),
                input_json={"synthetic": True, "maximum_requests": 5, "pdf_uploads": False},
                config_json={"models": sel.snapshot()},
                input_scope_json={},
                next_event_seq=1,
                event_floor=1,
                error_json={"code": "chatanywhere_preflight_403"},
                finished_at=sa.func.now(),
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        existing = conn.execute(
            sa.text("SELECT manifest_sha256 FROM model_runtime.grants WHERE approval_key=:key"), {"key": APPROVAL_KEY}
        ).scalar()
        if existing:
            if existing != fingerprint(manifest):
                raise RuntimeError("approved_manifest_changed")
            conn.execute(
                sa.text(
                    "UPDATE model_runtime.grants SET state='stopped',stop_code='chatanywhere_preflight_403',updated_at=now() WHERE approval_key=:key AND state='active'"
                ),
                {"key": APPROVAL_KEY},
            )
            return
        from psycopg.types.json import Jsonb

        conn.connection.driver_connection.execute(
            """INSERT INTO model_runtime.grants
            (approval_key,owner_id,project_id,run_id,manifest_json,manifest_sha256,max_requests,max_output_tokens,state,expires_at,stop_code,preflight_json)
            VALUES(%s,%s,%s,%s,%s,%s,5,256,'stopped',%s,'chatanywhere_preflight_403',%s)""",
            (
                APPROVAL_KEY,
                actor,
                project,
                run_id,
                Jsonb(manifest),
                fingerprint(manifest),
                datetime.now(timezone.utc) + timedelta(days=1),
                Jsonb(preflight),
            ),
        )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--action", choices=["status", "record-stop", "run"], default=ACTION)
    action = p.parse_args().action
    sel = selection()
    manifest = make_manifest(sel, REQUESTS)
    if action == "record-stop":
        # Explicit, user-authorized approval maintenance only; never used by run/status.
        from scholarship.infrastructure.initialize import admin_engine

        maintenance = admin_engine()
        try:
            record_stopped_approval(maintenance, sel, manifest)
            issued = find_approval(maintenance)
            REFERENCE.parent.mkdir(parents=True, exist_ok=True)
            REFERENCE.write_text(json.dumps({"owner_id": str(issued["owner_id"]), "grant_id": str(issued["id"])}))
        finally:
            maintenance.dispose()
    if not REFERENCE.exists():
        print(json.dumps({"approval_state": "reference_not_present", "inference_requests": 0}))
        return
    reference = json.loads(REFERENCE.read_text())
    db = RuntimeApprovalDatabase(reference["owner_id"])
    try:
        grant = find_approval(db)
        if grant and str(grant["id"]) != reference["grant_id"]:
            raise RuntimeError("grant_reference_mismatch")
        if not grant:
            print(json.dumps({"approval_state": "not_created", "inference_requests": 0}))
            return
        if grant["manifest_sha256"] != fingerprint(manifest):
            raise RuntimeError("approved_manifest_changed")
        if action == "run" and grant["state"] == "active":
            executor = LiveExecutor(grant["owner_id"], grant["id"], sel)
            document_dimensions = None
            for intent, role, operation, arguments in REQUESTS:
                # Native dimensions from the first response must match the query.
                try:
                    receipt = executor.execute(intent, role, operation, arguments)
                    if intent == "embed_documents":
                        document_dimensions = receipt["dimensions"]
                    if intent == "embed_query" and receipt["dimensions"] != document_dimensions:
                        executor.stop("embedding_dimension_changed")
                        break
                except Exception:
                    break  # no retry, fallback, or remaining requests
            with tenant_transaction(grant["owner_id"]) as conn:
                row = (
                    conn.execute(
                        sa.text("SELECT state,used_requests FROM model_runtime.grants WHERE id=:id"),
                        {"id": grant["id"]},
                    )
                    .mappings()
                    .one()
                )
                if row["state"] == "active" and row["used_requests"] == 5:
                    conn.execute(sa.text("SELECT model_runtime.complete_grant(:id)"), {"id": grant["id"]})
            grant = find_approval(db)
        with db.connect() as conn:
            records = (
                conn.execute(
                    sa.text(
                        "SELECT intent,state,provider,model,receipt_json FROM model_runtime.calls WHERE grant_id=:id ORDER BY created_at"
                    ),
                    {"id": grant["id"]},
                )
                .mappings()
                .all()
            )
        safe = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "approval_key": APPROVAL_KEY,
            "state": grant["state"],
            "max_inference_requests": 5,
            "used_request_slots": grant["used_requests"],
            "stop_code": grant["stop_code"],
            "preflight": grant["preflight_json"],
            "read_only_account_queries": 1,
            "preflight_endpoint": "https://chatanywhere.tech/api/anonymous/query/balance",
            "preflight_method": "POST",
            "pdf_uploads": 0,
            "mineru_requests": 0,
            "recharges": 0,
            "calls": [dict(r) for r in records],
            "global_external_services_unchanged": True,
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(safe, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({k: v for k, v in safe.items() if k not in {"calls"}}, ensure_ascii=False))
    finally:
        db.dispose()


if __name__ == "__main__":
    main()
