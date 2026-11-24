"""Real local-only PDF/OCR/vision/translation journey in a new, isolated workspace.

Run directly from the project IDE. No original PDFs or existing indexes are changed.
"""

from io import BytesIO
import json
import os
from pathlib import Path
import time
from uuid import UUID, uuid4

from dotenv import dotenv_values
import httpx
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
import sqlalchemy as sa

from scholarship.infrastructure.database import tenant_transaction

# USER CONFIGURATION. Only synthetic, self-authored content goes to loopback models.
ROOT = Path(__file__).resolve().parent
API = "http://127.0.0.1:18080/api/v1/"
REPORT = ROOT / "var/reports/document-pipeline-real.json"
WORKSPACE = "本地解析与双语验收"
TIMEOUT_SECONDS = 900
CHECK_QA = True


def samples():
    writer = PdfWriter()
    page = writer.add_blank_page(600, 800)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 16 Tf 40 760 Td (SYNTHETIC LOCAL ACCEPTANCE) Tj ET\n"
        b"BT /F1 12 Tf 40 700 Td (Temperature is 23 degrees Celsius.) Tj ET\n"
        b"BT /F1 12 Tf 40 680 Td (The source is a synthetic instrument.) Tj ET\n"
        b"BT /F1 12 Tf 330 700 Td (Right column keeps its own order.) Tj ET\n"
        b"BT /F1 12 Tf 330 680 Td (No scientific result is claimed.) Tj ET\n"
        b"40 430 180 120 re S 350 430 180 120 re S 220 490 m 350 490 l S\n"
        b"BT /F1 12 Tf 70 485 Td (Input) Tj ET\n"
        b"BT /F1 12 Tf 380 485 Td (Output) Tj ET\n"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = BytesIO()
    writer.write(out)
    native = out.getvalue()
    from matplotlib import font_manager

    image = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_manager.findfont("DejaVu Sans"), 32)
    draw.text((80, 100), "SYNTHETIC LOCAL OCR ACCEPTANCE", fill="black", font=font)
    draw.text((80, 160), "The sample uses eight GPU devices.", fill="black", font=font)
    draw.rectangle((120, 400, 450, 650), outline="black", width=6)
    draw.rectangle((700, 400, 1050, 650), outline="black", width=6)
    draw.line((450, 525, 700, 525), fill="black", width=6)
    draw.text((180, 500), "Input", fill="black", font=font)
    draw.text((760, 500), "Output", fill="black", font=font)
    out = BytesIO()
    image.save(out, format="PDF", resolution=150)
    return [("Local two-column fixture.pdf", native), ("Local scanned fixture.pdf", out.getvalue())]


def main():
    report = {
        "passed": False,
        "scope": "self_authored_fixture_local_only",
        "steps": [],
        "figure_semantics_accepted": False,
        "cloud_uploads": 0,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    if REPORT.exists():
        prior = REPORT.with_name(f"document-pipeline-real-{time.time_ns()}.json")
        prior.write_bytes(REPORT.read_bytes())

    def save():
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    with httpx.Client(
        base_url=API, trust_env=False, timeout=60, headers={"Origin": "http://127.0.0.1:15173"}
    ) as client:

        def api(method, path, **kwargs):
            r = client.request(method, path, **kwargs)
            if not r.is_success:
                raise RuntimeError(f"http_{r.status_code}_{path.split('/')[0]}")
            return r.json()

        identity = api(
            "POST",
            "auth/login",
            json={
                "username": "researcher",
                "password": os.environ.get("LOCAL_ACCEPTANCE_PASSWORD")
                or dotenv_values(ROOT / ".env").get("LOCAL_ACCEPTANCE_PASSWORD")
                or dotenv_values(ROOT / ".env")["INITIAL_USER_PASSWORD"],
            },
        )
        owner = UUID(identity["id"])
        client.headers["X-CSRF-Token"] = client.cookies["ss_csrf"]

        def cloud_count():
            with tenant_transaction(owner) as c:
                return c.scalar(
                    sa.text(
                        "SELECT count(*) FROM model_runtime.calls WHERE NOT simulated AND "
                        "(config_json->'roles'->role->>'base_url') LIKE 'https://%'"
                    )
                )

        before = cloud_count()
        project = api("POST", "projects", json={"name": WORKSPACE + "-" + uuid4().hex[:6]})["id"]
        report["project_id"] = project

        def wait(run, label):
            start = time.monotonic()
            while time.monotonic() - start < TIMEOUT_SECONDS:
                row = api("GET", "runs/" + str(run))
                if row["status"] not in {"queued", "running"}:
                    report["steps"].append(
                        {
                            "step": label,
                            "run_id": str(run),
                            "status": row["status"],
                            "seconds": round(time.monotonic() - start, 2),
                            "error": row.get("error_json"),
                        }
                    )
                    save()
                    print(label, row["status"], flush=True)
                    if row["status"] != "succeeded":
                        raise RuntimeError("local_step_not_successful_" + label)
                    return row["result_json"]
                time.sleep(1)
            raise RuntimeError("local_task_timeout_" + label)

        try:
            records = []
            for name, raw in samples():
                uploaded = api(
                    "POST",
                    f"projects/{project}/uploads",
                    files={"file": (name, raw, "application/pdf")},
                    headers={"Idempotency-Key": uuid4().hex},
                )
                vid = uploaded["version_id"]
                result = wait(uploaded["preparation_run_id"], "automatic_parse_and_schedule_" + name)
                wait(result["index_run_id"], "automatic_index_" + name)
                state = api("GET", f"research/versions/{vid}/preparation")
                assert state["parse_ready"] and state["index_ready"]
                with tenant_transaction(owner) as c:
                    from scholarship.infrastructure.schema import tables as T

                    stages = (
                        c.execute(
                            sa.select(T["run_events"].c.public_payload).where(
                                T["run_events"].c.run_id == UUID(result["index_run_id"])
                            )
                        )
                        .scalars()
                        .all()
                    )
                assert {"chunking", "embedding", "index_verification", "index_ready"} <= {
                    e.get("stage") for e in stages
                }
                report.setdefault("automatic_preparation", []).append(
                    {
                        "version_id": vid,
                        "progress": state,
                        "observed_index_stages": [e["stage"] for e in stages if e.get("stage")],
                    }
                )
                for child in result["children"]:
                    wait(child["id"], child["operation"] + "_" + name)
                aligned = api("GET", f"studio/versions/{vid}/alignment")
                assert aligned["translation_blocks"] == aligned["total_blocks"] > 0
                for block in aligned["blocks"]:
                    assert "".join(p["source"] for p in block["parts"]) == block["canonical_text"]
                    assert not any(p.get("model_simulated") for p in block["parts"])
                assert any(
                    "\u4e00" <= char <= "\u9fff"
                    for b in aligned["blocks"]
                    for p in b["parts"]
                    for char in (p["translation"] or "")
                )
                records.append(
                    {
                        "version_id": vid,
                        "parse_id": aligned["parse_id"],
                        "name": name,
                        "blocks": aligned["total_blocks"],
                        "translated_blocks": aligned["translation_blocks"],
                        "ocr_pages": aligned["ocr_pages"],
                        "figures": sum(c["operation"] == "figure" for c in result["children"]),
                    }
                )
                report["documents"] = records
                save()
            assert records[1]["ocr_pages"] == [1] and not records[0]["ocr_pages"]
            if CHECK_QA:
                vid = records[0]["version_id"]
                question = api(
                    "POST",
                    f"research/projects/{project}/questions",
                    json={"version_ids": [vid], "mode": "local_inference", "question": "测试仪器的温度是多少摄氏度？"},
                    headers={"Idempotency-Key": uuid4().hex},
                )
                answer = wait(question["id"], "chinese_query_english_source")
                assert "23" in answer["answer"] and answer["citations"]
                quote = api("GET", "research/evidence/" + answer["citations"][0]["id"])
                assert quote.get("pdf_bbox") and "23" in quote["quote"]
                report["qa"] = {"answer": answer["answer"], "quote": quote["quote"], "page": quote["page"]}
            report["passed"] = True
        finally:
            report["new_cloud_model_calls"] = cloud_count() - before
            save()
    assert report["new_cloud_model_calls"] == 0
    print("PASS local document pipeline; report:", REPORT, flush=True)


if __name__ == "__main__":
    main()
