"""Repeatable local product/export checks; consumes no model or upload authorization."""

from pathlib import Path
from uuid import UUID, uuid4
from io import BytesIO
import json
import time
import zipfile
import httpx
import sqlalchemy as sa
from dotenv import dotenv_values

# USER CONFIGURATION — credentials remain private; sample CSV is explicitly synthetic.
ROOT = Path(__file__).resolve().parent
API = "http://127.0.0.1:18080/api/v1/"
REFERENCE = ROOT / "var/reports/live-business-import.json"
CLOUD_REPORT = ROOT / "var/reports/cloud-business-acceptance.json"
REPORT = ROOT / "var/reports/studio-local-acceptance.json"
TIMEOUT_SECONDS = 120
CSV = b"model,precision,recall,f1,year\nControl,78,80,79,2024\nCandidate,85,83,84,2025\nVariant,88,87,87.5,2026\n"


def main():
    from scholarship.infrastructure.database import tenant_transaction

    ref = json.loads(REFERENCE.read_text())
    owner = UUID(ref["owner_id"])
    project = ref["project_id"]

    def count():
        with tenant_transaction(owner) as conn:
            return conn.scalar(sa.text("SELECT count(*) FROM model_runtime.calls WHERE NOT simulated"))

    before = count()
    report = {"new_model_requests": None, "steps": [], "passed": False}
    with httpx.Client(base_url=API, headers={"Origin": "http://127.0.0.1:15173"}, timeout=60) as client:
        deadline = time.monotonic() + 30
        while True:
            try:
                if client.get("health/ready").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("local_api_not_ready")
            time.sleep(0.5)

        def api(method, path, **kwargs):
            r = client.request(method, path, **kwargs)
            if r.status_code >= 400:
                raise RuntimeError("studio_http_" + str(r.status_code))
            return r.json()

        who = api(
            "POST",
            "auth/login",
            json={"username": "researcher", "password": dotenv_values(ROOT / ".env")["INITIAL_USER_PASSWORD"]},
        )
        if who["id"] != str(owner):
            raise RuntimeError("owner_mismatch")
        client.headers["X-CSRF-Token"] = client.cookies["ss_csrf"]

        def wait(job, label):
            deadline = time.monotonic() + TIMEOUT_SECONDS
            while True:
                row = api("GET", "runs/" + job["id"])
                if row["status"] not in {"queued", "running"}:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("local_task_timeout")
                time.sleep(0.5)
            if row["status"] != "succeeded":
                raise RuntimeError("local_task_failed_" + label)
            report["steps"].append({"label": label, "run_id": job["id"], "result": row["result_json"]})
            return row["result_json"]

        def task(op, body):
            return api(
                "POST",
                f"studio/projects/{project}/tasks",
                json={"operation": op, **body},
                headers={"Idempotency-Key": uuid4().hex},
            )

        try:
            status = api("GET", "system/external-services")
            if status["requested_external_flag"]:
                raise RuntimeError("model_gate_should_be_closed")
            diagram = wait(
                task("diagram", {"description": "Source → Parse → Retrieve → Answer\nRetrieve → Evidence"}),
                "text_to_diagram",
            )
            assert "N2 --> N4" in diagram["mermaid"]
            config = {
                "kind": "radar3d",
                "x": "model",
                "y": ["precision", "recall", "f1"],
                "z": "year",
                "units": {"year": "year", "precision": "percent", "recall": "percent", "f1": "percent"},
                "title": "Synthetic CSV verification",
                "missing": "reject",
            }
            chart = wait(
                api(
                    "POST",
                    f"studio/projects/{project}/charts",
                    files={"file": ("synthetic-verification.csv", CSV, "text/csv")},
                    data={"config": json.dumps(config)},
                    headers={"Idempotency-Key": uuid4().hex},
                ),
                "reproducible_3d_chart",
            )
            downloaded = client.get("studio/artifacts/" + chart["artifact_version_id"])
            downloaded.raise_for_status()
            with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
                assert archive.read("input.csv") == CSV
                (ROOT / "var/reports/studio-chart.png").write_bytes(archive.read("chart.png"))
                assert json.loads(archive.read("metadata.json"))["third_dimension"] == "year"
            live = json.loads(CLOUD_REPORT.read_text())
            for kind in ["summary", "draft"]:
                source = next(
                    s["result"]["artifact_version_id"]
                    for s in live["steps"]
                    if s["label"] == kind and s["status"] == "succeeded"
                )
                edited = wait(task("review_artifact", {"artifact_version_id": source}), "review_" + kind)
                assert edited["new_model_requests"] == 0 and edited["local_postprocessing_only"]
                bibliography = edited["bibliography"][0]
                assert (
                    bibliography["title"] == "Attention Is All You Need"
                    and len(bibliography["authors"]) == 8
                    and bibliography["year"] == 2017
                )
                assert bibliography["external_identity_verified"] is False
                if kind == "summary":
                    assert len(edited["sections"]) == 1 and len(edited["excluded_unrequested_section_titles"]) == 3
                blocks = [
                    span for window in edited["summary_spans"] for a in window["anchors"] for span in a["block_spans"]
                ]
                assert blocks
                located = api("GET", "studio/blocks/" + blocks[0]["block_id"] + "/locate")
                assert located["text"][blocks[0]["start"] : blocks[0]["end"]] == blocks[0]["quote"]
                raw = client.get("studio/artifacts/" + edited["artifact_version_id"])
                raw.raise_for_status()
                with zipfile.ZipFile(BytesIO(raw.content)) as archive:
                    assert b"2017" in archive.read("references.bib") and b"1706.03762v1" in archive.read(
                        "references.bib"
                    )
            report["passed"] = True
        finally:
            report["new_model_requests"] = count() - before
            report["passed"] = report["passed"] and report["new_model_requests"] == 0
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "steps": len(report["steps"]),
                "new_model_requests": report["new_model_requests"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
