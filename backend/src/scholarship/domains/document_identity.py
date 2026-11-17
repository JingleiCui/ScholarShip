"""Bounded local content similarity for review, never an automatic identity merge."""

import hashlib
import re
import unicodedata

SIGNATURE_SIZE = 256
MIN_WORDS = 80


def fingerprint(text):
    words = re.findall(r"[\w]+", unicodedata.normalize("NFKC", text).casefold())
    if len(words) < MIN_WORDS:
        return []
    hashes = {hashlib.sha256(" ".join(words[i : i + 5]).encode()).hexdigest()[:16] for i in range(len(words) - 4)}
    return sorted(hashes)[:SIGNATURE_SIZE]


def similarity(a, b):
    if not a or not b:
        return 0.0
    # Compare the same bottom-k universe; differing document lengths do not change the threshold.
    threshold = min(max(a), max(b))
    left, right = {v for v in a if v <= threshold}, {v for v in b if v <= threshold}
    return len(left & right) / max(1, len(left | right))


def inspect_content(pdf):
    texts = [(p.extract_text() or "")[:30000] for p in pdf.pages[:3]]
    first = texts[0] if texts else ""
    candidates = []

    def visitor(text, cm, tm, font, size):
        cleaned = " ".join(text.split())
        if 10 <= len(cleaned) <= 250 and len(re.findall(r"[A-Za-z]", cleaned)) >= 8 and "@" not in cleaned:
            candidates.append((float(size), float(tm[5]), cleaned))

    if pdf.pages:
        pdf.pages[0].extract_text(visitor_text=visitor)
    title = str((pdf.metadata or {}).get("/Title", "")).strip()
    if not title and candidates:
        largest = max(x[0] for x in candidates)
        lines = [x for x in candidates if x[0] >= largest - 0.1]
        title = " ".join(x[2] for x in sorted(lines, key=lambda x: -x[1]))[:2000]
    match = re.search(r"arXiv\s*:\s*(\d{4}\.\d{4,5})v(\d+)", first, re.I)
    return {
        "title": title,
        "content_signature": fingerprint(" ".join(texts)),
        "signature_scope": "first-three-pages-text-v1",
        "signature_chars": sum(map(len, texts)),
        "arxiv_revision": {"id": match[1], "version": int(match[2])} if match else None,
        "first_page": first,
    }
