"""PyCharm entry: reuse the approved local artifact, with zero cloud requests."""

from pathlib import Path
from uuid import UUID
import json
import subprocess
import sys
import sqlalchemy as sa

# USER CONFIGURATION: edit here, then Run. No API keys or network clients.
ROOT = Path(__file__).resolve().parent
ACTION = "status"  # status / import / acceptance / validate; all actions are local
REFERENCE = ROOT / "var/reports/mineru-scan-reference.json"
RESULT = ROOT / "var/reports/mineru-scan-cloud.json"
SOURCE_DIRECTORY = Path("/Users/cuijinglei/Documents/homework/比赛/科研小助手/pdf")
REPORT = ROOT / "var/reports/live-business-import.json"
TEST_TIMEOUT_SECONDS = 180


def import_approved_artifact():
    from scholarship.infrastructure.database import tenant_transaction
    from scholarship.infrastructure.schema import tables as T
    from scholarship.domains.deep_parse import persist_file
    from scholarship.domains.mineru_native import import_native
    from scholarship.domains.retrieval_profile import digest

    ref = json.loads(REFERENCE.read_text())
    report = json.loads(RESULT.read_text())
    owner, run = UUID(ref["owner_id"]), UUID(ref["run_id"])
    scope = report["source"]
    raw = (ROOT / "var/mineru-validation" / run.hex / "result.zip").read_bytes()
    if (
        scope["filename"] != "1706.03762v1_img.pdf"
        or not report.get("technical_completed")
        or digest(raw) != report["artifact"]["sha256"]
    ):
        raise ValueError("approved_completed_artifact_required")
    pdf = (SOURCE_DIRECTORY / scope["filename"]).read_bytes()
    if digest(pdf) != scope["sha256"]:
        raise ValueError("approved_source_changed")
    with tenant_transaction(owner) as conn:
        job = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == run)).mappings().one()
        # The durable upload journal, not the local report alone, owns authorization.
        if job["input_json"] != scope and job["input_json"].get("scope") != scope:
            raise ValueError("approved_job_scope_mismatch")
        if job["status"] != "succeeded" or job["kind"] != "mineru_validation":
            raise ValueError("approved_completed_job_required")
        project = job["project_id"]
        fid = persist_file(conn, owner, pdf, "pdf", "application/pdf")
        vid = conn.scalar(
            sa.select(T["paper_versions"].c.id)
            .where(
                T["paper_versions"].c.file_id == fid,
                T["paper_versions"].c.project_id == project,
                T["paper_versions"].c.deleted_at.is_(None),
            )
            .order_by(T["paper_versions"].c.created_at)
            .limit(1)
        )
        if not vid:
            title = "Attention Is All You Need · 扫描 OCR 载体"
            paper = conn.scalar(
                T["papers"]
                .insert()
                .values(
                    owner_id=owner,
                    project_id=project,
                    title=title,
                    normalized_title=title.casefold(),
                    display_metadata={"source": "approved-local-mineru-artifact"},
                )
                .returning(T["papers"].c.id)
            )
            vid = conn.scalar(
                T["paper_versions"]
                .insert()
                .values(
                    owner_id=owner,
                    project_id=project,
                    paper_id=paper,
                    file_id=fid,
                    metadata_json={
                        "page_count": scope["pages"],
                        "synthetic": False,
                        "approved_mineru_run_id": str(run),
                    },
                )
                .returning(T["paper_versions"].c.id)
            )
    pid = import_native(owner, vid, raw, expected_source_sha256=scope["sha256"])
    with tenant_transaction(owner) as conn:
        blocks = conn.scalar(
            sa.select(sa.func.count()).select_from(T["document_blocks"]).where(T["document_blocks"].c.parse_id == pid)
        )
    result = {
        "owner_id": str(owner),
        "project_id": str(project),
        "version_id": str(vid),
        "parse_id": str(pid),
        "pages": scope["pages"],
        "blocks": blocks,
        "source_synthetic": False,
        "external_requests": 0,
        "new_uploads": 0,
        "research_quality_verified": False,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def acceptance():
    from scholarship.domains.research import enqueue_research
    from scholarship.workers.offline import execute_offline
    from scholarship.infrastructure.database import tenant_transaction
    from scholarship.infrastructure.schema import tables as T
    from scholarship.domains.research import project_selection

    source = import_approved_artifact()
    owner, project, vid = (UUID(source[k]) for k in ("owner_id", "project_id", "version_id"))
    stamp = source["parse_id"] + ":" + project_selection(owner, project).snapshot()["fingerprint"][:16]

    def execute(kind, key, question=None):
        run = enqueue_research(owner, project, key, kind, [vid], question=question, mode="offline_acceptance")
        execute_offline(str(owner), str(project), str(run["id"]))
        with tenant_transaction(owner) as conn:
            saved = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == run["id"])).mappings().one()
        if saved["status"] != "succeeded":
            raise RuntimeError("local_business_acceptance_failed:" + str(saved["error_json"]))
        return {
            "run_id": str(run["id"]),
            "status": saved["status"],
            "citations": len((saved["result_json"] or {}).get("citations", [])),
            "contract_only": True,
            "scientific_correctness": "not_evaluated",
        }

    built = execute("research_build", "accept-build:" + stamp)
    cases = json.loads((ROOT / "docs/competition/qa-cases.json").read_text())["cases"]
    results = []
    for case in cases:
        if case["group"] in ("official_single", "refusal"):
            result = execute("research_qa", "accept:" + case["id"] + ":" + stamp, case["question"])
            results.append({"id": case["id"], **result, "multi_turn_tested": False})
        else:
            results.append({"id": case["id"], "status": "not_run", "reason": case["scientific_status"]})
    summary = {
        "source": source,
        "build": built,
        "cases": results,
        "external_requests": 0,
        "new_uploads": 0,
        "model_simulated": True,
        "full_competition_passed": False,
    }
    (ROOT / "var/reports/live-business-acceptance.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    return summary


def main():
    if ACTION == "validate":
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "backend/tests/test_live_business.py",
                f"--junitxml={ROOT / 'var/reports/live-business.xml'}",
            ],
            cwd=ROOT,
            timeout=TEST_TIMEOUT_SECONDS,
        )
        raise SystemExit(result.returncode)
    if ACTION == "acceptance":
        print(json.dumps(acceptance(), ensure_ascii=False, indent=2))
    elif ACTION == "import":
        print(json.dumps(import_approved_artifact(), ensure_ascii=False, indent=2))
    elif ACTION == "status":
        print(
            REPORT.read_text()
            if REPORT.exists()
            else "尚未发布本地 MinerU 产物。ACTION=import 可执行本地入库；不调用外部服务。"
        )
    else:
        raise ValueError("unknown_action")


if __name__ == "__main__":
    main()
