"""Real local user journey with PostgreSQL receipts; never sends a cloud request.

A dedicated workspace protects existing approved cloud indexes. Synthetic PDF
facts test citation correctness; the approved scan reuses its existing OCR zip.
"""

from pathlib import Path
from uuid import UUID, uuid4
import json
import os
import shutil
import time
import hashlib
import httpx
from dotenv import dotenv_values
import sqlalchemy as sa
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parent
API = "http://127.0.0.1:18080/api/v1/"
REPORT = ROOT / "var/reports/local-journey.json"
TIMEOUT_SECONDS = 1200
SOURCE_DIRECTORY = Path("/Users/cuijinglei/Documents/homework/比赛/科研小助手/pdf")
WORKSPACE_NAME = "本地模型真实链路验收"
USE_APPROVED_SCAN = True
FACT = "The ScholarShip test instrument measured a temperature of 23 degrees Celsius."


def sample_pdf():
    # Deterministic one-page PDF with a real text layer, explicitly a test fixture.
    stream = ("BT /F1 12 Tf 50 740 Td (Synthetic local instrument fixture) Tj 0 -30 Td (" + FACT + ") Tj ET").encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    raw = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    start = len(raw)
    raw += b"xref\n0 6\n0000000000 65535 f \n"
    raw += b"".join(f"{n:010d} 00000 n \n".encode() for n in offsets[1:])
    return raw + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(start).encode() + b"\n%%EOF\n"


def main():
    report = {
        "passed": False,
        "mode": "local_inference",
        "cloud_requests": 0,
        "cloud_uploads": 0,
        "steps": [],
        "scientific_quality_verified": False,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    if REPORT.exists():
        history = REPORT.parent / "local-attempts"
        history.mkdir(exist_ok=True)
        shutil.copyfile(REPORT, history / (str(time.time_ns()) + ".json"))
    report["input_fixtures"] = ["synthetic_instrument_pdf", "approved_scan_existing_cloud_ocr"]
    report["environment"] = (
        "isolated_temporary_database"
        if os.environ.get("POSTGRES_DB", "").startswith("ss_test_")
        else "configured_application_database"
    )

    def save():
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    save()
    with httpx.Client(
        base_url=API, headers={"Origin": "http://127.0.0.1:15173"}, trust_env=False, timeout=60
    ) as client:

        def api(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code >= 400:
                raise RuntimeError("local_journey_http_" + str(response.status_code) + "_" + path.split("/")[0])
            return response.json()

        who = api(
            "POST",
            "auth/login",
            json={
                "username": "researcher",
                "password": (
                    os.environ.get("LOCAL_ACCEPTANCE_PASSWORD")
                    or dotenv_values(ROOT / ".env").get("LOCAL_ACCEPTANCE_PASSWORD")
                    or dotenv_values(ROOT / ".env")["INITIAL_USER_PASSWORD"]
                ),
            },
        )
        client.headers["X-CSRF-Token"] = client.cookies["ss_csrf"]
        owner = UUID(who["id"])

        def cloud_count():
            with tenant_transaction(owner) as conn:
                return conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM model_runtime.calls WHERE NOT simulated AND (config_json->'roles'->role->>'base_url') LIKE 'https://%'"
                    )
                )

        before = cloud_count()

        def wait(job, label, allow_partial=False):
            started = time.monotonic()
            while time.monotonic() - started < TIMEOUT_SECONDS:
                row = api("GET", "runs/" + job["id"])
                if row["status"] not in {"queued", "running"}:
                    record = {
                        "label": label,
                        "run_id": job["id"],
                        "status": row["status"],
                        "seconds": round(time.monotonic() - started, 2),
                    }
                    report["steps"].append(record)
                    save()
                    if row["status"] != "succeeded" and not (allow_partial and row["status"] == "partially_succeeded"):
                        record["error"] = row.get("error_json")
                        save()
                        raise RuntimeError("local_task_failed_" + label)
                    if row["status"] == "partially_succeeded":
                        record["accepted_partial_reason"] = "scan_text_layer_absent_verified_cloud_ocr_reused_next"
                    record["result"] = row.get("result_json")
                    save()
                    return row.get("result_json")
                time.sleep(1)
            raise RuntimeError("local_task_timeout_" + label)

        def task(operation, label=None, **arguments):
            return wait(
                api(
                    "POST",
                    f"studio/projects/{project}/tasks",
                    json={"operation": operation, "mode": "local_inference", **arguments},
                    headers={"Idempotency-Key": uuid4().hex},
                ),
                label or operation,
            )

        try:
            if api("GET", "system/external-services")["requested_external_flag"]:
                raise RuntimeError("cloud_global_gate_must_remain_closed")
            spaces = api("GET", "projects")
            project = next((p["id"] for p in spaces if p["name"] == WORKSPACE_NAME), None)
            if not project:
                project = api(
                    "POST",
                    "projects",
                    json={
                        "name": WORKSPACE_NAME,
                        "description": "真实本地模型验收；合成事实与已批准扫描 OCR；不调用云端",
                    },
                )["id"]
            report["project_id"] = project
            current = api("GET", f"projects/{project}/model-configuration")
            api(
                "PUT",
                f"projects/{project}/model-configuration",
                json={"preset": "local", "overrides": {}, "expected_fingerprint": current["fingerprint"]},
            )
            upload = api(
                "POST",
                f"projects/{project}/uploads",
                files={"file": ("synthetic-local-instrument.pdf", sample_pdf(), "application/pdf")},
                headers={"Idempotency-Key": uuid4().hex},
            )
            fixture = upload["version_id"]
            if upload.get("run_id"):
                wait({"id": upload["run_id"]}, "fixture_preview")
            parsed = task("local_parse", version_id=fixture)
            parse = parsed["parse_id"]
            wait(
                api(
                    "POST",
                    f"research/projects/{project}/indexes",
                    json={"version_ids": [fixture], "mode": "local_inference"},
                    headers={"Idempotency-Key": uuid4().hex},
                ),
                "fixture_index",
            )
            answer = wait(
                api(
                    "POST",
                    f"research/projects/{project}/questions",
                    json={
                        "version_ids": [fixture],
                        "mode": "local_inference",
                        "question": "What temperature did the ScholarShip test instrument measure?",
                    },
                    headers={"Idempotency-Key": uuid4().hex},
                ),
                "fixture_qa",
            )
            assert not answer["model_simulated"] and answer["citations"] and "23" in answer["answer"]
            blocks = api("GET", f"parses/{parse}/pages/1")["blocks"]
            block = next(b for b in blocks if "23" in b["canonical_text"])
            selection = {
                "block_id": block["id"],
                "start": 0,
                "end": len(block["canonical_text"]),
                "text": block["canonical_text"],
            }
            translated = task("translate", parse_id=parse, source_selection=selection, language="zh-CN")
            tid = translated["translation_id"]
            segment = api("GET", f"studio/translations/{tid}/review")[0]
            revision = api(
                "POST",
                f"studio/translations/{tid}/revisions",
                json={
                    "segment_id": segment["id"],
                    "expected_text_sha256": segment["text_sha256"],
                    "text": segment["translated_text"] + " [acceptance edit]",
                    "reason": "Synthetic local revision API test",
                },
            )
            assert revision["translation_id"] != tid
            report["translation_revision_api_checked"] = True
            for operation in ["summary", "draft", "direction"]:
                task(
                    operation,
                    parse_ids=[parse],
                    idea="Describe the synthetic temperature measurement; do not invent experiments.",
                )
            task("diagram", description="Source → Parse → Retrieve → Answer")
            chart = wait(
                api(
                    "POST",
                    f"studio/projects/{project}/charts",
                    files={"file": ("synthetic.csv", b"model,score\nControl,70\nCandidate,85\n", "text/csv")},
                    data={
                        "config": json.dumps(
                            {
                                "kind": "bar",
                                "x": "model",
                                "y": ["score"],
                                "units": {"score": "percent"},
                                "title": "Synthetic local acceptance",
                                "missing": "reject",
                            }
                        )
                    },
                    headers={"Idempotency-Key": uuid4().hex},
                ),
                "chart_export",
            )
            downloaded = client.get("studio/artifacts/" + chart["artifact_version_id"])
            downloaded.raise_for_status()
            from zipfile import ZipFile
            from io import BytesIO

            with ZipFile(BytesIO(downloaded.content)) as archive:
                assert archive.read("input.csv").startswith(b"model,score")
                assert "chart.png" in archive.namelist()
            papers = api("GET", f"projects/{project}/papers")
            graph = api("POST", f"studio/projects/{project}/graph", json={"paper_ids": [p["id"] for p in papers]})
            report["graph_checked"] = bool(graph)
            if USE_APPROVED_SCAN:
                ref = json.loads((ROOT / "var/reports/mineru-scan-reference.json").read_text())
                cloud = json.loads((ROOT / "var/reports/mineru-scan-cloud.json").read_text())
                source = cloud["source"]
                raw = (SOURCE_DIRECTORY / source["filename"]).read_bytes()
                archive = ROOT / "var/mineru-validation" / UUID(ref["run_id"]).hex / "result.zip"
                assert hashlib.sha256(raw).hexdigest() == source["sha256"]
                assert hashlib.sha256(archive.read_bytes()).hexdigest() == cloud["artifact"]["sha256"]
                scan = api(
                    "POST",
                    f"projects/{project}/uploads",
                    files={"file": (source["filename"], raw, "application/pdf")},
                    headers={"Idempotency-Key": uuid4().hex},
                )
                if scan.get("run_id"):
                    wait({"id": scan["run_id"]}, "scan_preview", allow_partial=True)
                from scholarship.domains.mineru_native import import_native

                pid = import_native(
                    owner, UUID(scan["version_id"]), archive.read_bytes(), expected_source_sha256=source["sha256"]
                )
                report["scan_parse_id"] = str(pid)
                wait(
                    api(
                        "POST",
                        f"research/projects/{project}/indexes",
                        json={"version_ids": [scan["version_id"]], "mode": "local_inference"},
                        headers={"Idempotency-Key": uuid4().hex},
                    ),
                    "scan_local_index",
                )
                scan_answer = wait(
                    api(
                        "POST",
                        f"research/projects/{project}/questions",
                        json={
                            "version_ids": [scan["version_id"]],
                            "mode": "local_inference",
                            "question": "How many NVIDIA P100 GPUs were used to train the base models?",
                        },
                        headers={"Idempotency-Key": uuid4().hex},
                    ),
                    "scan_evidence_qa",
                )
                assert scan_answer["citations"] and not scan_answer["model_simulated"]
                comparison = wait(
                    api(
                        "POST",
                        f"research/projects/{project}/questions",
                        json={
                            "version_ids": [fixture, scan["version_id"]],
                            "mode": "local_inference",
                            "question": "Compare the two sources: which reports a measured temperature of 23 degrees Celsius and which introduces the Transformer architecture? Cite both sources.",
                        },
                        headers={"Idempotency-Key": uuid4().hex},
                    ),
                    "two_source_comparison",
                )
                assert len({c["version_id"] for c in comparison["citations"]}) == 2
                for citation in comparison["citations"]:
                    located = api("GET", "research/evidence/" + citation["id"])
                    assert located["text"][located["start"] : located["end"]] == located["quote"]
                report["citation_source_navigation_checked"] = True
                with tenant_transaction(owner) as conn:
                    figure = conn.scalar(
                        sa.select(T["document_blocks"].c.id)
                        .where(
                            T["document_blocks"].c.parse_id == pid,
                            T["document_blocks"].c.kind == "figure",
                            T["document_blocks"].c.asset_file_id.is_not(None),
                        )
                        .order_by(T["document_blocks"].c.ordinal)
                        .limit(1)
                    )
                assert figure is not None
                visual = task("figure", block_ids=[str(figure)])
                assert not visual["model_simulated"] and visual["review_state"] == "unreviewed"
                report["vision_semantic_review"] = "unreviewed_not_scientific_acceptance"
            report["passed"] = True
        except Exception as exc:
            report["error_type"] = type(exc).__name__
            from scholarship.config import settings

            diagnostics = []
            with tenant_transaction(owner) as conn:
                calls = (
                    conn.execute(
                        sa.text(
                            "SELECT id,receipt_json FROM model_runtime.calls WHERE project_id=:p AND NOT simulated AND state IN ('unknown','rejected')"
                        ),
                        {"p": project},
                    )
                    .mappings()
                    .all()
                )
                for call in calls:
                    fid = (call["receipt_json"] or {}).get("diagnostic_file_id")
                    if fid:
                        f = (
                            conn.execute(sa.select(T["file_objects"]).where(T["file_objects"].c.id == UUID(fid)))
                            .mappings()
                            .one()
                        )
                        path = (Path(settings().scholar_data_dir) / "files" / f["storage_key"]).resolve()
                        diagnostics.append({"call_id": str(call["id"]), "diagnostic": json.loads(path.read_text())})
            report["failed_model_diagnostics"] = diagnostics
            raise
        finally:
            report["cloud_requests"] = cloud_count() - before
            report["passed"] = report["passed"] and report["cloud_requests"] == 0
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("Report: " + str(REPORT))


if __name__ == "__main__":
    main()
