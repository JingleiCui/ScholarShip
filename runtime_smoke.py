"""Real HTTP/queue smoke and local restart recovery. No browser or paid API."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import time
from uuid import uuid4

from dotenv import dotenv_values
import httpx

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parent
TARGET = "local"  # local: manage owned launcher; container: test an already started app profile
START_TIMEOUT = 25
TASK_TIMEOUT = 35
REPORT_DIR = ROOT / "var/reports"


def wait_http(base, process=None):
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError("Owned launcher exited; see runtime-smoke.log")
        try:
            if httpx.get(base + "/api/v1/health/ready", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise RuntimeError("Readiness timeout")


def launch(mode, log):
    return subprocess.Popen([sys.executable, str(ROOT / "dev.py"), "--mode", mode], cwd=ROOT, stdout=log, stderr=log)


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()  # dev.py reaps only the children that it started
        process.wait(timeout=15)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["local", "container"], default=TARGET)
    target = parser.parse_args().target
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    base = "http://127.0.0.1:" + ("18080" if target == "local" else "18081")
    origin = "http://127.0.0.1:" + ("15173" if target == "local" else "18081")
    values = dotenv_values(ROOT / ".env")
    process = None
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target": target,
        "external_paid_calls": 0,
        "passed": False,
    }
    with (REPORT_DIR / f"runtime-{target}.log").open("w") as log:
        try:
            if target == "local":
                for port in (18080, 15173):
                    with socket.socket() as sock:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        try:
                            sock.bind(("127.0.0.1", port))
                        except OSError:
                            raise RuntimeError(
                                f"Port {port} is occupied; stop the existing launcher before the restart test."
                            )
                process = launch("api", log)
            wait_http(base, process)
            with httpx.Client(base_url=base, headers={"Origin": origin}, timeout=5) as client:
                response = client.post(
                    "/api/v1/auth/login",
                    json={
                        "username": "researcher",
                        "password": os.environ.get("LOCAL_ACCEPTANCE_PASSWORD")
                        or values.get("LOCAL_ACCEPTANCE_PASSWORD")
                        or values["INITIAL_USER_PASSWORD"],
                    },
                )
                response.raise_for_status()
                client.headers["X-CSRF-Token"] = client.cookies["ss_csrf"]
                projects = client.get("/api/v1/projects").json()
                response = client.post(
                    "/api/v1/runs", json={"project_id": projects[0]["id"]}, headers={"Idempotency-Key": uuid4().hex}
                )
                response.raise_for_status()
                rid = response.json()["id"]
                if target == "local":
                    assert response.json()["status"] == "queued"
                    stop(process)
                    process = launch("all", log)
                    wait_http(base, process)
                    assert client.get("/api/v1/auth/me").status_code == 200  # persistent session across API restart
                deadline = time.monotonic() + TASK_TIMEOUT
                while time.monotonic() < deadline:
                    run = client.get(f"/api/v1/runs/{rid}").json()
                    if run["status"] in {"succeeded", "failed"}:
                        break
                    time.sleep(0.3)
                assert run["status"] == "succeeded"
                events = client.get(f"/api/v1/runs/{rid}/events").text
                assert all(f"event: {name}" in events for name in ["queued", "running", "succeeded"])
                assert client.get(f"/api/v1/runs/{rid}/events", headers={"Last-Event-ID": "2"}).text.count("id: ") == 1
                report.update(
                    passed=True,
                    run_id=rid,
                    events=3,
                    session_persisted=target == "local",
                    queued_task_survived_restart=target == "local",
                )
                if target == "container":
                    page = client.get("/")
                    assert page.status_code == 200 and "ScholarShip" in page.text
                client.post("/api/v1/auth/logout").raise_for_status()
        finally:
            stop(process)
            (REPORT_DIR / f"runtime-{target}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
