"""Pinned official Qwen ModelScope snapshot, TLS plus per-file SHA-256 checks."""

from pathlib import Path
import hashlib
import json
import subprocess
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
PROXY = "http://127.0.0.1:7897"
DESTINATION = ROOT / ".tools/model-cache/reranker"
MODEL = "Qwen/Qwen3-Reranker-0.6B"
REVISION = "0311d7b88fd535395d154b17fe08f85327306508"


def main():
    args = [
        "curl",
        "--proxy",
        PROXY,
        "--fail",
        "--location",
        "--silent",
        "--show-error",
        "--retry",
        "4",
        "--retry-all-errors",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
    ]
    base = f"https://modelscope.cn/api/v1/models/{MODEL}/repo"
    query = urlencode({"Revision": REVISION, "Recursive": "true"})
    meta = json.loads(subprocess.check_output(args + ["--max-time", "120", base + "/files?" + query]))
    if meta.get("Success") is not True:
        raise ValueError("snapshot_metadata_failed")
    DESTINATION.mkdir(parents=True, exist_ok=True)
    verified = []
    for item in meta["Data"]["Files"]:
        if item["Type"] != "blob":
            continue
        name = item["Path"]
        path = DESTINATION / name
        if not path.resolve().is_relative_to(DESTINATION.resolve()):
            raise ValueError("snapshot_path_invalid")
        expected = item["Sha256"]
        if not expected or len(expected) != 64:
            raise ValueError("snapshot_digest_missing")
        if path.is_file() and sha(path) == expected:
            verified.append(name)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".partial")
        url = base + "?" + urlencode({"Revision": REVISION, "FilePath": name})
        print(name, item["Size"], flush=True)
        subprocess.run(args + ["--max-time", "7200", "--continue-at", "-", "-o", str(partial), url], check=True)
        if sha(partial) != expected:
            raise ValueError("model_sha256_mismatch")
        partial.replace(path)
        verified.append(name)
    (DESTINATION / "verified.json").write_text(
        json.dumps({"model": MODEL, "revision": REVISION, "source": "modelscope.cn", "files": verified}) + "\n"
    )


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


if __name__ == "__main__":
    main()
