"""PyCharm: one-click isolated model-plugin and end-to-end workflow validation."""

from pathlib import Path
import subprocess
import sys

# USER CONFIGURATION: all provider responses are synthetic; PG/Qdrant are real.
ROOT = Path(__file__).resolve().parent
TESTS = ["backend/tests/test_adapter_plugins.py", "backend/tests/test_model_pipeline.py"]
TIMEOUT_SECONDS = 180
REPORT = ROOT / "var/reports/model-pipeline.xml"


def main():
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *TESTS, f"--junitxml={REPORT}"], cwd=ROOT, timeout=TIMEOUT_SECONDS
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
