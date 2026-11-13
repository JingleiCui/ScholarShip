"""Local container integration; stops only ScholarShip application containers."""

import argparse
import fcntl
from pathlib import Path

from bootstrap import compose_args, run, tool_env

# USER CONFIGURATION
ACTION = "up"  # up / stop / build / status
ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["up", "stop", "build", "status"], default=ACTION)
    action = parser.parse_args().action
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
        run(compose_args("--profile", "app", "up", "-d", "--wait"), env=tool_env())
    elif action == "stop":
        run(compose_args("--profile", "app", "stop", "web", "beat", "worker", "api"), env=tool_env())
    elif action == "build":
        run(compose_args("--profile", "app", "build"), env=tool_env())
    else:
        run(compose_args("--profile", "app", "ps"), env=tool_env())


if __name__ == "__main__":
    main()
