"""PyCharm entry: validate model configuration; optional isolated contract tests."""

import json
import os
from pathlib import Path
import subprocess
import sys
from scholarship.models.configuration import configuration_status

# USER CONFIGURATION: no model requests or downloads.
ROOT = Path(__file__).resolve().parent
RUN_TESTS = False
TEST_PATH = "backend/tests/test_model_configuration.py"
REPORT = ROOT / "var/reports/models-status.json"
TIMEOUT_SECONDS = 180


def main():
    status = configuration_status()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "preset": status["selected_preset"],
                "roles": {
                    k: {
                        "provider": v["provider"],
                        "model": v["model"],
                        "credential_present": v["credential_present"],
                        "verification": v["verification_state"],
                    }
                    for k, v in status["roles"].items()
                },
                "inference_requests": 0,
            },
            ensure_ascii=False,
        )
    )
    if RUN_TESTS:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", TEST_PATH],
            cwd=ROOT,
            env={**os.environ, "EXTERNAL_SERVICES_ENABLED": "false"},
            timeout=TIMEOUT_SECONDS,
        )
        raise SystemExit(r.returncode)


if __name__ == "__main__":
    main()
