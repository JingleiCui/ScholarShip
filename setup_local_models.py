"""Project-scoped resumable local model setup and smoke validation.

Run with .venv/bin/python. No cloud inference or uploads; test documents enter only the loopback API. Stages are
logged separately and failed stages are never recorded as passed.
"""

from pathlib import Path
import json
import shutil
import subprocess
import sys
import time
import fcntl
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from urllib.parse import urlsplit
import httpx
from bootstrap import uv, tool_env

# USER CONFIGURATION (CLI arguments are not required).
ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".tools/model-services"
PYTHON = ENV / "bin/python"
OLLAMA_MODELS = ROOT / ".tools/ollama-models"
HF_HOME = ROOT / ".tools/model-cache/huggingface"
CHAT = "qwen3.5:9b"
EMBEDDING = "qwen3-embedding:0.6b"
RERANK = "Qwen/Qwen3-Reranker-0.6B"
DOWNLOADS_ENABLED = True  # Authorized by user on 2026-11-22.
LOCAL_SMOKE_ENABLED = True
ACCEPTANCE_MODE = "isolated"  # isolated / current_application
START_APPLICATION_AFTER_ACCEPTANCE = True
TIMEOUT = 7200
REPORT = ROOT / "var/reports/local-setup.json"


def environment():
    return {
        **tool_env(),
        "HF_HOME": str(HF_HOME),
        "OLLAMA_HOST": "127.0.0.1:11434",
        "OLLAMA_MODELS": str(OLLAMA_MODELS),
        "OLLAMA_NO_CLOUD": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_CONTEXT_LENGTH": "8192",
        "LANGSMITH_TRACING": "false",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_XET": "1",
        "HF_HUB_DOWNLOAD_TIMEOUT": "120",
    }


def run(name, args, *, timeout=TIMEOUT):
    log = ROOT / "var/logs" / ("local-" + name + ".log")
    print(name, flush=True)
    with log.open("w") as out:
        subprocess.run(
            [str(a) for a in args],
            cwd=ROOT,
            env=environment(),
            stdout=out,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=True,
        )


def start(name, args, url):
    try:
        with httpx.Client(trust_env=False) as client:
            client.get(url, timeout=2).raise_for_status()
        port = urlsplit(url).port
        listing = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"], capture_output=True, text=True
        )
        pids = {line[1:] for line in listing.stdout.splitlines() if line.startswith("p")}
        for pid in pids:
            cwd = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True, text=True)
            if "n" + str(ROOT) not in cwd.stdout.splitlines():
                raise RuntimeError(name + "_listener_not_owned_by_project")
        if not pids:
            raise RuntimeError(name + "_listener_ownership_unknown")
        return
    except httpx.HTTPError:
        pass
    with (ROOT / "var/logs" / ("local-" + name + ".log")).open("a") as log:
        child = subprocess.Popen(
            [str(a) for a in args], cwd=ROOT, env=environment(), stdout=log, stderr=log, start_new_session=True
        )
    (ROOT / "var/run" / ("local-" + name + ".pid")).write_text(str(child.pid))
    for _ in range(60):
        if child.poll() is not None:
            raise RuntimeError(name + "_exited")
        try:
            with httpx.Client(trust_env=False) as client:
                client.get(url, timeout=2).raise_for_status()
            return
        except httpx.HTTPError:
            time.sleep(1)
    raise RuntimeError(name + "_startup_timeout")


def main():
    for folder in ("var/logs", "var/run", "var/reports"):
        (ROOT / folder).mkdir(parents=True, exist_ok=True)
    with (ROOT / "var/run/local-setup.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        execute_setup()


def execute_setup():
    result = {
        "stages": {},
        "passed": False,
        "cloud_inference_requests": 0,
        "cloud_uploads": 0,
        "parser": "cloud_existing_results_only_no_new_upload",
    }
    write_lock = Lock()

    def save():
        temporary = REPORT.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, indent=2) + "\n")
        temporary.replace(REPORT)

    def stage(name, action):
        with write_lock:
            result["stages"][name] = {"state": "running", "started_at": time.time()}
            save()
        started = time.monotonic()
        try:
            action()
            state = {"state": "passed"}
        except Exception as exc:
            state = {"state": "failed", "error_type": type(exc).__name__}
        with write_lock:
            result["stages"][name] = {**state, "seconds": round(time.monotonic() - started, 2)}
            save()
        return state["state"] == "passed"

    if not DOWNLOADS_ENABLED:
        raise RuntimeError("downloads_disabled")
    ollama = shutil.which("ollama")
    if not ollama:
        raise RuntimeError("ollama_executable_missing_install_required")
    service = stage("ollama_service", lambda: start("ollama", [ollama, "serve"], "http://127.0.0.1:11434/api/tags"))

    def dependencies():
        if not PYTHON.is_file():
            run("venv", [uv(), "venv", "--python", sys.executable, ENV])
        lock = ROOT / "local_services/requirements.lock"
        if not lock.exists():
            raise RuntimeError("local_service_dependency_lock_missing")
        run("dependencies", [uv(), "pip", "sync", "--require-hashes", "--python", PYTHON, lock])

    dependency_ok = stage("dependencies", dependencies)
    jobs = [
        (
            "pull_" + m,
            lambda m=m: run(
                "pull-" + m.replace(":", "-"), [sys.executable, ROOT / "local_services/download_ollama.py", m]
            ),
        )
        for m in (CHAT, EMBEDDING)
    ]
    jobs.append(
        (
            "rerank_weights",
            lambda: run("rerank-download", [sys.executable, ROOT / "local_services/download_reranker.py"]),
        )
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(lambda job: stage(*job), jobs))
    rerank_ok = False
    if dependency_ok and outcomes[-1]:
        rerank_ok = stage(
            "rerank_service",
            lambda: start(
                "rerank", [PYTHON, ROOT / "local_services/rerank_server.py"], "http://127.0.0.1:18083/health"
            ),
        )
    if service and dependency_ok and all(outcomes) and rerank_ok and LOCAL_SMOKE_ENABLED:
        if ACCEPTANCE_MODE == "isolated":
            stopped = stage("stop_owned_application", lambda: run("stop-owned", [sys.executable, ROOT / "stop.py"]))
            if stopped:
                stage(
                    "local_acceptance",
                    lambda: run(
                        "acceptance",
                        [sys.executable, "-m", "pytest", "-q", ROOT / "backend/tests/local_model_acceptance.py"],
                    ),
                )
        elif ACCEPTANCE_MODE == "current_application":
            api_ok = stage(
                "application",
                lambda: start(
                    "application", [sys.executable, ROOT / "dev.py"], "http://127.0.0.1:18080/api/v1/health/ready"
                ),
            )
            if api_ok:
                stage("local_journey", lambda: run("journey", [sys.executable, ROOT / "verify_local_journey.py"]))
        else:
            raise ValueError("invalid_acceptance_mode")
    else:
        result["stages"]["local_acceptance"] = {"state": "not_run", "reason": "prerequisites_not_ready_or_disabled"}
    if START_APPLICATION_AFTER_ACCEPTANCE:
        stage(
            "application",
            lambda: start(
                "application", [sys.executable, ROOT / "dev.py"], "http://127.0.0.1:18080/api/v1/health/ready"
            ),
        )
    result["passed"] = all(s["state"] == "passed" for s in result["stages"].values())
    save()
    print("Report: " + str(REPORT), flush=True)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
