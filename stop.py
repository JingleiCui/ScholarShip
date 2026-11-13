"""Stop only launchers proven to belong to this project; retain all containers."""

import os
from pathlib import Path
import signal
import subprocess
import time

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parent
MODES = ["api", "worker", "beat", "frontend"]
STOP_TIMEOUT = 15


def candidates():
    pids = set()
    for mode in MODES:
        path = ROOT / f"var/locks/{mode}.lock"
        if path.exists():
            result = subprocess.run(["lsof", "-t", str(path)], capture_output=True, text=True)
            pids.update(int(line) for line in result.stdout.splitlines() if line.isdigit())
    return pids


def is_launcher(pid):
    command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout.strip()
    cwd = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
    return ("dev.py" in command.split() or str(ROOT / "dev.py") in command.split()) and f"n{ROOT}\n" in cwd


def main():
    owned = [pid for pid in candidates() if is_launcher(pid)]
    for pid in owned:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline and any(is_launcher(pid) for pid in owned):
        time.sleep(0.2)
    if any(is_launcher(pid) for pid in owned):
        raise SystemExit("A project launcher did not exit in time; no other process was touched.")
    print(f"Stopped {len(owned)} ScholarShip launcher(s); containers retained.")


if __name__ == "__main__":
    main()
