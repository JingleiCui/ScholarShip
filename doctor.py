"""Read-only diagnostics; output intentionally omits credentials."""

import json
import os
from pathlib import Path
import platform
import subprocess
import sys

from bootstrap import node, pnpm_args, uv, tool_env
from scholarship.infrastructure.health import dependency_health, public_meta
from scholarship.config import PROJECT_ROOT, settings

# USER CONFIGURATION
REPORT_PATH = Path(__file__).resolve().parent / "var/reports/doctor.json"


def report():
    versions = {}
    for name, args in {
        "node": [node(), "--version"],
        "pnpm": pnpm_args("--version"),
        "uv": [uv(), "--version"],
    }.items():
        versions[name] = subprocess.check_output(args, env=tool_env(), text=True, timeout=20).strip()
    cfg = settings()
    config = {
        "dotenv_exists": (PROJECT_ROOT / ".env").is_file(),
        "env_only": os.getenv("SCHOLAR_ENV_ONLY") == "1",
        "postgres_host": cfg.postgres_host,
        "postgres_port": cfg.postgres_port,
        "postgres_db": cfg.postgres_db,
        "redis_host": cfg.redis_host,
        "redis_port": cfg.redis_port,
        "qdrant_url": cfg.qdrant_url,
        "runtime_password_present": bool(cfg.runtime_password.get_secret_value()),
        "redis_password_present": bool(cfg.redis_password.get_secret_value()),
        "qdrant_key_present": bool(cfg.qdrant_api_key.get_secret_value()),
    }
    return {
        "python": platform.python_version(),
        "interpreter": sys.executable,
        "architecture": platform.machine(),
        "tools": versions,
        "configuration": config,
        "dependencies": dependency_health(),
        "meta": public_meta(),
    }


if __name__ == "__main__":
    result = report()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["dependencies"]["ready"] else 1)
