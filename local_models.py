"""Small compatibility entry for project-local models; MinerU stays in the cloud."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

# USER CONFIGURATION; flags only override ACTION.
ROOT = Path(__file__).resolve().parent
ACTION = "check"  # check / all / rerank


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["check", "all", "rerank"], default=ACTION)
    action = parser.parse_args().action
    if action == "check":
        print(
            json.dumps(
                {
                    "isolated_environment_present": (ROOT / ".tools/model-services/bin/python").is_file(),
                    "weights_prepared": (ROOT / ".tools/model-cache/reranker/verified.json").is_file(),
                    "inference_verified": False,
                    "inference_requests": 0,
                    "parser": "cloud",
                }
            )
        )
        return
    if action == "all":
        subprocess.run([sys.executable, str(ROOT / "setup_local_models.py")], cwd=ROOT, check=True)
        return
    from setup_local_models import PYTHON, environment

    subprocess.run(
        [str(PYTHON), str(ROOT / "local_services/rerank_server.py")], cwd=ROOT, env=environment(), check=True
    )


if __name__ == "__main__":
    main()
