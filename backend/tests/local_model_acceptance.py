"""Opt-in REAL loopback acceptance: run this file explicitly, never default CI.

Uses the established temporary database fixture and never enables cloud access.
"""

from dataclasses import fields
import json
from pathlib import Path
import time
from uuid import uuid4
import sqlalchemy as sa
from scholarship.domains.offline_workflow import enqueue
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.models.configuration import load_selection
from scholarship.models.local import LocalGateway
from scholarship.models.contracts import ModelResult
from scholarship.models.runtime import register_embedding_profile
from test_offline_business import case as case

REPORT = Path(__file__).resolve().parents[2] / "var/reports/local-embedding-real.json"


def test_actual_local_embedding_and_durable_profile(case):
    owner, project = case
    selection = load_selection("local")
    run = enqueue(owner, project, "research_build", uuid4().hex, {}, dispatch=False)["id"]
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == run)
            .values(config_json={"mode": "local_inference", "models": selection.snapshot()})
        )
    started = time.monotonic()
    with LocalGateway(owner, project, run, selection) as gateway:
        result = gateway.invoke(
            "actual-embed", "embedding", "embed", {"texts": ["The synthetic instrument measured 23 degrees Celsius."]}
        )
    assert result["dimensions"] == 1024 and result["simulated"] is False
    with tenant_transaction(owner) as conn:
        call = conn.scalar(sa.text("SELECT id FROM model_runtime.calls WHERE run_id=:r"), {"r": run})
    receipt = ModelResult(**{f.name: result[f.name] for f in fields(ModelResult)})
    profile = register_embedding_profile(owner, selection.roles["embedding"], receipt, call_id=call)
    REPORT.write_text(
        json.dumps(
            {
                "passed": True,
                "seconds": round(time.monotonic() - started, 2),
                "dimensions": result["dimensions"],
                "usage": result["usage"],
                "cloud_requests": 0,
                "profile_id": str(profile),
                "environment": "isolated_acceptance_database",
            },
            indent=2,
        )
        + "\n"
    )


def test_actual_full_http_queue_journey(database, monkeypatch):
    import subprocess
    import sys
    import httpx
    from dotenv import dotenv_values
    from scholarship.config import PROJECT_ROOT

    from setup_local_models import start, PYTHON

    start("rerank", [PYTHON, PROJECT_ROOT / "local_services/rerank_server.py"], "http://127.0.0.1:18083/health")
    initial = dotenv_values(PROJECT_ROOT / ".env")["INITIAL_USER_PASSWORD"]
    monkeypatch.setenv("LOCAL_ACCEPTANCE_PASSWORD", initial)
    monkeypatch.setenv("CELERY_QUEUE", "scholarship_local_acceptance_" + uuid4().hex)
    log_path = PROJECT_ROOT / "var/logs/local-isolated-journey.log"
    process = None
    with log_path.open("w") as log:
        try:
            subprocess.run(
                [sys.executable, "runtime_smoke.py"], cwd=PROJECT_ROOT, stdout=log, stderr=log, check=True, timeout=180
            )
            process = subprocess.Popen([sys.executable, "dev.py"], cwd=PROJECT_ROOT, stdout=log, stderr=log)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("isolated_application_exited")
                try:
                    response = httpx.get("http://127.0.0.1:18080/api/v1/health/ready", trust_env=False, timeout=2)
                    if response.is_success:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("isolated_application_not_ready")
            subprocess.run(
                [sys.executable, "verify_local_journey.py"],
                cwd=PROJECT_ROOT,
                stdout=log,
                stderr=log,
                check=True,
                timeout=7200,
            )
            subprocess.run(
                [str(PROJECT_ROOT / ".tools/node/bin/node"), str(PROJECT_ROOT / "scripts/check_local_ui.mjs")],
                cwd=PROJECT_ROOT,
                stdout=log,
                stderr=log,
                check=True,
                timeout=120,
            )
            result = json.loads((PROJECT_ROOT / "var/reports/local-journey.json").read_text())
            result["environment"] = "isolated_temporary_database_real_http_queue_and_models"
            (PROJECT_ROOT / "var/reports/local-journey.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            )
            assert result["passed"] and result["cloud_requests"] == 0
        finally:
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=20)


def test_actual_local_chat_translation_and_vision(case):
    from scholarship.adapters.providers import Evidence
    from io import BytesIO
    import base64
    from PIL import Image, ImageDraw

    owner, project = case
    selection = load_selection("local")
    run = enqueue(owner, project, "research_qa", uuid4().hex, {}, dispatch=False)["id"]
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == run)
            .values(config_json={"mode": "local_inference", "models": selection.snapshot()})
        )
    report = {"passed": False, "cloud_requests": 0, "environment": "isolated_acceptance_database", "steps": []}
    path = REPORT.with_name("local-generation-real.json")

    def call(gateway, intent, role, operation, args):
        start = time.monotonic()
        result = gateway.invoke(intent, role, operation, args)
        report["steps"].append(
            {
                "name": intent,
                "seconds": round(time.monotonic() - start, 2),
                "usage": result["usage"],
                "value": result["value"],
            }
        )
        return result

    try:
        with LocalGateway(owner, project, run, selection) as gateway:
            answer = call(
                gateway,
                "answer",
                "chat",
                "answer",
                {
                    "question": "What temperature did the instrument measure?",
                    "evidence": [Evidence("fixture", "The instrument measured 23 degrees Celsius.", 1)],
                    "citation_mode": "quote_id",
                },
            )
            assert "23" in answer["value"]["answer"] and answer["value"]["claims"] and not answer["simulated"]
            translated = call(
                gateway,
                "translation",
                "translation",
                "generate",
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": "Translate into Chinese, preserve the number: The instrument measured 23 degrees Celsius.",
                        }
                    ]
                },
            )
            assert "23" in translated["value"]
            image = Image.new("RGB", (640, 220), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((20, 60, 230, 160), outline="black", width=3)
            draw.rectangle((410, 60, 620, 160), outline="black", width=3)
            draw.text((85, 100), "Source", fill="black", font_size=30)
            draw.text((470, 100), "Answer", fill="black", font_size=30)
            draw.line((230, 110, 410, 110), fill="black", width=4)
            draw.polygon([(410, 110), (395, 102), (395, 118)], fill="black")
            raw = BytesIO()
            image.save(raw, format="PNG")
            visual = call(
                gateway,
                "vision",
                "chat",
                "describe_figure",
                {
                    "image_base64": base64.b64encode(raw.getvalue()).decode(),
                    "mime": "image/png",
                    "caption": "Synthetic two-node test graph",
                },
            )
            labels = " ".join(visual["value"]["visible_text"] + [n["text"] for n in visual["value"]["nodes"]]).lower()
            report["synthetic_vision_labels_correct"] = "source" in labels and "answer" in labels
            assert report["synthetic_vision_labels_correct"]
        report["passed"] = True
    finally:
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
