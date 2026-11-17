"""Honest local text-layer structure, not OCR; immutable geometry and references."""

import re
from collections import defaultdict
import pdfplumber
import sqlalchemy as sa
from scholarship.adapters.providers import ContractError
from scholarship.api.library import stored_file
from scholarship.domains.deep_parse import version_scope
from scholarship.domains.mineru_native import map_box, geometry
from scholarship.domains.retrieval_profile import canonical, digest
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T

REVISION = "pdfplumber-lines-columns-v3"
MAX_PAGES = 1000


def parse_local(owner, project, version_id, *, run=None):
    with tenant_transaction(owner) as conn:
        v = version_scope(conn, version_id)
        if v["project_id"] != project:
            raise ContractError("version_project_mismatch")
        path = stored_file(conn, version_id)
        source_hash = digest(path.read_bytes())
    pages = []
    total_chars = 0
    references = False
    nul_replacements = 0
    with pdfplumber.open(path) as pdf:
        if not 1 <= len(pdf.pages) <= MAX_PAGES:
            raise ContractError("local_parse_page_limit")
        for number, page in enumerate(pdf.pages, 1):
            if run is not None:
                from scholarship.domains.cancellation import check_cancelled
                check_cancelled(owner, run)
            if page.rotation or page.cropbox != page.mediabox:
                raise ContractError("local_crop_rotation_requires_native_parser")
            words = page.extract_words(extra_attrs=["size", "fontname"], x_tolerance=2, y_tolerance=3)
            groups = defaultdict(list)
            # A two-column gutter must be visibly supported by words on both sides.
            mid = page.width / 2
            left = sum(w["x1"] < mid - 8 for w in words)
            right = sum(w["x0"] > mid + 8 for w in words)
            crossing = sum(w["x0"] < mid - 8 and w["x1"] > mid + 8 for w in words)
            columns = left > 30 and right > 30 and crossing < 5
            for w in words:
                col = 0 if not columns or w["x0"] < mid else 1
                groups[(col, round(w["top"] / 3))].append(w)
            blocks = []
            ordered = sorted(groups.items(), key=lambda item: (item[0][0], item[0][1]))
            for (column, _), line in ordered:
                line.sort(key=lambda w: w["x0"])
                text = " ".join(w["text"] for w in line)
                missing_glyphs = text.count("\x00")
                nul_replacements += missing_glyphs
                text = text.replace("\x00", "\ufffd")
                if not text.strip():
                    continue
                box = [
                    min(w["x0"] for w in line),
                    min(w["top"] for w in line),
                    max(w["x1"] for w in line),
                    max(w["bottom"] for w in line),
                ]
                if not 0 <= box[0] < box[2] <= page.width or not 0 <= box[1] < box[3] <= page.height:
                    continue
                if re.fullmatch(r"References|Bibliography", text, re.I):
                    references = True
                elif references and re.match(r"^(Appendix|[A-Z]\.?\s+[A-Z])", text):
                    references = False
                heading = bool(re.match(r"^\d+(?:\.\d+)*\s+[A-Z]", text)) or text in {
                    "Abstract",
                    "References",
                    "Conclusion",
                    "Introduction",
                    "Bibliography",
                }
                kind = (
                    "heading" if heading else "reference" if references and re.match(r"^\[\d+\]", text) else "paragraph"
                )
                pdfbox = map_box(
                    [
                        box[0] * 1000 / page.width,
                        box[1] * 1000 / page.height,
                        box[2] * 1000 / page.width,
                        box[3] * 1000 / page.height,
                    ],
                    geometry([0, 0, page.width, page.height], 0, [page.width, page.height]),
                )[1]
                # Join wrapped bibliography entries, retaining a conservative union bbox.
                previous = blocks[-1] if blocks else None
                join_paragraph = bool(
                    previous
                    and not references
                    and kind == previous["kind"] == "paragraph"
                    and previous["column"] == column
                    and len(previous["text"]) + len(text) < 800
                    and 0 <= box[1] - previous["bbox"][3] <= max(5, (box[3] - box[1]) * 0.7)
                )
                if (
                    references and previous and previous["kind"] == "reference" and kind == "paragraph"
                ) or join_paragraph:
                    old = blocks[-1]
                    old["missing_glyphs"] += missing_glyphs
                    old["text"] += " " + text
                    old["bbox"] = [
                        min(old["bbox"][0], box[0]),
                        min(old["bbox"][1], box[1]),
                        max(old["bbox"][2], box[2]),
                        max(old["bbox"][3], box[3]),
                    ]
                    old["pdf_bbox"] = [
                        min(old["pdf_bbox"][0], pdfbox[0]),
                        min(old["pdf_bbox"][1], pdfbox[1]),
                        max(old["pdf_bbox"][2], pdfbox[2]),
                        max(old["pdf_bbox"][3], pdfbox[3]),
                    ]
                else:
                    blocks.append(
                        {
                            "text": text,
                            "bbox": box,
                            "pdf_bbox": pdfbox,
                            "kind": kind,
                            "column": column,
                            "missing_glyphs": missing_glyphs,
                        }
                    )
                total_chars += len(text)
            pages.append(
                {
                    "number": number,
                    "width": page.width,
                    "height": page.height,
                    "blocks": blocks,
                    "columns_detected": columns,
                }
            )
            page.close()  # release cached layout before parsing the next textbook page
    if total_chars < 30:
        raise ContractError("no_text_layer_ocr_required")
    if run is not None:
        check_cancelled(owner, run)
    config = digest(canonical({"source": source_hash, "mapper": REVISION}))
    with tenant_transaction(owner) as conn:
        conn.execute(
            sa.select(T["paper_versions"].c.id).where(T["paper_versions"].c.id == version_id).with_for_update()
        )
        v = version_scope(conn, version_id)
        old = conn.scalar(
            sa.select(T["document_parses"].c.id).where(
                T["document_parses"].c.version_id == version_id,
                T["document_parses"].c.config_sha256 == config,
                T["document_parses"].c.parser_name == "pdfplumber-structure",
            )
        )
        if old:
            return {"parse_id": str(old), "cached": True, "ocr_performed": False}
        scope = {"owner_id": owner, "project_id": project}
        rev = 1 + conn.scalar(
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
                parser_name="pdfplumber-structure",
                parser_version=REVISION,
                config_sha256=config,
                revision_no=rev,
                mode="deep",
                status="processing",
                expected_pages=len(pages),
                completed_pages=0,
            )
            .returning(T["document_parses"].c.id)
        )
        text = []
        for page in pages:
            page_id = conn.scalar(
                T["document_pages"]
                .insert()
                .values(
                    **scope,
                    parse_id=pid,
                    page_no=page["number"],
                    width=page["width"],
                    height=page["height"],
                    rotation=0,
                    status="ready",
                    text_layer_chars=sum(len(b["text"]) for b in page["blocks"]),
                    page_sha256=digest(canonical(page)),
                )
                .returning(T["document_pages"].c.id)
            )
            for ordinal, b in enumerate(page["blocks"]):
                text.append(b["text"])
                level = (
                    1 + len(re.match(r"^(\d+(?:\.\d+)*)", b["text"])[1].split("."))
                    if b["kind"] == "heading" and re.match(r"^\d+", b["text"])
                    else 1
                )
                bid = conn.scalar(
                    T["document_blocks"]
                    .insert()
                    .values(
                        **scope,
                        parse_id=pid,
                        page_id=page_id,
                        ordinal=ordinal,
                        kind=b["kind"],
                        raw_text=b["text"],
                        canonical_text=b["text"],
                        content_sha256=digest(b["text"]),
                        locator_precision="bbox",
                        bbox=b["bbox"],
                        source_locator={
                            "mapping": REVISION,
                            "source_sha256": source_hash,
                            "fixture": False,
                            "ocr_performed": False,
                            "pdf_bbox": b["pdf_bbox"],
                            "page_idx": page["number"] - 1,
                        },
                        structured_json={
                            "heading_level": level if b["kind"] == "heading" else None,
                            "reading_order": "column_line_heuristic_unverified",
                            "semantic_classification": "heuristic",
                            "nul_glyph_replacements": b["missing_glyphs"],
                        },
                    )
                    .returning(T["document_blocks"].c.id)
                )
                if b["kind"] == "reference":
                    marker = re.match(r"^\[(\d+)\]", b["text"])[1]
                    conn.execute(
                        T["bibliographic_refs"]
                        .insert()
                        .values(
                            **scope,
                            parse_id=pid,
                            entry_block_id=bid,
                            marker=marker,
                            raw_reference=b["text"],
                            metadata_json={"source": "local_text_heuristic"},
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
                manifest_sha256=digest(canonical(pages)),
                source_text_sha256=digest("\n".join(text)),
                published_at=sa.func.now(),
            )
        )
    return {
        "parse_id": str(pid),
        "ocr_performed": False,
        "pages": len(pages),
        "text_chars": total_chars,
        "limitations": "本地文本层/分栏/行级启发式；未验收作者机构、公式与表格语义、阅读顺序。空文本页未冒充 OCR。",
        "empty_text_pages": [p["number"] for p in pages if not p["blocks"]],
        "external_requests": 0,
        "nul_glyph_replacements": nul_replacements,
        "quality_warning": "PDF 中 NUL 字形已明确替换为 �；这些位置不能作为已识别的公式或精确字形。"
        if nul_replacements
        else None,
    }
