"""Exercise recovery on the real 541-page book; only kill our own worker."""

from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import time
from dotenv import dotenv_values
import httpx
from stop import candidates, is_launcher

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parent
API = "http://127.0.0.1:18080/api/v1"
TIMEOUT = 240


def main():
    values = dotenv_values(ROOT / ".env")
    process = None
    report = {"passed": False}
    with (
        (ROOT / "var/logs/m1-book-recovery.log").open("w") as log,
        httpx.Client(base_url=API, trust_env=False, headers={"Origin": "http://127.0.0.1:15173"}, timeout=5) as c,
    ):
        try:
            c.post(
                "/auth/login", json={"username": "researcher", "password": values["INITIAL_USER_PASSWORD"]}
            ).raise_for_status()
            c.headers["X-CSRF-Token"] = c.cookies["ss_csrf"]
            project = c.get("/projects").json()[0]["id"]
            paper = next(
                r for r in c.get(f"/projects/{project}/papers").json() if r["metadata_json"]["page_count"] == 541
            )
            job = c.post(f"/paper-versions/{paper['version_id']}/parses").json()
            start = time.monotonic()
            deadline = start + TIMEOUT
            first = None
            while time.monotonic() < deadline:
                p = c.get(f"/parses/{job['parse_id']}/pages/1")
                if p.status_code == 200:
                    first = p.json()["page"]["id"]
                    break
                time.sleep(0.05)
            assert first
            owners = [p for p in candidates() if is_launcher(p)]
            assert len(owners) == 1
            children = subprocess.check_output(["ps", "-axo", "pid,ppid,command"], text=True).splitlines()
            workers = [
                int(parts[0])
                for line in children
                if len(parts := line.strip().split(None, 2)) == 3
                and parts[1] == str(owners[0])
                and "scholarship.workers.celery_app:app worker" in parts[2]
            ]
            assert len(workers) == 1
            os.kill(workers[0], signal.SIGKILL)
            while is_launcher(owners[0]) and time.monotonic() < deadline:
                time.sleep(0.2)
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "dev.py")], cwd=ROOT, stdout=log, stderr=log, start_new_session=True
            )
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Recovery launcher exited")
                try:
                    r = c.get(f"/runs/{job['run_id']}")
                    if r.status_code == 200 and r.json()["status"] in ["succeeded", "partially_succeeded", "failed"]:
                        result = r.json()
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            assert result["status"] == "partially_succeeded", result
            assert c.get(f"/parses/{job['parse_id']}/pages/1").json()["page"]["id"] == first
            assert c.get(f"/parses/{job['parse_id']}/pages/541").status_code == 200
            report = {
                "passed": True,
                "job": job,
                "first_page_id_preserved": True,
                "real_worker_killed": True,
                "pages": 541,
                "seconds": round(time.monotonic() - start, 2),
                "result": result["result_json"],
            }
        finally:
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
            (ROOT / "var/reports/m1-book-recovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
