"""Repeatable LOCAL-only competition corpus import; originals are read-only."""

from pathlib import Path
from uuid import UUID
import hashlib
import json
import re
import time
import httpx
import sqlalchemy as sa
from pypdf import PdfReader
from dotenv import dotenv_values

# USER CONFIGURATION — no cloud models, no remote upload or source modification.
ROOT = Path(__file__).resolve().parent
SOURCE_DIRECTORY = Path("/Users/cuijinglei/Documents/homework/比赛/科研小助手/pdf")
ACTION = "status"  # status / prepare / repair (only previously failed local text parses)
PROJECT_NAME = "比赛本地语料验收"
MAX_PAPERS = 20
MAX_PAGES = 80  # textbook and scan acceptance remain separate
API = "http://127.0.0.1:18080/api/v1/"
REPORT = ROOT / "var/reports/competition-local-corpus.json"
TASK_TIMEOUT_SECONDS = 1800


def main():
    if ACTION == "status":
        print(REPORT.read_text() if REPORT.exists() else '{"prepared":false}')
        return
    if ACTION not in {"prepare", "repair"}:
        raise ValueError("unknown_action")
    from scholarship.infrastructure.database import tenant_transaction
    from scholarship.infrastructure.schema import tables as T

    report = {
        "originals_read_only": True,
        "cloud_requests": 0,
        "project_name": PROJECT_NAME,
        "papers": [],
        "skipped": [],
        "passed": False,
    }
    paths = sorted(SOURCE_DIRECTORY.glob("*.pdf"))
    if ACTION == "repair":
        report = json.loads(REPORT.read_text())
        REPORT.with_name(REPORT.stem + "-before-repair-" + str(time.time_ns()) + ".json").write_text(REPORT.read_text())
        names = {
            x["file"]
            for x in report["skipped"]
            if isinstance(x["reason"], dict) and x["reason"].get("type") == "DataError"
        }
        paths = [p for p in paths if p.name in names]
    with httpx.Client(base_url=API, headers={"Origin": "http://127.0.0.1:15173"}, timeout=120) as client:
        # Local readiness reads may wait for startup; model POSTs are never retried.
        ready_until = time.monotonic() + 30
        while True:
            try:
                ready = client.get("health/ready")
                if ready.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= ready_until:
                raise RuntimeError("local_api_not_ready")
            time.sleep(0.5)

        def api(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code >= 400:
                raise RuntimeError("local_corpus_http_" + str(response.status_code))
            return response.json()

        who = api(
            "POST",
            "auth/login",
            json={"username": "researcher", "password": dotenv_values(ROOT / ".env")["INITIAL_USER_PASSWORD"]},
        )
        client.headers["X-CSRF-Token"] = client.cookies["ss_csrf"]
        owner = UUID(who["id"])
        with tenant_transaction(owner) as c:
            c.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"), {"k": str(owner) + PROJECT_NAME})
            project = c.scalar(
                sa.select(T["projects"].c.id).where(
                    T["projects"].c.name == PROJECT_NAME, T["projects"].c.deleted_at.is_(None)
                )
            )
            if not project:
                project = c.scalar(
                    T["projects"]
                    .insert()
                    .values(
                        owner_id=owner,
                        name=PROJECT_NAME,
                        description="本地比赛全文结构验收，未授权云端调用",
                        preferences={"model_configuration": {"preset": "mock", "overrides": {}}},
                    )
                    .returning(T["projects"].c.id)
                )
        report["project_id"] = str(project)
        works = set()
        try:
            for path in paths:
                if ACTION == "prepare" and len(report["papers"]) >= MAX_PAPERS:
                    break
                work = re.sub(r"v\d+$", "", path.stem)
                if work in works or "_img" in path.stem:
                    continue
                reader = PdfReader(path)
                if len(reader.pages) > MAX_PAGES or not (reader.pages[0].extract_text() or "").strip():
                    report["skipped"].append({"file": path.name, "reason": "scan_or_page_scope"})
                    continue
                with path.open("rb") as f:
                    uploaded = api(
                        "POST",
                        f"projects/{project}/uploads",
                        files={"file": (path.name, f, "application/pdf")},
                        headers={"Idempotency-Key": "local-corpus-" + hashlib.sha256(path.read_bytes()).hexdigest()},
                    )
                if uploaded.get("review_id"):
                    report["skipped"].append({"file": path.name, "reason": "duplicate_review_required"})
                    continue
                vid = uploaded["version_id"]
                task = api(
                    "POST",
                    f"studio/projects/{project}/tasks",
                    json={"operation": "local_parse", "version_id": vid},
                    headers={"Idempotency-Key": "local-corpus-structure-v3-" + vid},
                )
                deadline = time.monotonic() + TASK_TIMEOUT_SECONDS
                while True:
                    result = api("GET", "runs/" + task["id"])
                    if result["status"] not in {"queued", "running"}:
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError("local_corpus_task_timeout")
                    time.sleep(1)
                if result["status"] != "succeeded":
                    report["skipped"].append({"file": path.name, "reason": result["error_json"]})
                    continue
                with tenant_transaction(owner) as c:
                    pid = c.scalar(
                        sa.select(T["paper_versions"].c.paper_id).where(T["paper_versions"].c.id == UUID(vid))
                    )
                report["papers"].append(
                    {
                        "file": path.name,
                        "version_id": vid,
                        "paper_id": str(pid),
                        "parse_id": result["result_json"]["parse_id"],
                        "pages": len(reader.pages),
                        "quality_warning": result["result_json"].get("quality_warning"),
                        "nul_glyph_replacements": result["result_json"].get("nul_glyph_replacements", 0),
                    }
                )
                report["skipped"] = [x for x in report["skipped"] if x["file"] != path.name]
                works.add(work)
                REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            graph = api(
                "POST",
                f"studio/projects/{project}/graph",
                json={"paper_ids": [p["paper_id"] for p in report["papers"]]},
            )
            report["graph"] = graph
            report["passed"] = len(graph["nodes"]) >= 20 and graph["papers_with_active_parse"] >= 20
        finally:
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "papers": len(report["papers"]),
                "edges": len(report.get("graph", {}).get("edges", [])),
                "cloud_requests": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
