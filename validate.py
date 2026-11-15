"""Repeatable project checks. Run in PyCharm; reports contain no credentials."""

from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from bootstrap import compose_args, pnpm_args, tool_env, uv
from doctor import report as doctor_report

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "var/reports"
RUN_FROZEN_INSTALL = True
RUN_DATABASE_INTEGRATION_TESTS = True  # creates and removes its own ss_test_<uuid> DB
TIMEOUT_SECONDS = 600
MILESTONE = "M2-live-business-offline-acceptance"


def run_check(name, args, cwd=ROOT):
    start = time.monotonic()
    path = REPORT_DIR / f"{name}.log"
    with path.open("w") as output:
        result = subprocess.run(
            args, cwd=cwd, env=tool_env(), stdout=output, stderr=subprocess.STDOUT, timeout=TIMEOUT_SECONDS
        )
    return {
        "name": name,
        "passed": result.returncode == 0,
        "seconds": round(time.monotonic() - start, 2),
        "log": str(path.relative_to(ROOT)),
    }


def secret_check():
    from dotenv import dotenv_values

    secrets = [
        v
        for k, v in dotenv_values(ROOT / ".env").items()
        if v and any(word in k for word in ["PASSWORD", "SECRET", "API_KEY"])
    ]
    candidates = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).split(b"\0")
    violations = []
    for raw in candidates:
        if not raw:
            continue
        path = ROOT / os.fsdecode(raw)
        if path.is_file():
            content = path.read_bytes()
            if any(value.encode() in content for value in secrets):
                violations.append(str(path.relative_to(ROOT)))
    return {"name": "secret_scan", "passed": not violations, "files_with_secret": violations}


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    checks = []
    if RUN_FROZEN_INSTALL:
        checks.append(run_check("uv_frozen", [uv(), "sync", "--locked"]))
        checks.append(run_check("pnpm_frozen", pnpm_args("install", "--frozen-lockfile"), ROOT / "frontend"))
    for module in [
        "fastapi",
        "sqlalchemy",
        "alembic",
        "psycopg",
        "langgraph.graph",
        "langgraph.checkpoint.postgres",
        "qdrant_client",
        "celery",
        "pypdf",
        "pdfplumber",
        "pandas",
        "matplotlib",
        "networkx",
        "tiktoken",
    ]:
        importlib.import_module(module)
    checks.append({"name": "core_imports", "passed": True})
    checks.append(
        run_check(
            "ruff",
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "backend",
                "bootstrap.py",
                "dev.py",
                "doctor.py",
                "deploy.py",
                "stop.py",
                "validate.py",
                "runtime_smoke.py",
                "evaluate_m1.py",
                "recover_m1.py",
                "estimate_m2.py",
                "verify_m2.py",
                "verify_m2_business.py",
                "verify_m2_live_business.py",
                "verify_user_journey.py",
                "verify_models.py",
                "validate_live_models.py",
                "model_config.py",
                "local_models.py",
                "local_services",
                "storage_preflight.py",
            ],
        )
    )
    checks.append(run_check("frontend_build", pnpm_args("build"), ROOT / "frontend"))
    if RUN_DATABASE_INTEGRATION_TESTS:
        checks.append(
            run_check("pytest", [sys.executable, "-m", "pytest", "-q", f"--junitxml={REPORT_DIR / 'pytest.xml'}"])
        )
    diagnosis = doctor_report()
    checks.append({"name": "dependency_health", "passed": diagnosis["dependencies"]["ready"]})
    checks.append(secret_check())
    raw = subprocess.check_output(compose_args("ps", "--format", "json"), cwd=ROOT, text=True).strip()
    containers = (
        json.loads(raw) if raw.startswith("[") else [json.loads(line) for line in raw.splitlines() if line.strip()]
    )
    images = []
    image_lock = json.loads((ROOT / "docs/environment/image-lock.json").read_text())
    locked = {entry["tag"]: entry["manifest_digest"] for entry in image_lock["images"]}
    for tag in [
        "postgres:17.12-bookworm",
        "qdrant/qdrant:v1.20.0",
        "redis:7.4.12-alpine",
        "python:3.12.16-slim-bookworm",
        "node:24.22.3-bookworm-slim",
        "caddy:2.11.9-alpine",
    ]:
        result = subprocess.run(
            ["docker", "image", "inspect", tag, "--format", "{{json .RepoDigests}}"], capture_output=True, text=True
        )
        images.append(
            {
                "tag": tag,
                "present_in_image_store": result.returncode == 0,
                "recorded_manifest_digest": locked.get(tag),
                "digests": json.loads(result.stdout) if result.returncode == 0 else [],
            }
        )
    tests = None
    if (REPORT_DIR / "pytest.xml").exists():
        suite = ET.parse(REPORT_DIR / "pytest.xml").find("testsuite")
        tests = dict(suite.attrib) if suite is not None else None
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "milestone": MILESTONE,
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "diagnostics": diagnosis,
        "tests": tests,
        "images": images,
        "containers": [
            {"service": c.get("Service"), "state": c.get("State"), "health": c.get("Health")} for c in containers
        ],
        "not_validated": [
            "paid provider integrations",
            "competition A01–A25 business acceptance",
            "real paper retrieval/translation/plot quality",
            "public hosting",
        ],
        "external_paid_calls": 0,
    }
    (REPORT_DIR / "initialization.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    for check in checks:
        print(f"{'PASS' if check['passed'] else 'FAIL'} {check['name']}")
    print("Report: var/reports/initialization.json")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
