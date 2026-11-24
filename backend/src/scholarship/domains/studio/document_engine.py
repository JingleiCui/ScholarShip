"""Local, inspectable PDF pipeline. No HTTP, model downloads or provider SDKs.

Native words / local OCR -> regions -> recursive reading order -> source blocks.
Figures remain image evidence; OCR is never advertised as diagram understanding.
"""

import csv
from collections import defaultdict
from html import escape
from io import BytesIO, StringIO
import re
from statistics import median
import shutil
import subprocess
import tempfile
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import ImageDraw, ImageFilter

from scholarship.adapters.providers import ContractError
from scholarship.domains.mineru_native import geometry, map_box

REVISION = "local-regions-ocr-v3"
# USER CONFIGURATION: task submission freezes these settings from model_config.py.
DEFAULT_POLICY = {
    "revision": REVISION,
    "ocr_language": "eng",
    "ocr_dpi": 220,
    "ocr_timeout_seconds": 90,
    "max_pages": 1000,
    "max_page_pixels": 24_000_000,
    "min_native_chars": 40,
    "min_ocr_confidence": 20,
    "max_model_tasks": 200,
    "max_asset_bytes": 134217728,
    "tesseract_command": "tesseract",
}


def policy_snapshot(overrides=None):
    policy = {**DEFAULT_POLICY, **(overrides or {})}
    if policy["revision"] not in {"local-regions-ocr-v1", "local-regions-ocr-v2", REVISION}:
        raise ContractError("unsupported_local_document_revision")
    bounds = {
        "ocr_dpi": (100, 300),
        "ocr_timeout_seconds": (5, 300),
        "max_pages": (1, 1000),
        "max_page_pixels": (1_000_000, 40_000_000),
        "min_native_chars": (1, 1000),
        "min_ocr_confidence": (0, 100),
        "max_model_tasks": (1, 500),
        "max_asset_bytes": (1048576, 268435456),
    }
    for key, (low, high) in bounds.items():
        if type(policy[key]) is not int or not low <= policy[key] <= high:
            raise ContractError("invalid_local_document_policy")
    if not re.fullmatch(r"[a-z_]+(?:\+[a-z_]+)*", policy["ocr_language"]):
        raise ContractError("invalid_ocr_language")
    if not isinstance(policy["tesseract_command"], str) or not policy["tesseract_command"]:
        raise ContractError("invalid_tesseract_command")
    return policy


def union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def inside(box, region, margin=1):
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return region[0] - margin <= x <= region[2] + margin and region[1] - margin <= y <= region[3] + margin


def reading_order(blocks):
    """Recursive whitespace cuts: columns first, horizontal bands around spanning regions."""
    if len(blocks) < 2:
        return blocks
    for axis, gap_min in ((0, 10), (1, 10)):
        intervals = sorted((b["bbox"][axis], b["bbox"][axis + 2]) for b in blocks)
        edge = intervals[0][1]
        gaps = []
        for lo, hi in intervals[1:]:
            if lo - edge > gap_min:
                gaps.append((lo - edge, (lo + edge) / 2))
            edge = max(edge, hi)
        if gaps:
            _, cut = max(gaps)
            first = [b for b in blocks if b["bbox"][axis + 2] <= cut]
            second = [b for b in blocks if b["bbox"][axis] >= cut]
            if first and second and len(first) + len(second) == len(blocks):
                return reading_order(first) + reading_order(second)
    return sorted(blocks, key=lambda b: (round(b["bbox"][1] / 3), b["bbox"][0]))


def text_blocks(words, method, legacy=False, paragraphs=False):
    lines = defaultdict(list)
    centers = []
    line_tolerance = median([w["bottom"] - w["top"] for w in words]) * 0.55 if words else 3
    for word in sorted(words, key=lambda w: ((w["top"] + w["bottom"]) / 2, w["x0"])):
        if legacy:
            key = (0, round(word["top"] / 3))
        elif word.get("line_id"):
            key = (1, *word["line_id"])
        else:
            center = (word["top"] + word["bottom"]) / 2
            match = next(
                (
                    i
                    for i, c in reversed(list(enumerate(centers)))
                    if abs(c - center)
                    <= (max(3, line_tolerance) if paragraphs else max(3, (word["bottom"] - word["top"]) * 0.4))
                ),
                None,
            )
            if match is None:
                match = len(centers)
                centers.append(center)
            key = (2, match)
        lines[key].append(word)
    blocks = []
    for _, row in sorted(lines.items()):
        pieces = [[]]
        for word in sorted(row, key=lambda w: w["x0"]):
            if pieces[-1] and word["x0"] - pieces[-1][-1]["x1"] > max(22, 2 * (word["bottom"] - word["top"])):
                pieces.append([])
            pieces[-1].append(word)
        for line in pieces:
            text = " ".join(w["text"] for w in line).replace("\x00", "\ufffd")
            if not text.strip():
                continue
            heading = bool(re.match(r"^\d+(?:\.\d+)*\s+[A-Z]", text)) or text in {
                "Abstract",
                "References",
                "Bibliography",
                "Introduction",
                "Conclusion",
            }
            blocks.append(
                {
                    "kind": "heading" if heading else "paragraph",
                    "text": text,
                    "bbox": union([[w["x0"], w["top"], w["x1"], w["bottom"]] for w in line]),
                    "method": method,
                    "confidence": min(w["confidence"] for w in line) if method == "tesseract-tsv" else None,
                }
            )
    result = []
    for b in reading_order(blocks):
        prev = result[-1] if result else None
        if (
            prev
            and b["kind"] == prev["kind"] == "paragraph"
            and abs(b["bbox"][0] - prev["bbox"][0]) < (24 if paragraphs else 12)
            and (-3 if paragraphs else 0) <= b["bbox"][1] - prev["bbox"][3] < 9
            and len(prev["text"]) + len(b["text"]) < 1800
        ):
            prev["text"] += "\n" + b["text"]
            prev["bbox"] = union([prev["bbox"], b["bbox"]])
            if prev["confidence"] is not None and b["confidence"] is not None:
                prev["confidence"] = min(prev["confidence"], b["confidence"])
        else:
            result.append(b)
    return result


def ocr_words(image, width, height, policy):
    command = shutil.which(policy["tesseract_command"])
    if not command:
        raise ContractError("local_ocr_not_installed")
    with tempfile.TemporaryDirectory(prefix="scholarship-ocr-") as tmp:
        path = Path(tmp) / "page.png"
        image.save(path)
        try:
            result = subprocess.run(
                [command, str(path), "stdout", "-l", policy["ocr_language"], "--psm", "3", "tsv"],
                capture_output=True,
                timeout=policy["ocr_timeout_seconds"],
                env={**__import__("os").environ, "OMP_THREAD_LIMIT": "1"},
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ContractError("local_ocr_timeout") from None
    if result.returncode:
        raise ContractError("local_ocr_failed_check_language")
    rows = []
    for row in csv.DictReader(StringIO(result.stdout.decode("utf-8", errors="replace")), delimiter="\t"):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        try:
            confidence = float(row["conf"])
            if confidence < policy["min_ocr_confidence"]:
                continue
            x, y, w, h = (float(row[k]) for k in ("left", "top", "width", "height"))
        except (ValueError, KeyError):
            raise ContractError("invalid_local_ocr_tsv") from None
        rows.append(
            {
                "text": row["text"],
                "x0": x * width / image.width,
                "x1": (x + w) * width / image.width,
                "top": y * height / image.height,
                "bottom": (y + h) * height / image.height,
                "confidence": confidence,
                "line_id": tuple(int(row[k]) for k in ("block_num", "par_num", "line_num")),
            }
        )
    return rows


def graphic_regions(image, words, width, height):
    """Conservative non-text ink regions. Candidates require visual review, not semantic acceptance."""
    small = image.convert("L")
    small.thumbnail((420, 600))
    sx, sy = small.width / width, small.height / height
    draw = ImageDraw.Draw(small)
    for w in words:
        draw.rectangle((w["x0"] * sx - 2, w["top"] * sy - 2, w["x1"] * sx + 2, w["bottom"] * sy + 2), fill=255)
    mask = small.point(lambda p: 255 if p < 150 else 0).filter(ImageFilter.MaxFilter(9))
    pixels = mask.load()
    seen = set()
    boxes = []
    for y in range(mask.height):
        for x in range(mask.width):
            if not pixels[x, y] or (x, y) in seen:
                continue
            stack = [(x, y)]
            seen.add((x, y))
            points = []
            while stack:
                px, py = stack.pop()
                points.append((px, py))
                for nx, ny in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                    if 0 <= nx < mask.width and 0 <= ny < mask.height and (nx, ny) not in seen and pixels[nx, ny]:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
            y0, y1 = min(p[1] for p in points), max(p[1] for p in points)
            if len(points) >= 180 and x1 - x0 >= 35 and y1 - y0 >= 25:
                boxes.append(
                    [
                        max(0, (x0 - 3) / sx),
                        max(0, (y0 - 3) / sy),
                        min(width, (x1 + 3) / sx),
                        min(height, (y1 + 3) / sy),
                    ]
                )
    return boxes


def vector_regions(page):
    """Group touching PDF drawing primitives, including diagrams not stored as raster images."""
    groups = []
    for item in [*page.rects, *page.curves, *page.lines]:
        box = [max(0, item["x0"]), max(0, item["top"]), min(page.width, item["x1"]), min(page.height, item["bottom"])]
        hits = [
            g
            for g in groups
            if not (box[2] + 8 < g[0][0] or g[0][2] + 8 < box[0] or box[3] + 8 < g[0][1] or g[0][3] + 8 < box[1])
        ]
        for g in hits:
            groups.remove(g)
        groups.append((union([box, *[g[0] for g in hits]]), 1 + sum(g[1] for g in hits)))
    return [
        b
        for b, count in groups
        if count >= 2
        and b[2] - b[0] >= 45
        and b[3] - b[1] >= 35
        and (b[2] - b[0]) * (b[3] - b[1]) < page.width * page.height * 0.8
    ]


def extract(path, policy, progress=lambda *_: None):
    policy = policy_snapshot(policy)
    pages, assets = [], {}
    with pdfplumber.open(path) as pdf, pdfium.PdfDocument(path) as rendered:
        if not 1 <= len(pdf.pages) <= policy["max_pages"]:
            raise ContractError("local_parse_page_limit")
        for n, page in enumerate(pdf.pages):
            progress("page_start", {"page": n + 1, "total": len(pdf.pages)})
            native = page.extract_words(x_tolerance=2, y_tolerance=3)
            usable = sum(len(w["text"].strip()) for w in native)
            invalid = sum(w["text"].count("\x00") + w["text"].count("\ufffd") for w in native)
            use_ocr = usable < policy["min_native_chars"] or invalid > max(2, usable * 0.02)
            scale = min(policy["ocr_dpi"] / 72, (policy["max_page_pixels"] / (page.width * page.height)) ** 0.5)
            rp = rendered[n]
            bitmap = rp.render(scale=scale)
            image = bitmap.to_pil().convert("RGB")
            bitmap.close()
            rp.close()
            method = "tesseract-tsv" if use_ocr else "pdf-text-layer"
            words = ocr_words(image, page.width, page.height, policy) if use_ocr else native
            tables = []
            if not use_ocr:
                for table in page.find_tables():
                    cells = table.extract()
                    if len(cells) < 2 or max((len(r) for r in cells), default=0) < 2:
                        continue
                    html = (
                        "<table>"
                        + "".join(
                            "<tr>" + "".join("<td>" + escape(str(v or "")) + "</td>" for v in r) + "</tr>"
                            for r in cells
                        )
                        + "</table>"
                    )
                    tables.append(
                        {
                            "kind": "table",
                            "text": html,
                            "bbox": list(table.bbox),
                            "method": "pdf-ruling-lines",
                            "confidence": None,
                        }
                    )
            figures = []
            for box in vector_regions(page):
                if not any(inside(box, t["bbox"]) for t in tables):
                    figures.append(box)
            for item in page.images:
                box = [item["x0"], item["top"], item["x1"], item["bottom"]]
                if (
                    box[2] - box[0] > 45
                    and box[3] - box[1] > 35
                    and (box[2] - box[0]) * (box[3] - box[1]) < page.width * page.height * 0.8
                ):
                    figures.append(box)
            for box in graphic_regions(image, words, page.width, page.height):
                if not any(inside(box, t["bbox"]) for t in tables) and not any(inside(box, b) for b in figures):
                    figures.append(box)
            blocks = text_blocks(
                [
                    w
                    for w in words
                    if not any(inside([w["x0"], w["top"], w["x1"], w["bottom"]], t["bbox"]) for t in tables)
                ],
                method,
                legacy=policy["revision"] == "local-regions-ocr-v1",
                paragraphs=policy["revision"] == REVISION,
            )
            if policy["revision"] == REVISION:
                for b in blocks:
                    if len(b["text"]) < 100 and (
                        b["bbox"][3] < page.height * 0.04 or b["bbox"][1] > page.height * 0.95
                    ):
                        b["kind"] = "other"
            blocks += tables
            for box in figures:
                box = [max(0, box[0]), max(0, box[1]), min(page.width, box[2]), min(page.height, box[3])]
                if box[2] <= box[0] or box[3] <= box[1]:
                    continue
                crop = image.crop(
                    (
                        int(box[0] * image.width / page.width),
                        int(box[1] * image.height / page.height),
                        int(box[2] * image.width / page.width),
                        int(box[3] * image.height / page.height),
                    )
                )
                crop.thumbnail((1500, 1500))
                raw = BytesIO()
                crop.save(raw, format="PNG")
                key = f"page-{n + 1}-figure-{len(assets)}"
                assets[key] = raw.getvalue()
                if sum(len(value) for value in assets.values()) > policy["max_asset_bytes"]:
                    raise ContractError("local_document_image_budget_exceeded")
                captions = [
                    b["text"]
                    for b in blocks
                    if re.match(r"^(Figure|Fig\.)\s*\d", b["text"], re.I) and 0 <= b["bbox"][1] - box[3] < 45
                ]
                blocks.append(
                    {
                        "kind": "figure",
                        "text": "\n".join(captions),
                        "bbox": box,
                        "asset": key,
                        "method": "local-image-region-candidate",
                        "confidence": None,
                    }
                )
            cropbox = list(map(float, page.page_obj.attrs.get("CropBox", page.page_obj.attrs["MediaBox"])))
            g = geometry(cropbox, int(page.rotation or 0), [page.width, page.height])
            for b in blocks:
                b["pdf_bbox"] = map_box(
                    [
                        b["bbox"][0] * 1000 / page.width,
                        b["bbox"][1] * 1000 / page.height,
                        b["bbox"][2] * 1000 / page.width,
                        b["bbox"][3] * 1000 / page.height,
                    ],
                    g,
                )[1]
                b["missing_glyphs"] = b["text"].count("\ufffd")
            pages.append(
                {
                    "number": n + 1,
                    "width": page.width,
                    "height": page.height,
                    "rotation": int(page.rotation or 0),
                    "crop_box": cropbox,
                    "ocr_performed": use_ocr,
                    "blocks": reading_order(blocks),
                }
            )
            progress("page_done", {"page": n + 1, "blocks": len(blocks), "ocr": use_ocr})
            image.close()
            page.close()
    return pages, assets
