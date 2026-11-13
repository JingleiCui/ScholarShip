"""PyCharm entry for offline business-chain integration (local infra required)."""

import os
from pathlib import Path
import subprocess
import sys

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parent
TEST_PATH = "backend/tests/test_offline_business.py"
REPORT = ROOT / "var/reports/m2-business.xml"
TIMEOUT_SECONDS = 180


def main():
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", TEST_PATH, f"--junitxml={REPORT}"],
        cwd=ROOT,
        env={**os.environ, "EXTERNAL_SERVICES_ENABLED": "false", "LANGSMITH_TRACING": "false"},
        timeout=TIMEOUT_SECONDS,
    )
    raise SystemExit(r.returncode)


if __name__ == "__main__":
    main()
