"""Opt-in actual loopback model + HTTP + queue acceptance in a disposable database."""

import json
import subprocess
import sys
import time
from uuid import uuid4

from dotenv import dotenv_values
import httpx

from scholarship.config import PROJECT_ROOT


def test_actual_local_document_pipeline(database, monkeypatch, accounts):
    # Does not reset or reuse the ordinary application account or its password.
    monkeypatch.setenv("LOCAL_ACCEPTANCE_PASSWORD", dotenv_values(PROJECT_ROOT / ".env")["INITIAL_USER_PASSWORD"])
    monkeypatch.setenv("CELERY_QUEUE", "scholarship_document_" + uuid4().hex)
    logfile = PROJECT_ROOT / "var/logs/document-acceptance.log"
    process = None
    with logfile.open("w") as log:
        try:
            process = subprocess.Popen([sys.executable, "dev.py"], cwd=PROJECT_ROOT, stdout=log, stderr=log)
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError("isolated_document_application_exited")
                try:
                    if httpx.get("http://127.0.0.1:18080/api/v1/health/ready", trust_env=False, timeout=2).is_success:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("isolated_document_application_timeout")
            subprocess.run(
                [sys.executable, "verify_document_pipeline.py"],
                cwd=PROJECT_ROOT,
                stdout=log,
                stderr=log,
                timeout=1800,
                check=True,
            )
            subprocess.run(
                [str(PROJECT_ROOT / ".tools/node/bin/node"), "scripts/check_document_ui.mjs"],
                cwd=PROJECT_ROOT,
                stdout=log,
                stderr=log,
                timeout=120,
                check=True,
            )
            subprocess.run(
                [str(PROJECT_ROOT / ".tools/node/bin/node"), "scripts/check_chat_ui.mjs"],
                cwd=PROJECT_ROOT,
                stdout=log,
                stderr=log,
                timeout=480,
                check=True,
            )
            report_path = PROJECT_ROOT / "var/reports/document-pipeline-real.json"
            report = json.loads(report_path.read_text())
            import sqlalchemy as sa
            from scholarship.infrastructure.database import tenant_transaction

            with tenant_transaction(accounts["researcher"]["user"]["id"]) as conn:
                cloud_calls = conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM model_runtime.calls WHERE NOT simulated AND "
                        "(config_json->'roles'->role->>'base_url') LIKE 'https://%'"
                    )
                )
            chat_report = PROJECT_ROOT / "var/reports/chat-ui.json"
            chat_data = json.loads(chat_report.read_text())
            chat_data["cloud_calls_in_isolated_database"] = cloud_calls
            chat_report.write_text(json.dumps(chat_data, ensure_ascii=False, indent=2) + "\n")
            assert cloud_calls == 0 and chat_data["passed"]
            report["environment"] = "isolated_temporary_database_real_http_queue_and_loopback_models"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            assert report["passed"] and report["new_cloud_model_calls"] == 0
        finally:
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=20)
