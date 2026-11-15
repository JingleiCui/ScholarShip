"""Exact mapping between concatenated source windows and immutable PDF blocks."""


def source_map(rows, version_id, parse_id):
    text = []
    spans = []
    cursor = 0
    for b in rows:
        content = b["canonical_text"]
        if not content.strip():
            continue
        spans.append(
            {
                "global_start": cursor,
                "global_end": cursor + len(content),
                "text": content,
                "block_id": str(b["id"]),
                "version_id": str(version_id),
                "parse_id": str(parse_id),
                "page": b["page_no"],
                "pdf_bbox": b["source_locator"].get("pdf_bbox"),
            }
        )
        text.append(content)
        cursor += len(content) + 1
    return "\n".join(text), spans


def locate(spans, start, end):
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise ValueError("invalid_source_window")
    found = []
    for b in spans:
        a = max(start, b["global_start"])
        z = min(end, b["global_end"])
        if a >= z:
            continue
        first, last = a - b["global_start"], z - b["global_start"]
        found.append(
            {k: b[k] for k in ["block_id", "version_id", "parse_id", "page", "pdf_bbox"]}
            | {"start": first, "end": last, "quote": b["text"][first:last]}
        )
    return found
