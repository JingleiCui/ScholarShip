"""Run from PyCharm. Install only this project's toolchain and infrastructure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tarfile
import urllib.request

# USER CONFIGURATION — normal usage: edit these values, then Run.
PROJECT_ROOT = Path(__file__).resolve().parent
PHASE = "all"  # all / toolchain / dependencies / infra / database
PYTHON_VERSION = "3.12.16"
NODE_VERSION = "24.22.3"
UV_VERSION = "0.13.8"
PNPM_VERSION = "11.28.11"
BOOTSTRAP_PYTHON = "/opt/homebrew/bin/python3.12"
TIMEOUT_SECONDS = 600
TOOLS = PROJECT_ROOT / ".tools"


def run(args: list[str], *, cwd: Path = PROJECT_ROOT, env: dict | None = None) -> None:
    result = subprocess.run(args, cwd=cwd, env=env, timeout=TIMEOUT_SECONDS)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {args[0]}")


def tool_env() -> dict[str, str]:
    return {
        **os.environ,
        "UV_PYTHON_INSTALL_DIR": str(TOOLS / "python"),
        "UV_CACHE_DIR": str(PROJECT_ROOT / ".cache/uv"),
        "PATH": f"{TOOLS / 'node/bin'}:{TOOLS / 'pnpm/bin'}:{os.environ.get('PATH', '')}",
        "LANGSMITH_TRACING": "false",
    }


def uv() -> str:
    return str(TOOLS / "bootstrap/bin/uv")


def node() -> str:
    return str(TOOLS / "node/bin/node")


def pnpm_args(*args: str) -> list[str]:
    return [node(), str(TOOLS / "pnpm/lib/node_modules/pnpm/bin/pnpm.cjs"), *args]


def install_toolchain() -> None:
    TOOLS.mkdir(exist_ok=True)
    if not (TOOLS / "bootstrap/bin/python").exists():
        run([BOOTSTRAP_PYTHON, "-m", "venv", str(TOOLS / "bootstrap")])
    if not Path(uv()).exists():
        run([str(TOOLS / "bootstrap/bin/python"), "-m", "pip", "install", f"uv=={UV_VERSION}"], env=tool_env())
    run([uv(), "python", "install", PYTHON_VERSION], env=tool_env())
    if not (PROJECT_ROOT / ".venv/bin/python").exists():
        run([uv(), "venv", "--python", PYTHON_VERSION, ".venv"], env=tool_env())
    if not Path(node()).exists():
        filename = f"node-v{NODE_VERSION}-darwin-arm64.tar.gz"
        base = f"https://nodejs.org/dist/v{NODE_VERSION}/"
        cache = PROJECT_ROOT / ".cache/downloads"
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / filename
        urllib.request.urlretrieve(base + filename, archive)
        with urllib.request.urlopen(base + "SHASUMS256.txt", timeout=60) as response:
            sums = response.read().decode()
        expected = next(line.split()[0] for line in sums.splitlines() if line.split()[-1] == filename)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Node checksum mismatch")
        with tarfile.open(archive) as tf:
            tf.extractall(TOOLS, filter="data")
        (TOOLS / f"node-v{NODE_VERSION}-darwin-arm64").rename(TOOLS / "node")
    if not (TOOLS / "pnpm/lib/node_modules/pnpm/bin/pnpm.cjs").exists():
        run(
            [
                node(),
                str(TOOLS / "node/lib/node_modules/npm/bin/npm-cli.js"),
                "install",
                "--prefix",
                str(TOOLS / "pnpm"),
                "--global",
                f"pnpm@{PNPM_VERSION}",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
            env=tool_env(),
        )


def initialize_env() -> None:
    target = PROJECT_ROOT / ".env"
    if target.exists():
        return
    text = (PROJECT_ROOT / ".env.example").read_text()
    for key in [
        "POSTGRES_PASSWORD",
        "RUNTIME_PASSWORD",
        "AUTH_PASSWORD",
        "CHECKPOINT_PASSWORD",
        "REDIS_PASSWORD",
        "QDRANT_API_KEY",
        "SESSION_SECRET",
        "INITIAL_USER_PASSWORD",
        "INITIAL_REVIEWER_PASSWORD",
    ]:
        text = text.replace(f"{key}=\n", f"{key}={secrets.token_hex(24)}\n")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(text)


def compose_args(*args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        "scholarship",
        "--project-directory",
        str(PROJECT_ROOT),
        "--env-file",
        str(PROJECT_ROOT / ".env"),
        "-f",
        str(PROJECT_ROOT / "deploy/compose.yaml"),
        *args,
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI overrides the configuration at the top of bootstrap.py")
    parser.add_argument("--phase", choices=["all", "toolchain", "dependencies", "infra", "database"], default=PHASE)
    phase = parser.parse_args().phase
    (PROJECT_ROOT / "var/logs").mkdir(parents=True, exist_ok=True)
    if phase in ("all", "toolchain"):
        install_toolchain()
    if phase in ("all", "dependencies"):
        run([uv(), "lock"], env=tool_env())
        run([uv(), "sync", "--locked"], env=tool_env())
        run(pnpm_args("install"), cwd=PROJECT_ROOT / "frontend", env=tool_env())
    if phase in ("all", "infra", "database"):
        initialize_env()
    if phase in ("all", "infra"):
        run(compose_args("--profile", "infra", "up", "-d", "--wait"), env=tool_env())
    if phase in ("all", "database"):
        run([str(PROJECT_ROOT / ".venv/bin/python"), "-m", "scholarship.infrastructure.initialize"], env=tool_env())
    print(json.dumps({"phase": phase, "status": "completed"}))


if __name__ == "__main__":
    main()
