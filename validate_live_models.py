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
from scholarship.models.live import LiveExecutor, serialize
from scholarship.models.gateway import GrantedGateway, ReuseRetrievalGateway
from scholarship.models.approvals import issue_approval, history
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T

# USER CONFIGURATION: edit here, then Run. CLI options are optional overrides.
ROOT = Path(__file__).resolve().parent
ACTION = "status"  # status / issue / run
APPROVAL_CONFIRMED = False  # all twelve inference slots are consumed; completed results replay only
APPROVAL_KEY = "2026-11-14-scholarship-synthetic-five-v1"
CURRENT_APPROVAL_KEY = "2026-11-14-scholarship-official-paper-answer-repair-v1"
PRIOR_KEYS = [
    APPROVAL_KEY,
    "2026-11-14-scholarship-synthetic-six-continuation-v1",
    "2026-11-14-scholarship-six-proxy-retrieval-v1",
    "2026-11-14-scholarship-six-final-gemma-v1",
    "2026-11-14-scholarship-official-paper-retry-v1",
    "2026-11-14-scholarship-official-paper-chatanywhere-v1",
]
TOTAL_LIMIT = 12  # all twelve model slots are consumed; completed validation is read-only
WORKFLOW_KIND = "official_paper_qa"
REUSE_RETRIEVAL = True
REUSE_REFERENCE = ROOT / "var/reports/official-paper-reference.json"
PAPER_SOURCE = Path("/Users/cuijinglei/Documents/homework/比赛/科研小助手/pdf/1706.03762v1.pdf")
MODEL_PRESET = "free_api"
TIMEOUT_SECONDS = 120
MAX_OUTPUT_TOKENS = 256
REFERENCE = ROOT / "var/reports/official-paper-answer-repair-reference.json"
REPORT = ROOT / "var/reports/live-status.json"
PAPER_REPORT = ROOT / "var/reports/official-paper-cloud.json"

# Independent one-file authorization; free policy was read on the official site.
MINERU_APPROVED = False  # one authorized scan already uploaded and verified; no new issuance
MINERU_SOURCE = PAPER_SOURCE.with_name("1706.03762v1_img.pdf")
MINERU_APPROVAL_KEY = "2026-11-14-scholarship-one-public-scan-v1"
MINERU_REFERENCE = ROOT / "var/reports/mineru-scan-reference.json"
MINERU_REPORT = ROOT / "var/reports/mineru-scan-cloud.json"
MINERU_POLL_LIMIT = 40
MINERU_POLL_INTERVAL_SECONDS = 3
MINERU_FREE_POLICY = {
    "commercial_charging": False,
    "source_url": "https://mineru.net/apiManage/limit",
    "checked_on": "2026-11-14",
    "statement": "目前暂无商业化收费计划",
    "account_remaining_priority_pages": "not_reported",
}

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
        max_requests_per_run=(1 if REUSE_RETRIEVAL else 4)
        if WORKFLOW_KIND == "official_paper_qa"
        else len(PLANNED_REQUESTS),
    )


def request_plan():
    if WORKFLOW_KIND == "official_paper_qa":
        from scholarship.models.paper_validation import load_transformer_sample

        plan = load_transformer_sample(PAPER_SOURCE)
        if REUSE_RETRIEVAL:
            prior = json.loads(REUSE_REFERENCE.read_text())
            return [plan.requests()[-1]], {"source": plan.source, "reuse_grant_id": prior["grant_id"]}, plan
        return plan.requests(), plan.source, plan
    return PLANNED_REQUESTS, None, None


def mineru_action(action):
    from scholarship.models.mineru_validation import scan_scope, issue_scan_approval, MinerUScanValidation

    if action == "mineru-issue":
        if not MINERU_APPROVED:
            raise SystemExit("No scan upload approval.")
        previous = json.loads(REUSE_REFERENCE.read_text())
        runtime = RuntimeApprovalDatabase(previous["owner_id"])
        with runtime.connect() as conn:
            project = conn.scalar(
                sa.text("SELECT project_id FROM model_runtime.grants WHERE id=:id"), {"id": previous["grant_id"]}
            )
        scope = scan_scope(MINERU_SOURCE, MINERU_FREE_POLICY)
        from scholarship.infrastructure.initialize import admin_engine

        admin = admin_engine()
        try:
            row = issue_scan_approval(
                admin,
                key=MINERU_APPROVAL_KEY,
                scope=scope,
                owner_id=UUID(previous["owner_id"]),
                project_id=project,
                approved=True,
            )
        finally:
            admin.dispose()
        MINERU_REFERENCE.write_text(json.dumps({"owner_id": str(row["owner_id"]), "run_id": str(row["id"])}))
    if not MINERU_REFERENCE.exists():
        print('{"mineru_state":"not_issued"}')
        return
    ref = json.loads(MINERU_REFERENCE.read_text())
    if action == "mineru-run":
        executor = MinerUScanValidation(
            ref["owner_id"],
            ref["run_id"],
            MINERU_SOURCE,
            poll_limit=MINERU_POLL_LIMIT,
            poll_interval=MINERU_POLL_INTERVAL_SECONDS,
        )

        def progress(result):
            MINERU_REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(
                json.dumps(
                    {
                        "mineru_phase": result.get("phase"),
                        "provider_state": result.get("provider_state"),
                        "error_code": result.get("error_code"),
                    }
                ),
                flush=True,
            )

        try:
            executor.execute(progress)
        finally:
            executor.close()
    runtime = RuntimeApprovalDatabase(ref["owner_id"])
    with runtime.connect() as conn:
        row = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == UUID(ref["run_id"]))).mappings().one()
    print(
        json.dumps(
            {
                "mineru_state": row["status"],
                "pages_authorized": row["input_json"]["pages"],
                "passed": (row["result_json"] or {}).get("passed"),
                "error_code": (row["error_json"] or {}).get("code"),
            }
        )
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
    parser.add_argument(
        "--action", choices=["status", "issue", "run", "mineru-status", "mineru-issue", "mineru-run"], default=ACTION
    )
    action = parser.parse_args().action
    if action.startswith("mineru-"):
        mineru_action(action)
        return
    sel = planned_selection()
    if action == "issue":
        if not APPROVAL_CONFIRMED:
            raise SystemExit("No new inference approval: recorded history and consumed slots cannot be reset.")
        from scholarship.infrastructure.initialize import admin_engine

        requests, scope, _ = request_plan()

        admin = admin_engine()
        try:
            grant = issue_approval(
                admin,
                key=CURRENT_APPROVAL_KEY,
                selection=sel,
                requests=requests,
                total_limit=TOTAL_LIMIT,
                prior_keys=PRIOR_KEYS,
                approved=True,
                data_scope=scope,
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
        requests, scope, plan = request_plan()
        if grant["manifest_sha256"] != fingerprint(make_manifest(sel, requests, data_scope=scope)):
            raise RuntimeError("approved_manifest_changed")
        partial = {"source": scope, "steps": [], "passed": False}

        def progress(value):
            partial.update(serialize(value))
            PAPER_REPORT.write_text(json.dumps(partial, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"workflow_step": value["steps"][-1]["step"]}), flush=True)

        try:
            chosen_gateway = (
                ReuseRetrievalGateway(ref["owner_id"], grant["id"], sel, scope["reuse_grant_id"], plan.source)
                if REUSE_RETRIEVAL and plan is not None
                else GrantedGateway(ref["owner_id"], grant["id"], sel)
            )
            with chosen_gateway as gateway:
                if plan is None:
                    for request in requests:
                        gateway.invoke(*request)
                else:
                    from scholarship.models.paper_validation import run_paper_validation

                    partial = run_paper_validation(
                        gateway,
                        plan,
                        owner_id=grant["owner_id"],
                        project_id=grant["project_id"],
                        grant_id=grant["id"],
                        progress=progress,
                    )
            with tenant_transaction(ref["owner_id"]) as conn:
                conn.execute(sa.text("SELECT model_runtime.complete_grant(:id)"), {"id": grant["id"]})
                conn.execute(
                    T["runs"]
                    .update()
                    .where(T["runs"].c.id == grant["run_id"])
                    .values(status="succeeded", result_json=partial, finished_at=sa.func.now())
                )
        except Exception as exc:
            code = getattr(exc, "code", "paper_workflow_validation_failed")
            LiveExecutor(ref["owner_id"], grant["id"], sel).stop(code)
            partial.update(passed=False, error_code=code)
            PAPER_REPORT.write_text(json.dumps(partial, ensure_ascii=False, indent=2) + "\n")
            with tenant_transaction(ref["owner_id"]) as conn:
                conn.execute(
                    T["runs"]
                    .update()
                    .where(T["runs"].c.id == grant["run_id"])
                    .values(status="failed", result_json=partial, error_json={"code": code}, finished_at=sa.func.now())
                )
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
        "scope": "model_inference_approval_chain_only",
        "parser_validation_report": "var/reports/mineru-scan-cloud.json",
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
