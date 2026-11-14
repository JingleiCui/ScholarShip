"""Versioned MinerU 3.4.4 mapping. No network, extraction or HTML execution."""

from io import BytesIO
import json
import math
from pathlib import PurePosixPath
import re
import zipfile

import pdfplumber
import sqlalchemy as sa
from scholarship.adapters.providers import ContractError
from scholarship.domains.deep_parse import persist_file, version_scope
from scholarship.domains.retrieval_profile import canonical, digest
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T

MAPPING = "mineru-native-3.4.4-content1000-v2"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 2000
MAX_BLOCKS = 20000
KINDS = {
    "text": "paragraph",
    "equation": "formula",
    "table": "table",
    "image": "figure",
    "chart": "figure",
    "ref_text": "reference",
    "aside_text": "other",
    "page_footnote": "paragraph",
    "page_number": "other",
}


def geometry(crop, rotation, native_size):
    """MinerU renders the rotated CropBox. Store PDF.js absolute PDF user units."""
    if (
        len(crop) != 4
        or len(native_size) != 2
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in [*crop, *native_size])
    ):
        raise ContractError("invalid_pdf_geometry")
    x0, y0, x1, y1 = crop
    w, h = x1 - x0, y1 - y0
    dw, dh = (h, w) if rotation in (90, 270) else (w, h)
    if rotation not in (0, 90, 180, 270) or min(w, h) <= 0:
        raise ContractError("invalid_pdf_geometry")
    if len(native_size) != 2 or any(abs(a - b) > max(2, b * 0.005) for a, b in zip(native_size, (dw, dh))):
        raise ContractError("mineru_pdf_page_size_mismatch")
    return {"crop_box": list(crop), "rotation": rotation, "width": dw, "height": dh}


def map_box(box, g):
    if (
        not isinstance(box, list)
        or len(box) != 4
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
        or not 0 <= box[0] < box[2] <= 1000
        or not 0 <= box[1] < box[3] <= 1000
    ):
        raise ContractError("invalid_mineru_normalized_bbox")
    displayed = [
        box[0] * g["width"] / 1000,
        box[1] * g["height"] / 1000,
        box[2] * g["width"] / 1000,
        box[3] * g["height"] / 1000,
    ]
    x0, y0, x1, y1 = g["crop_box"]
    w, h = x1 - x0, y1 - y0

    def inverse(x, y):
        r = g["rotation"]
        u, v = {0: (x, h - y), 90: (y, x), 180: (w - x, y), 270: (w - y, h - x)}[r]
        return x0 + u, y0 + v

    points = [inverse(x, y) for x in (displayed[0], displayed[2]) for y in (displayed[1], displayed[3])]
    return displayed, [
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    ]


def normalize(raw, pdf_raw):
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ContractError("mineru_archive_size_limit")
    try:
        with zipfile.ZipFile(BytesIO(raw)) as z:
            infos = z.infolist()
            names = [i.filename for i in infos]
            if (
                len(infos) > MAX_MEMBERS
                or len(set(names)) != len(names)
                or sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES
                or any(PurePosixPath(n).is_absolute() or ".." in PurePosixPath(n).parts or "\\" in n for n in names)
                or any((i.external_attr >> 16) & 0o170000 == 0o120000 for i in infos)
            ):
                raise ContractError("unsafe_mineru_archive")
            lists = [n for n in names if n.endswith("_content_list.json") and "/" not in n]
            if len(lists) != 1 or "layout.json" not in names:
                raise ContractError("mineru_native_members_required")
            layout = json.loads(z.read("layout.json"))
            content = json.loads(z.read(lists[0]))
            if layout.get("_version_name") != "3.4.4" or not isinstance(content, list) or len(content) > MAX_BLOCKS:
                raise ContractError("unverified_mineru_native_version")
            native = layout["pdf_info"]
            with pdfplumber.open(BytesIO(pdf_raw)) as pdf:
                if len(native) != len(pdf.pages) or [p["page_idx"] for p in native] != list(range(len(native))):
                    raise ContractError("mineru_full_page_coverage_required")
                pages = []
                for i, p in enumerate(pdf.pages):
                    if p.page_obj.attrs.get("UserUnit", 1) != 1:
                        raise ContractError("pdf_user_unit_requires_verified_mapping")
                    # pdfplumber cropbox is in top-origin coordinates; pdfminer retains PDF user space.
                    crop = list(map(float, p.page_obj.attrs.get("CropBox", p.page_obj.attrs["MediaBox"])))
                    g = geometry(crop, int(p.rotation or 0), native[i]["page_size"])
                    pages.append({**g, "number": i + 1, "blocks": []})
            assets = {}
            first_heading = True
            previous_page = -1
            for ordinal, b in enumerate(content):
                if (
                    type(b.get("page_idx")) is not int
                    or not 0 <= b["page_idx"] < len(pages)
                    or b.get("type") not in KINDS
                ):
                    raise ContractError("invalid_mineru_block")
                if b["page_idx"] < previous_page:
                    raise ContractError("mineru_reading_order_page_regression")
                previous_page = b["page_idx"]
                page = pages[b["page_idx"]]
                bbox, pdfbox = map_box(b.get("bbox"), page)
                kind = KINDS[b["type"]]
                native_level = b.get("text_level")
                level = native_level
                if level is not None:
                    if type(level) is not int or not 1 <= level <= 6:
                        raise ContractError("invalid_mineru_heading_level")
                    kind = "title" if first_heading and b["page_idx"] == 0 and level == 1 else "heading"
                    first_heading = False
                    section_number = re.match(r"^(\d+(?:\.\d+)*)(?:\s|$)", b.get("text", ""))
                    if section_number:
                        level = min(6, 1 + len(section_number[1].split(".")))
                text = b.get("text", "")
                if b["type"] in ("table", "image", "chart"):
                    prefix = b["type"]
                    fragments = (
                        b.get(prefix + "_caption", [])
                        + [b.get("table_body", b.get("content", ""))]
                        + b.get(prefix + "_footnote", [])
                    )
                    if any(not isinstance(s, str) for s in fragments):
                        raise ContractError("invalid_mineru_asset_text")
                    text = "\n".join(s for s in fragments if s)
                if not isinstance(text, str) or "\x00" in text or len(text.encode()) > 1200000:
                    raise ContractError("invalid_mineru_text")
                asset = b.get("img_path")
                if asset:
                    if (
                        not isinstance(asset, str)
                        or not re.fullmatch(r"images/[a-f0-9]+\.(jpg|png|jpeg)", asset)
                        or asset not in names
                    ):
                        raise ContractError("invalid_mineru_asset_path")
                    assets[asset] = z.read(asset)
                    if not (
                        assets[asset].startswith(b"\xff\xd8\xff") or assets[asset].startswith(b"\x89PNG\r\n\x1a\n")
                    ):
                        raise ContractError("invalid_mineru_image_signature")
                page["blocks"].append(
                    {
                        "ordinal": len(page["blocks"]),
                        "kind": kind,
                        "text": text,
                        "bbox": bbox,
                        "pdf_bbox": pdfbox,
                        "native_ordinal": ordinal,
                        "native_bbox": b["bbox"],
                        "level": level,
                        "native_level": native_level,
                        "native_type": b["type"],
                        "asset": asset,
                        "table_html": b.get("table_body"),
                    }
                )
            return pages, assets
    except ContractError:
        raise
    except (KeyError, ValueError, TypeError, zipfile.BadZipFile, OSError):
        raise ContractError("invalid_mineru_native_archive") from None


def import_native(owner, version_id, raw, *, expected_source_sha256):
    """Publish only when the original matches the approved job source fingerprint."""
    from scholarship.api.library import stored_file

    with tenant_transaction(owner) as conn:
        version_scope(conn, version_id)
        pdf_raw = stored_file(conn, version_id).read_bytes()
    if digest(pdf_raw) != expected_source_sha256:
        raise ContractError("mineru_source_hash_mismatch")
    pages, assets = normalize(raw, pdf_raw)
    config = digest(canonical({"mapping": MAPPING, "raw": digest(raw), "source": expected_source_sha256}))
    with tenant_transaction(owner) as conn:
        conn.execute(
            sa.select(T["paper_versions"].c.id).where(T["paper_versions"].c.id == version_id).with_for_update()
        )
        v = version_scope(conn, version_id)
        if v["metadata_json"].get("page_count") != len(pages):
            raise ContractError("source_page_count_mismatch")
        old = conn.scalar(
            sa.select(T["document_parses"].c.id).where(
                T["document_parses"].c.version_id == version_id, T["document_parses"].c.config_sha256 == config
            )
        )
        if old:
            return old
        scope = {"owner_id": owner, "project_id": v["project_id"]}
        fid = persist_file(conn, owner, raw, "zip", "application/zip")
        asset_ids = {
            n: persist_file(
                conn, owner, data, n.rsplit(".", 1)[1], "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
            )
            for n, data in assets.items()
        }
        revision = 1 + conn.scalar(
            sa.select(sa.func.coalesce(sa.func.max(T["document_parses"].c.revision_no), 0)).where(
                T["document_parses"].c.version_id == version_id
            )
        )
        pid = conn.scalar(
            T["document_parses"]
            .insert()
            .values(
                **scope,
                version_id=version_id,
                parser_name="mineru-native",
                parser_version=MAPPING,
                config_sha256=config,
                revision_no=revision,
                mode="deep",
                status="processing",
                expected_pages=len(pages),
                completed_pages=0,
            )
            .returning(T["document_parses"].c.id)
        )
        headings = []
        texts = []
        for p in pages:
            page_id = conn.scalar(
                T["document_pages"]
                .insert()
                .values(
                    **scope,
                    parse_id=pid,
                    page_no=p["number"],
                    width=p["width"],
                    height=p["height"],
                    rotation=p["rotation"],
                    status="ready",
                    text_layer_chars=sum(len(b["text"]) for b in p["blocks"]),
                    page_sha256=digest(canonical(p)),
                )
                .returning(T["document_pages"].c.id)
            )
            for b in p["blocks"]:
                texts.append(b["text"])
                level = b["level"]
                if level:
                    while headings and headings[-1][0] >= level:
                        headings.pop()
                bid = conn.scalar(
                    T["document_blocks"]
                    .insert()
                    .values(
                        **scope,
                        parse_id=pid,
                        page_id=page_id,
                        parent_block_id=headings[-1][1] if headings else None,
                        ordinal=b["ordinal"],
                        kind=b["kind"],
                        raw_text=b["text"],
                        canonical_text=b["text"],
                        content_sha256=digest(b["text"]),
                        locator_precision="bbox",
                        bbox=b["bbox"],
                        asset_file_id=asset_ids.get(b["asset"]),
                        source_locator={
                            "fixture": False,
                            "mapping": MAPPING,
                            "raw_file_id": str(fid),
                            "raw_sha256": digest(raw),
                            "source_sha256": expected_source_sha256,
                            "page_idx": p["number"] - 1,
                            "native_ordinal": b["native_ordinal"],
                            "native_bbox": b["native_bbox"],
                            "native_bbox_units": "normalized_1000",
                            "pdf_bbox": b["pdf_bbox"],
                            "crop_box": p["crop_box"],
                            "rotation": p["rotation"],
                        },
                        structured_json={
                            "heading_level": level,
                            "native_heading_level": b["native_level"],
                            "heading_policy": "numeric-section-depth-v1",
                            "native_type": b["native_type"],
                            "table_html": b["table_html"],
                        },
                    )
                    .returning(T["document_blocks"].c.id)
                )
                if level:
                    headings.append((level, bid))
                if b["kind"] == "reference":
                    marker = re.match(r"^\[([^]]+)\]", b["text"])
                    conn.execute(
                        T["bibliographic_refs"]
                        .insert()
                        .values(
                            **scope,
                            parse_id=pid,
                            entry_block_id=bid,
                            marker=marker[1] if marker else str(b["native_ordinal"]),
                            raw_reference=b["text"],
                            metadata_json={"source": "mineru-unverified"},
                            resolution_state="unresolved",
                        )
                    )
        conn.execute(
            T["document_parses"]
            .update()
            .where(T["document_parses"].c.id == pid)
            .values(
                status="ready",
                completed_pages=len(pages),
                manifest_sha256=digest(raw),
                source_text_sha256=digest("\n".join(texts)),
                published_at=sa.func.now(),
            )
        )
        conn.execute(
            T["audit_events"]
            .insert()
            .values(
                **scope,
                action="native_parse_published",
                subject_json={"parse_id": str(pid)},
                safe_payload={
                    "raw_file_id": str(fid),
                    "raw_sha256": digest(raw),
                    "source_sha256": expected_source_sha256,
                    "mapping": MAPPING,
                },
            )
        )
        return pid


def combine_parts(parts, pdf_raw):
    """Offline assembly of complete, nonoverlapping page ranges (e.g. 541 pages).

    Each part is validated against the exact source PDF slice before remapping.
    Original ZIPs remain embedded; this never uploads or invokes a parser.
    """
    from pypdf import PdfReader, PdfWriter

    if sum(len(raw) for _, _, raw in parts) > MAX_ARCHIVE_BYTES:
        raise ContractError("native_parts_size_limit")
    reader = PdfReader(BytesIO(pdf_raw))
    count = len(reader.pages)
    expected = 1
    content = []
    page_info = []
    assets = {}
    originals = []
    manifest = []
    for start, end, raw in sorted(parts, key=lambda item: item[0]):
        if (
            type(start) is not int
            or type(end) is not int
            or start != expected
            or end < start
            or end > count
            or end - start + 1 > 200
        ):
            raise ContractError("invalid_native_part_coverage")
        writer = PdfWriter()
        for p in reader.pages[start - 1 : end]:
            writer.add_page(p)
        buf = BytesIO()
        writer.write(buf)
        normalize(raw, buf.getvalue())
        with zipfile.ZipFile(BytesIO(raw)) as z:
            layout = json.loads(z.read("layout.json"))
            rows = json.loads(
                z.read(next(n for n in z.namelist() if n.endswith("_content_list.json") and "/" not in n))
            )
            if len(layout["pdf_info"]) != end - start + 1:
                raise ContractError("native_part_page_count_mismatch")
            for row in rows:
                row["page_idx"] += start - 1
                content.append(row)
            for p in layout["pdf_info"]:
                p["page_idx"] += start - 1
                page_info.append(p)
            for name in z.namelist():
                if name.startswith("images/"):
                    data = z.read(name)
                    if name in assets and assets[name] != data:
                        raise ContractError("native_part_asset_collision")
                    assets[name] = data
        originals.append(raw)
        manifest.append({"start": start, "end": end, "sha256": digest(raw)})
        expected = end + 1
    if expected != count + 1:
        raise ContractError("full_native_part_coverage_required")
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("layout.json", canonical({"_version_name": "3.4.4", "pdf_info": page_info}))
        z.writestr("combined_content_list.json", canonical(content))
        z.writestr("parts_manifest.json", canonical({"source_sha256": digest(pdf_raw), "parts": manifest}))
        for i, raw in enumerate(originals):
            z.writestr(f"parts/{i + 1}.zip", raw)
        for name, data in assets.items():
            z.writestr(name, data)
    result = output.getvalue()
    normalize(result, pdf_raw)
    return result
