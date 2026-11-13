"""Local container integration; stops only ScholarShip application containers."""

import argparse
import fcntl
from pathlib import Path

from bootstrap import compose_args, run, tool_env
from storage_preflight import SHARED_DATA_DIR, main as check_storage

# USER CONFIGURATION
ACTION = "up"  # up / stop / build / status
ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["up", "stop", "build", "status"], default=ACTION)
    action = parser.parse_args().action
    env = tool_env()
    env["SCHOLAR_SHARED_DATA_DIR"] = str(SHARED_DATA_DIR.resolve())
    if action in {"up", "build"}:
        check_storage()
    if action == "up":
        for name in ["api", "worker", "beat"]:
            path = ROOT / f"var/locks/{name}.lock"
            if path.exists():
                with path.open("a") as handle:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise SystemExit(
                            "Local development is running. Stop its launcher before switching to containers."
                        )
        run(compose_args("--profile", "app", "up", "-d", "--wait"), env=env)
    elif action == "stop":
        run(compose_args("--profile", "app", "stop", "web", "beat", "worker", "api"), env=env)
    elif action == "build":
        run(compose_args("--profile", "app", "build"), env=env)
    else:
        run(compose_args("--profile", "app", "ps"), env=env)


if __name__ == "__main__":
    main()
