"""Optional local inference services. Default check never installs or infers."""

import argparse
import json
from pathlib import Path
import runpy
import subprocess
import sys
from bootstrap import uv, tool_env

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parent
ACTION = "check"  # check / prepare / parser / rerank
ALLOW_DOWNLOADS = False
ENV_DIR = ROOT / ".tools/model-services"
PYTHON = ENV_DIR / "bin/python"
LOCK = ROOT / ".tools/model-services-requirements.lock"
HF_HOME = ROOT / ".tools/model-cache/huggingface"
PARSER_PORT = 18082
RERANK_PORT = 18083
RERANK_MODEL = "Qwen/Qwen3-Reranker-0.6B"
TIMEOUT_SECONDS = 1800


def run(args, timeout=TIMEOUT_SECONDS):
    subprocess.run(
        [str(x) for x in args],
        cwd=ROOT,
        env={**tool_env(), "HF_HOME": str(HF_HOME), "LANGSMITH_TRACING": "false"},
        check=True,
        timeout=timeout,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["check", "prepare", "parser", "rerank"], default=ACTION)
    action = parser.parse_args().action
    if action == "check":
        print(
            json.dumps(
                {
                    "isolated_environment_present": PYTHON.is_file(),
                    "lock_present": LOCK.is_file(),
                    "weights_prepared": (ENV_DIR / "weights-prepared.json").is_file(),
                    "inference_verified": False,
                    "inference_requests": 0,
                }
            )
        )
        return
    if action == "prepare":
        if not ALLOW_DOWNLOADS:
            raise SystemExit(
                "Set ALLOW_DOWNLOADS=True in this script before downloading software/weights. No inference is performed."
            )
        ENV_DIR.parent.mkdir(parents=True, exist_ok=True)
        if not PYTHON.is_file():
            run([uv(), "venv", "--python", sys.executable, ENV_DIR])
        run(
            [
                uv(),
                "pip",
                "compile",
                ROOT / "local_services/requirements.in",
                "--python",
                PYTHON,
                "--generate-hashes",
                "-o",
                LOCK,
            ]
        )
        run([uv(), "pip", "sync", "--python", PYTHON, LOCK])
        # Download public model files only; no hosted inference or user document upload.
        code = (
            "from huggingface_hub import snapshot_download; import json; from pathlib import Path; p=snapshot_download("
            + repr(RERANK_MODEL)
            + "); Path("
            + repr(str(ENV_DIR / "weights-prepared.json"))
            + ').write_text(json.dumps({"rerank_snapshot":p,"inference_verified":False}))'
        )
        run([PYTHON, "-c", code])
        print(
            "Reranker files prepared. MinerU model packs and Ollama embedding weights require the separate local-model validation stage; no parse or inference was run."
        )
        return
    config = runpy.run_path(str(ROOT / "model_config.py"))
    if not config["ALLOW_LOCAL_INFERENCE"]:
        raise SystemExit("Local inference is disabled in model_config.py.")
    if not PYTHON.is_file():
        raise SystemExit("Prepare the isolated environment first.")
    if action == "parser":
        run(
            [
                ENV_DIR / "bin/mineru-kit",
                "api-server",
                "--host",
                "127.0.0.1",
                "--port",
                str(PARSER_PORT),
                "--tier",
                "basic",
            ],
            timeout=None,
        )
    else:
        run([PYTHON, ROOT / "local_services/rerank_server.py", "--port", str(RERANK_PORT)], timeout=None)


if __name__ == "__main__":
    main()
