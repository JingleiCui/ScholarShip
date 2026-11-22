"""Download official public Ollama layers with TLS and SHA-256 verification.

Used when a fake-IP resolver prevents Ollama's own registry downloader running.
Never fetches cloud inference endpoints. All files stay in the project cache.
"""

from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys
import fcntl
from concurrent.futures import ThreadPoolExecutor
import shutil

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".tools/ollama-models"
TIMEOUT = 7200
CHUNK_BYTES = 128 * 1024 * 1024
LARGE_DOWNLOAD_WORKERS = 6
SHARED_READ_ONLY_CACHE = Path.home() / ".ollama/models/blobs"
DOWNLOAD_PROXY = "http://127.0.0.1:7897"  # Set None for a directly connected host.


def download(model):
    if not re.fullmatch(r"[a-z0-9.-]+:[a-z0-9.-]+", model):
        raise ValueError("invalid_model_name")
    name, tag = model.split(":")
    base = "https://registry.ollama.ai/v2/library/" + name
    raw = subprocess.check_output(
        [
            "curl",
            *(["--proxy", DOWNLOAD_PROXY] if DOWNLOAD_PROXY else ["--noproxy", "*"]),
            "--retry",
            "4",
            "--retry-all-errors",
            "--fail",
            "--silent",
            "--show-error",
            "--max-time",
            "120",
            base + "/manifests/" + tag,
        ]
    )
    manifest = json.loads(raw)

    def fetch_layer(layer):
        digest = layer["digest"]
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise ValueError("invalid_layer_digest")
        target = CACHE / "blobs" / digest.replace(":", "-")
        target.parent.mkdir(parents=True, exist_ok=True)

        def valid():
            if not target.is_file() or target.stat().st_size != layer["size"]:
                return False
            with target.open("rb") as stream:
                return hashlib.file_digest(stream, "sha256").hexdigest() == digest.split(":")[1]

        if valid():
            return
        shared = SHARED_READ_ONLY_CACHE / digest.replace(":", "-")
        if shared.is_file() and shared.stat().st_size == layer["size"]:
            with shared.open("rb") as stream:
                correct = hashlib.file_digest(stream, "sha256").hexdigest() == digest.split(":")[1]
            if correct:
                copied = target.with_suffix(".copy")
                shutil.copyfile(shared, copied)
                copied.replace(target)
                print(model, digest[:22], "reused_verified_read_only_cache", flush=True)
                return
        partial = target.with_suffix(".partial")
        print(model, digest[:22], layer["size"], flush=True)
        if layer["size"] > 2 * 1024**3:
            parts = target.with_suffix(".parts")
            parts.mkdir(exist_ok=True)
            # Reuse complete chunks from the old sequential download.
            existing = partial.stat().st_size if partial.exists() else 0
            with partial.open("rb") if partial.exists() else open("/dev/null", "rb") as stream:
                for start in range(0, existing - CHUNK_BYTES + 1, CHUNK_BYTES):
                    dest = parts / str(start)
                    if not dest.exists():
                        with dest.open("wb") as out:
                            out.write(stream.read(CHUNK_BYTES))
                    else:
                        stream.seek(CHUNK_BYTES, 1)

            def get_part(start):
                end = min(start + CHUNK_BYTES, layer["size"]) - 1
                dest = parts / str(start)
                if dest.exists() and dest.stat().st_size == end - start + 1:
                    return dest
                temp = dest.with_suffix(".tmp")
                command = [
                    "curl",
                    *(["--proxy", DOWNLOAD_PROXY] if DOWNLOAD_PROXY else ["--noproxy", "*"]),
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
                    "--max-time",
                    str(TIMEOUT),
                    "--max-filesize",
                    str(end - start + 1),
                    "--range",
                    f"{start}-{end}",
                    "--output",
                    str(temp),
                    base + "/blobs/" + digest,
                ]
                subprocess.run(command, check=True)
                if temp.stat().st_size != end - start + 1:
                    raise ValueError("download_range_size_mismatch")
                temp.replace(dest)
                print(model, "range", start, "complete", flush=True)
                return dest

            with ThreadPoolExecutor(max_workers=LARGE_DOWNLOAD_WORKERS) as workers:
                ordered = list(workers.map(get_part, range(0, layer["size"], CHUNK_BYTES)))
            combined = partial.with_suffix(".assembled")
            with combined.open("wb") as out:
                for part in ordered:
                    with part.open("rb") as src:
                        shutil.copyfileobj(src, out)
            combined.replace(partial)
        else:
            subprocess.run(
                [
                    "curl",
                    *(["--proxy", DOWNLOAD_PROXY] if DOWNLOAD_PROXY else ["--noproxy", "*"]),
                    "--retry",
                    "4",
                    "--retry-all-errors",
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--proto",
                    "=https",
                    "--proto-redir",
                    "=https",
                    "--max-time",
                    str(TIMEOUT),
                    "--continue-at",
                    "-",
                    "--output",
                    str(partial),
                    base + "/blobs/" + digest,
                ],
                check=True,
            )
        partial.replace(target)
        if not valid():
            target.unlink()
            raise ValueError("layer_integrity_failed")

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch_layer, [manifest["config"], *manifest["layers"]]))
    for layer in [manifest["config"], *manifest["layers"]]:
        target = CACHE / "blobs" / layer["digest"].replace(":", "-")
        with target.open("rb") as stream:
            if (
                target.stat().st_size != layer["size"]
                or hashlib.file_digest(stream, "sha256").hexdigest() != layer["digest"].split(":")[1]
            ):
                raise ValueError("published_layer_integrity_failed")
        for suffix in (".partial", ".assembled", ".copy", ".parts"):
            extra = target.with_suffix(suffix)
            if extra.is_dir():
                shutil.rmtree(extra)
            else:
                extra.unlink(missing_ok=True)
    dest = CACHE / "manifests/registry.ollama.ai/library" / name / tag
    dest.parent.mkdir(parents=True, exist_ok=True)
    staged = dest.with_suffix(".tmp")
    staged.write_bytes(raw)
    staged.replace(dest)
    print(model, "verified", flush=True)


if __name__ == "__main__":
    for item in sys.argv[1:]:
        CACHE.mkdir(parents=True, exist_ok=True)
        with (CACHE / (item.replace(":", "-") + ".lock")).open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            download(item)
