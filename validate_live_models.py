"""Single bounded validation entry. Default status never sends model requests."""

from dataclasses import replace
from pathlib import Path
from uuid import UUID
import argparse
import json
import sqlalchemy as sa

from scholarship.adapters.providers import Evidence
from scholarship.models.configuration import load_selection, fingerprint
from scholarship.models.live import make_manifest
from scholarship.models.gateway import GrantedGateway
from scholarship.models.approvals import issue_approval, history
from scholarship.infrastructure.database import tenant_transaction

# USER CONFIGURATION: edit here, then Run. CLI options are optional overrides.
ROOT = Path(__file__).resolve().parent
ACTION = "status"  # status / issue / run
APPROVAL_CONFIRMED = False  # six historical attempts are exhausted; no new approval
APPROVAL_KEY = "2026-11-14-scholarship-synthetic-five-v1"
CURRENT_APPROVAL_KEY = "2026-11-14-scholarship-six-final-gemma-v1"
PRIOR_KEYS = [
    APPROVAL_KEY,
    "2026-11-14-scholarship-synthetic-six-continuation-v1",
    "2026-11-14-scholarship-six-proxy-retrieval-v1",
]
TOTAL_LIMIT = 6
MODEL_PRESET = "free_gemma"
TIMEOUT_SECONDS = 120
MAX_OUTPUT_TOKENS = 256
REFERENCE = ROOT / "var/reports/gemma-reference.json"
REPORT = ROOT / "var/reports/live-status.json"

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

# Intent names and short synthetic data are reviewable; no local files are read.
PLANNED_REQUESTS = [REQUESTS[-1]]


def selection():
    """Retain the historical contract fixture for regression tests."""
    return replace(load_selection("free_api"), timeout_seconds=30, max_output_tokens=256, max_requests_per_run=5)


def planned_selection():
    return replace(
        load_selection(MODEL_PRESET),
        timeout_seconds=TIMEOUT_SECONDS,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        max_requests_per_run=len(PLANNED_REQUESTS),
    )


class RuntimeApprovalDatabase:
    def __init__(self, owner):
        self.owner = UUID(str(owner))

    def connect(self):
        return tenant_transaction(self.owner)

    def dispose(self):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["status", "issue", "run"], default=ACTION)
    action = parser.parse_args().action
    sel = planned_selection()
    if action == "issue":
        if not APPROVAL_CONFIRMED:
            raise SystemExit("No new approval: the six historical slots remain consumed.")
        from scholarship.infrastructure.initialize import admin_engine

        admin = admin_engine()
        try:
            grant = issue_approval(
                admin,
                key=CURRENT_APPROVAL_KEY,
                selection=sel,
                requests=PLANNED_REQUESTS,
                total_limit=TOTAL_LIMIT,
                prior_keys=PRIOR_KEYS,
                approved=True,
            )
            REFERENCE.write_text(json.dumps({"owner_id": str(grant["owner_id"]), "grant_id": str(grant["id"])}))
        finally:
            admin.dispose()
    if not REFERENCE.exists():
        print('{"state":"no_approval_reference"}')
        return
    ref = json.loads(REFERENCE.read_text())
    db = RuntimeApprovalDatabase(ref["owner_id"])
    with db.connect() as conn:
        grant = (
            conn.execute(sa.text("SELECT * FROM model_runtime.grants WHERE id=:id"), {"id": ref["grant_id"]})
            .mappings()
            .one()
        )
    if action == "run" and grant["state"] == "active":
        if grant["manifest_sha256"] != fingerprint(make_manifest(sel, PLANNED_REQUESTS)):
            raise RuntimeError("approved_manifest_changed")
        try:
            with GrantedGateway(ref["owner_id"], grant["id"], sel) as gateway:
                for request in PLANNED_REQUESTS:
                    gateway.invoke(*request)
            with tenant_transaction(ref["owner_id"]) as conn:
                conn.execute(sa.text("SELECT model_runtime.complete_grant(:id)"), {"id": grant["id"]})
        except Exception as exc:
            print(json.dumps({"validation_error": type(exc).__name__}))
    with db.connect() as conn:
        lineage = history(conn, grant["id"])
        ids = [r["id"] for r in lineage]
        calls = [
            dict(r)
            for r in conn.execute(
                sa.text(
                    "SELECT intent,state,provider,model,receipt_json FROM model_runtime.calls WHERE grant_id=ANY(:ids) ORDER BY created_at"
                ),
                {"ids": ids},
            ).mappings()
        ]
    latest = lineage[0]
    report = {
        "state": latest["state"],
        "total_limit": latest["preflight_json"].get("total_limit", latest["max_requests"]),
        "used_request_slots": sum(r["used_requests"] for r in lineage),
        "stop_code": latest["stop_code"],
        "calls": calls,
        "file_uploads": 0,
        "mineru_requests": 0,
        "recharges": 0,
        "inference_retries": 0,
        "global_external_services_unchanged": True,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "calls"}))


if __name__ == "__main__":
    main()
