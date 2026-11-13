"""Versioned offline profile. These vectors cannot be mixed with live embeddings."""

import hashlib
import json
import re
import unicodedata

# USER CONFIGURATION: isolated offline profile; live vendor tokenizer is not asserted.
PROFILE_KEY = "offline-hash-1024-v1"
PROFILE = {
    "mode": "synthetic",
    "provider": "offline",
    "model": "hash-bow-sha256-v1",
    "dimensions": 1024,
    "metric": "cosine",
    "tokenizer": "utf8-bytes-v1",
    "target_tokens": 600,
    "max_tokens": 900,
    "overlap_tokens": 80,
    "chunker": "block-unicode-boundary-v1",
    "lexical": "nfkc-word-cjk-bigram-v1",
    "document_prefix": "",
    "query_prefix": "",
    "asymmetric": False,
    "normalization": "original-unmodified",
    "rrf_k": 60,
}


def digest(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


PROFILE_HASH = digest(canonical(PROFILE))


def lexical(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    aliases = text.replace("β", " beta ").replace("α", " alpha ")
    words = re.findall(r"[a-z0-9_]+", aliases)
    for segment in re.findall(r"[\u3400-\u9fff]+", text):
        words.extend(segment[i : i + 2] for i in range(max(1, len(segment) - 1)))
    return " ".join(words)


def chunks(text, kind="paragraph"):
    """Yield exact Unicode slices with UTF-8 byte counts, never decode split bytes."""
    if kind in {"formula", "table", "code"} and len(text.encode()) > PROFILE["max_tokens"]:
        return  # Preserve the source block; no invented verbatim representation.
    start = 0
    while start < len(text):
        end, size = start, 0
        while end < len(text) and size + len(text[end].encode()) <= PROFILE["target_tokens"]:
            size += len(text[end].encode())
            end += 1
        if end == start:
            raise ValueError("invalid_chunk_limit")
        if text[start:end].strip():
            yield start, end, text[start:end], size
        if end == len(text):
            break
        overlap, next_start = 0, end
        while next_start > start + 1 and overlap + len(text[next_start - 1].encode()) <= PROFILE["overlap_tokens"]:
            next_start -= 1
            overlap += len(text[next_start].encode())
        start = next_start
