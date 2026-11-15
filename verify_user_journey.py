"""Repeatable local API user journey; cloud inference/upload is never enabled."""

from pathlib import Path
from uuid import uuid4
import json
import re
import time
import httpx
import sqlalchemy as sa

# USER CONFIGURATION — edit here and Run in PyCharm.
ROOT = Path(__file__).resolve().parent
API = "http://127.0.0.1:18080/api/v1"
USERNAME = "researcher"
PASSWORD_FIELD = "INITIAL_USER_PASSWORD"
SOURCE = Path("/Users/cuijinglei/Documents/homework/比赛/科研小助手/pdf/1706.03762v1_img.pdf")
IMPORT_REFERENCE = ROOT / "var/reports/live-business-import.json"
REPORT = ROOT / "var/reports/user-journey-api.json"
QUESTION = "User journey: How many NVIDIA P100 GPUs were used for training?"
TASK_TIMEOUT_SECONDS = 45
POLL_SECONDS = 0.25


def main():
    from scholarship.infrastructure.database import tenant_transaction

    checks = []

    def check(name, condition):
        if not condition:
            raise RuntimeError("user_journey_failed:" + name)
        checks.append(name)

    match = re.search(r"^" + re.escape(PASSWORD_FIELD) + r"=(.*)$", (ROOT / ".env").read_text(), re.MULTILINE)
    if not match:
        raise RuntimeError("local_login_credential_missing")
    password = match[1].strip().strip("\"'")
    reference = json.loads(IMPORT_REFERENCE.read_text())
    with httpx.Client(
        base_url=API + "/", timeout=20, headers={"Origin": "http://127.0.0.1:15173"}, follow_redirects=False
    ) as client:

        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code >= 400:
                raise RuntimeError("local_api_failed:" + str(response.status_code))
            return response.json()

        check("services_ready", request("GET", "health/ready")["ready"])
        who = request("POST", "auth/login", json={"username": USERNAME, "password": password})
        password = ""
        from uuid import UUID

        owner = UUID(who["id"])
        project = reference["project_id"]
        check("project_owned", any(p["id"] == project for p in request("GET", "projects")))
        client.headers["X-CSRF-Token"] = client.cookies["ss_csrf"]

        def real_calls():
            with tenant_transaction(owner) as conn:
                return conn.scalar(sa.text("SELECT count(*) FROM model_runtime.calls WHERE NOT simulated"))

        baseline = real_calls()
        papers = request("GET", f"projects/{project}/papers")
        duplicate = request(
            "POST",
            f"projects/{project}/uploads",
            files={"file": (SOURCE.name, SOURCE.read_bytes(), "application/pdf")},
            headers={"Idempotency-Key": uuid4().hex},
        )
        check(
            "duplicate_pdf_reuses_version", duplicate["reused"] and duplicate["version_id"] == reference["version_id"]
        )
        check("no_duplicate_paper", len(request("GET", f"projects/{project}/papers")) == len(papers))

        def task(kind, body):
            key = uuid4().hex
            headers = {"Idempotency-Key": key}
            run = request(
                "POST",
                f"research/projects/{project}/{kind}",
                json={**body, "mode": "offline_acceptance"},
                headers=headers,
            )
            check(
                kind + "_idempotency",
                request(
                    "POST",
                    f"research/projects/{project}/{kind}",
                    json={**body, "mode": "offline_acceptance"},
                    headers=headers,
                )["id"]
                == run["id"],
            )
            deadline = time.monotonic() + TASK_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                state = request("GET", "runs/" + run["id"])
                if state["status"] not in {"queued", "running"}:
                    check(kind + "_worker_completed", state["status"] == "succeeded")
                    return state
                time.sleep(POLL_SECONDS)
            raise RuntimeError("local_task_timeout")

        body = {"version_ids": [reference["version_id"]]}
        task("indexes", body)
        docs = request("GET", f"research/projects/{project}/documents")
        check(
            "matching_simulated_index_ready",
            next(d for d in docs if d["id"] == reference["version_id"])["can_query_offline"],
        )
        qa = task("questions", {**body, "question": QUESTION})
        result = qa["result_json"]
        check(
            "truth_labels",
            result["model_simulated"] and not result["source_synthetic"] and result["external_requests"] == 0,
        )
        check("citations_available", bool(result["citations"]))
        for c in result["citations"]:
            e = request("GET", "research/evidence/" + c["id"])
            check("quote_matches_original_block", e["text"][e["start"] : e["end"]] == e["quote"])
            check(
                "pdf_coordinate_and_source",
                bool(e["pdf_bbox"]) and e["version_id"] == reference["version_id"] and e["original_pdf_available"],
            )
        check(
            "original_pdf_download",
            client.get("paper-versions/" + reference["version_id"] + "/file").content.startswith(b"%PDF"),
        )
        check(
            "history_restores_task",
            any(r["id"] == qa["id"] for r in request("GET", f"research/projects/{project}/runs")),
        )
        rejected = client.post(
            f"research/projects/{project}/questions",
            json={**body, "question": QUESTION, "mode": "cloud"},
            headers={"Idempotency-Key": uuid4().hex},
        )
        check("cloud_request_denied_before_inference", rejected.status_code == 409)
        check("no_new_cloud_calls", real_calls() == baseline)
        request("POST", "auth/logout")
        check("logout_revokes_session", client.get("projects").status_code == 401)
    summary = {
        "passed": True,
        "checks": checks,
        "cloud_call_count_unchanged": True,
        "new_cloud_uploads": 0,
        "model_simulated": True,
        "scientific_quality_verified": False,
        "qa_run_id": qa["id"],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps({"passed": True, "checks": len(checks), "report": str(REPORT.relative_to(ROOT))}, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
