"""PyCharm Run entry: edit MODE or use an optional --mode override."""

from __future__ import annotations
import argparse
import fcntl
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

from bootstrap import compose_args, pnpm_args, tool_env

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parent
MODE = "all"  # all / api / worker / beat / frontend
API_HOST = "127.0.0.1"
API_PORT = 18080
WORKER_CONCURRENCY = 1  # macOS solo
POLL_SECONDS = 0.5


def application_env(mode="worker"):
    from scholarship.config import Settings, environment_values

    values = environment_values()
    # No administrator password or initial account password enters a child.
    allowed = {key.upper(): str(values[key]) for key in Settings.model_fields if key in values}
    if mode != "api":
        allowed.pop("AUTH_PASSWORD", None)
        allowed.pop("SESSION_SECRET", None)
    if mode != "worker":
        allowed.pop("CHECKPOINT_PASSWORD", None)
    base = {
        key: value
        for key, value in tool_env().items()
        if key in {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "LANGSMITH_TRACING"}
    }
    return {**base, **allowed, "SCHOLAR_ENV_ONLY": "1", "PYTHONUNBUFFERED": "1"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default=MODE, choices=["all", "api", "worker", "beat", "frontend"])
    mode = parser.parse_args().mode
    from scholarship.infrastructure.health import dependency_health

    if mode != "frontend" and not dependency_health()["ready"]:
        raise SystemExit("Infrastructure is not ready. Run doctor.py / bootstrap.py first.")
    python = str(ROOT / ".venv/bin/python")
    commands = {
        "api": [
            python,
            "-m",
            "uvicorn",
            "scholarship.main:create_app",
            "--factory",
            "--host",
            API_HOST,
            "--port",
            str(API_PORT),
            "--no-access-log",
        ],
        "worker": [
            python,
            "-m",
            "celery",
            "-A",
            "scholarship.workers.celery_app:app",
            "worker",
            "--pool=solo",
            f"--concurrency={WORKER_CONCURRENCY}",
            "--loglevel=WARNING",
            "--without-gossip",
            "--without-mingle",
        ],
        "beat": [
            python,
            "-m",
            "celery",
            "-A",
            "scholarship.workers.celery_app:app",
            "beat",
            "--loglevel=WARNING",
            "--schedule",
            str(ROOT / "var/celerybeat"),
        ],
        "frontend": pnpm_args("dev"),
    }
    processes = []
    locks = []
    selected = list(commands) if mode == "all" else [mode]
    if mode != "frontend":
        running = subprocess.run(
            compose_args("--profile", "app", "ps", "--status", "running", "-q", "api", "worker", "beat"),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if running.returncode or running.stdout.strip():
            raise SystemExit(
                "Container application services are running or could not be checked. Run deploy.py --action stop before local development."
            )
    lock_dir = ROOT / "var/locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    for name in selected:
        handle = (lock_dir / f"{name}.lock").open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"ScholarShip {name} is already running.")
        locks.append(handle)
    for name, port in [("api", API_PORT), ("frontend", 15173)]:
        if name in selected:
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind((API_HOST, port))
                except OSError:
                    raise SystemExit(f"Port {port} is occupied. Existing process was left running.")

    # PyCharm Stop can send SIGTERM. Handle it just like Ctrl+C and reap children.
    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        for name in selected:
            env = (
                application_env(name)
                if name != "frontend"
                else {k: v for k, v in tool_env().items() if k in {"PATH", "HOME", "USER", "LANG", "TMPDIR"}}
            )
            processes.append(
                subprocess.Popen(
                    commands[name],
                    cwd=ROOT / "frontend" if name == "frontend" else ROOT,
                    env=env,
                    start_new_session=True,
                )
            )
        print("ScholarShip: http://127.0.0.1:15173 · API: http://127.0.0.1:18080/docs", flush=True)
        while all(p.poll() is None for p in processes):
            time.sleep(POLL_SECONDS)
        raise RuntimeError("A project child exited. See its output above.")
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for handle in locks:
            handle.close()


if __name__ == "__main__":
    main()
