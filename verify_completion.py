"""Local user journey in a dedicated workspace; never changes approved real indexes."""

from pathlib import Path
from uuid import uuid4, UUID
import json
import time
import httpx
from dotenv import dotenv_values
import sqlalchemy as sa
from scholarship.infrastructure.database import tenant_transaction

# USER CONFIGURATION — originals are opened read-only; all inference stays simulated.
ROOT = Path(__file__).resolve().parent
API = "http://127.0.0.1:18080/api/v1/"
ORIGIN = "http://127.0.0.1:15173"
MATERIALS = Path("/Users/cuijinglei/Documents/homework/比赛/科研小助手/pdf")
FILES = ["1706.03762v7.pdf", "1706.03762v1.pdf"]
REPORT = ROOT / "var/reports/feature-completion.json"
TASK_TIMEOUT = 180
WORKSPACE_NAME = "功能闭环本地验收"


def main():
    report = {"passed": False, "steps": [], "model_mode": "offline_acceptance"}
    with httpx.Client(base_url=API, headers={"Origin": ORIGIN}, timeout=60) as client:

        def request(method, path, **kwargs):
            r = client.request(method, path, **kwargs)
            if r.status_code >= 400:
                raise RuntimeError(f"journey_http_{r.status_code}_{path.split('/')[0]}")
            return r.json()

        user = request(
            "POST",
            "auth/login",
            json={"username": "researcher", "password": dotenv_values(ROOT / ".env")["INITIAL_USER_PASSWORD"]},
        )
        client.headers["X-CSRF-Token"] = client.cookies["ss_csrf"]
        owner = UUID(user["id"])

        def real_count():
            with tenant_transaction(owner) as conn:
                return conn.scalar(sa.text("SELECT count(*) FROM model_runtime.calls WHERE NOT simulated"))

        before = real_count()
        try:
            spaces = request("GET", "projects")
            project = next((p["id"] for p in spaces if p["name"] == WORKSPACE_NAME), None)
            if not project:
                project = request(
                    "POST",
                    "projects",
                    json={"name": WORKSPACE_NAME, "description": "独立空间；真实 PDF + 模拟模型流程验收"},
                )["id"]
            report["project_id"] = project
            report["steps"].append("workspace")

            def wait(rid):
                deadline = time.monotonic() + TASK_TIMEOUT
                while time.monotonic() < deadline:
                    row = request("GET", "runs/" + rid)
                    if row["status"] not in ["queued", "running"]:
                        if row["status"] not in ["succeeded", "partially_succeeded"]:
                            raise RuntimeError("journey_task_" + str(row.get("error_json")))
                        return row.get("result_json")
                    time.sleep(0.5)
                raise RuntimeError("journey_task_timeout")

            def task(op, **args):
                job = request(
                    "POST",
                    f"studio/projects/{project}/tasks",
                    json={"operation": op, **args},
                    headers={"Idempotency-Key": uuid4().hex},
                )
                return wait(job["id"])

            versions = []
            parses = []
            for name in FILES:
                with (MATERIALS / name).open("rb") as f:
                    row = request(
                        "POST",
                        f"projects/{project}/uploads",
                        files={"file": ("renamed-" + str(len(versions)) + ".pdf", f, "application/pdf")},
                        headers={"Idempotency-Key": uuid4().hex},
                    )
                versions.append(row["version_id"])
                if row.get("run_id"):
                    wait(row["run_id"])
                result = task("local_parse", version_id=row["version_id"])
                parses.append(result["parse_id"])
            report["steps"].append("renamed_real_pdfs_preview_and_structure")
            candidates = request("GET", f"projects/{project}/content-candidates")
            assert candidates["candidates"], "missing_content_candidate"
            report["steps"].append("content_identity_candidates")
            comparison = request("GET", f"paper-versions/{versions[0]}/compare/{versions[1]}")
            assert comparison["changes"], "missing_version_changes"
            report["steps"].append("version_comparison")
            for version_id in versions:
                job = request(
                    "POST",
                    f"research/projects/{project}/indexes",
                    json={"version_ids": [version_id], "mode": "offline_acceptance"},
                    headers={"Idempotency-Key": uuid4().hex},
                )
                wait(job["id"])
            job = request(
                "POST",
                f"research/projects/{project}/questions",
                json={
                    "version_ids": versions,
                    "mode": "offline_acceptance",
                    "question": "How does the attention mechanism work?",
                },
                headers={"Idempotency-Key": uuid4().hex},
            )
            answer = wait(job["id"])
            assert answer["model_simulated"]
            report["steps"].append("two_source_simulated_qa")
            blocks = request("GET", f"parses/{parses[0]}/pages/1")["blocks"]
            b = next(b for b in blocks if len(b["canonical_text"]) > 30)
            span = {"block_id": b["id"], "start": 0, "end": 20, "text": b["canonical_text"][:20]}
            translation = task("translate", parse_id=parses[0], source_selection=span, language="en")
            tid = translation["translation_id"]
            part = request("GET", f"studio/translations/{tid}/review")[0]
            revision = request(
                "POST",
                f"studio/translations/{tid}/revisions",
                json={
                    "segment_id": part["id"],
                    "expected_text_sha256": part["text_sha256"],
                    "text": part["translated_text"] + " Reviewed.",
                    "reason": "本地流程核对",
                },
            )
            assert revision["translation_id"] != tid
            assert any(
                t["id"] == revision["translation_id"] for t in request("GET", f"studio/parses/{parses[0]}/translations")
            )
            report["steps"].append("selection_translation_and_immutable_human_revision")
            papers = request("GET", f"projects/{project}/papers")
            paper = papers[0]
            request("PATCH", f"papers/{paper['id']}", json={"archived": True})
            assert request("GET", f"projects/{project}/archived-papers")
            request("PATCH", f"papers/{paper['id']}", json={"archived": False})
            report["steps"].append("archive_restore")
            report["passed"] = True
        finally:
            report["new_cloud_calls"] = real_count() - before
            report["passed"] = report["passed"] and report["new_cloud_calls"] == 0
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
