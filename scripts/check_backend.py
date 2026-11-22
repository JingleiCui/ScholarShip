"""Consolidated isolated regression entry; no real model inference."""

from pathlib import Path
import argparse
import os
import subprocess
import sys

# USER CONFIGURATION; CLI flags are optional overrides.
ROOT = Path(__file__).resolve().parents[1]
GROUP = "all"
TIMEOUT_SECONDS = 300
GROUPS = {
    "contracts": ["test_provider_contracts.py", "test_shared_billing.py"],
    "business": ["test_offline_business.py"],
    "pipeline": ["test_adapter_plugins.py", "test_model_pipeline.py"],
    "local": ["test_local_inference.py", "test_model_hotplug.py"],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=["all", *GROUPS], default=GROUP)
    group = parser.parse_args().group
    files = sorted({p for key, values in GROUPS.items() if group in {"all", key} for p in values})
    report = ROOT / "var/reports" / ("backend-" + group + ".xml")
    report.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            *[str(ROOT / "backend/tests" / p) for p in files],
            f"--junitxml={report}",
        ],
        cwd=ROOT,
        env={**os.environ, "EXTERNAL_SERVICES_ENABLED": "false", "LANGSMITH_TRACING": "false"},
        timeout=TIMEOUT_SECONDS,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
