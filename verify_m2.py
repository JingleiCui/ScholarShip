"""PyCharm entry: synthetic provider contracts and isolated billing integration."""

import os
from pathlib import Path
import subprocess
import sys

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parent
TEST_PATHS = ["backend/tests/test_provider_contracts.py", "backend/tests/test_shared_billing.py"]
REPORT = ROOT / "var/reports/m2-contracts.xml"
TIMEOUT_SECONDS = 180


def main():
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *TEST_PATHS, f"--junitxml={REPORT}"],
        cwd=ROOT,
        env={**os.environ, "EXTERNAL_SERVICES_ENABLED": "false", "LANGSMITH_TRACING": "false"},
        timeout=TIMEOUT_SECONDS,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
