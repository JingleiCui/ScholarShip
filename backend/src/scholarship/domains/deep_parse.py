"""Explicit synthetic-layout mapping; never guesses the unverified cloud schema."""

from io import BytesIO
import math
import os
from pathlib import Path
from uuid import uuid4
import zipfile

import sqlalchemy as sa
from fastapi import HTTPException

from scholarship.adapters.bundles import inspect_bundle
from scholarship.adapters.providers import ContractError
from scholarship.config import settings
from scholarship.domains.retrieval_profile import canonical, digest
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T

# USER CONFIGURATION
MAX_BLOCKS = 10000
MAX_TEXT_BYTES = 1200000
FIXTURE_SCHEMA = "scholarship.synthetic-layout/v1"


def persist_file(conn, owner, raw, suffix, mime):
    sha = digest(raw)
    old = conn.execute(sa.select(T["file_objects"]).where(T["file_objects"].c.sha256 == sha)).mappings().first()
    if old:
        return old["id"]
    root = Path(settings().scholar_data_dir) / "files"
    key = f"{owner}/{sha}.{suffix}"
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + uuid4().hex)
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return conn.scalar(
        T["file_objects"]
        .insert()
        .values(owner_id=owner, sha256=sha, byte_size=len(raw), mime_type=mime, storage_key=key, state="ready")
        .returning(T["file_objects"].c.id)
    )


def fixture_bundle(pages):
    doc = {
        "schema": "docvortex.middle",
        "schema_version": "2.0",
        "metadata": {"producer": {"version": "synthetic-v1"}, "fixture_schema": FIXTURE_SCHEMA},
        "pages": [
            {
                "page_idx": i,
                "blocks": [
                    {"type": "paragraph", "text": t, "bbox": [40, 50 + j * 45, 550, 90 + j * 45]}
                    for j, t in enumerate(texts)
                ],
            }
            for i, texts in enumerate(pages)
        ],
        "extensions": {
            "docvortex_layout": {
                "version": 1,
                "pages": [{"page_idx": i, "width_pt": 600, "height_pt": 800} for i in range(len(pages))],
            }
        },
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        member = zipfile.ZipInfo("middle_json.json", date_time=(2026, 1, 1, 0, 0, 0))
        member.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(member, canonical(doc))
    return output.getvalue()


def normalize(raw, expected_pages):
    inspected = inspect_bundle(raw, expected_pages)
    doc = inspected.document
    if doc["metadata"].get("fixture_schema") != FIXTURE_SCHEMA:
        raise ContractError("only_explicit_synthetic_mapping_is_publishable_offline")
    sizes = {p["page_idx"] + 1: (p["width_pt"], p["height_pt"]) for p in doc["extensions"]["docvortex_layout"]["pages"]}
    pages = []
    total = 0
    kinds = {"paragraph", "heading", "title", "table", "formula", "code", "reference", "figure", "other"}
    for page in doc["pages"]:
        number = page["page_idx"] + 1
        w, h = sizes[number]
        blocks = []
        for ordinal, b in enumerate(page["blocks"]):
            total += 1
            if total > MAX_BLOCKS or b.get("type") not in kinds or not isinstance(b.get("text"), str):
                raise ContractError("invalid_synthetic_block")
            text = b["text"]
            box = b.get("bbox")
            if len(text.encode()) > MAX_TEXT_BYTES or "\x00" in text:
                raise ContractError("invalid_block_text")
            if (
                not isinstance(box, list)
                or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                or not 0 <= box[0] < box[2] <= w
                or not 0 <= box[1] < box[3] <= h
            ):
                raise ContractError("invalid_block_bbox")
            blocks.append({"ordinal": ordinal, "kind": b["type"], "text": text, "bbox": box})
        pages.append({"number": number, "width": w, "height": h, "blocks": blocks})
    return inspected, pages


def version_scope(conn, version_id):
    row = (
        conn.execute(
            sa.select(T["paper_versions"])
            .join(T["papers"], T["papers"].c.id == T["paper_versions"].c.paper_id)
            .where(
                T["paper_versions"].c.id == version_id,
                T["paper_versions"].c.deleted_at.is_(None),
                T["papers"].c.deleted_at.is_(None),
            )
        )
        .mappings()
        .first()
    )
    if not row:
        raise HTTPException(404, "version_not_found")
    return row


def import_synthetic(owner, version_id, raw, expected_pages):
    inspected, pages = normalize(raw, expected_pages)
    config_hash = digest(
        canonical({"mapping": FIXTURE_SCHEMA, "raw": inspected.sha256, "pages": sorted(expected_pages)})
    )
    with tenant_transaction(owner) as conn:
        conn.execute(
            sa.select(T["paper_versions"].c.id).where(T["paper_versions"].c.id == version_id).with_for_update()
        )
        v = version_scope(conn, version_id)
        count = v["metadata_json"].get("page_count")
        if not isinstance(count, int) or set(expected_pages) != set(range(1, count + 1)):
            raise ContractError("full_source_page_coverage_required")
        old = (
            conn.execute(
                sa.select(T["document_parses"]).where(
                    T["document_parses"].c.version_id == version_id,
                    T["document_parses"].c.config_sha256 == config_hash,
                    T["document_parses"].c.parser_name == "synthetic-deep",
                )
            )
            .mappings()
            .first()
        )
        if old:
            return old["id"]
        scope = {"owner_id": owner, "project_id": v["project_id"]}
        fid = persist_file(conn, owner, raw, "zip", "application/zip")
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
                parser_name="synthetic-deep",
                parser_version=FIXTURE_SCHEMA,
                config_sha256=config_hash,
                revision_no=revision,
                mode="deep",
                status="processing",
                expected_pages=len(pages),
                completed_pages=0,
            )
            .returning(T["document_parses"].c.id)
        )
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
                    rotation=0,
                    status="ready",
                    text_layer_chars=sum(len(b["text"]) for b in p["blocks"]),
                    page_sha256=digest(canonical(p)),
                )
                .returning(T["document_pages"].c.id)
            )
            for b in p["blocks"]:
                texts.append(b["text"])
                conn.execute(
                    T["document_blocks"]
                    .insert()
                    .values(
                        **scope,
                        parse_id=pid,
                        page_id=page_id,
                        ordinal=b["ordinal"],
                        kind=b["kind"],
                        raw_text=b["text"],
                        canonical_text=b["text"],
                        content_sha256=digest(b["text"]),
                        locator_precision="bbox",
                        bbox=b["bbox"],
                        source_locator={
                            "fixture": True,
                            "raw_file_id": str(fid),
                            "raw_sha256": inspected.sha256,
                            "page_idx": p["number"] - 1,
                            "mapping": FIXTURE_SCHEMA,
                        },
                        structured_json={},
                    )
                )
        conn.execute(
            T["document_parses"]
            .update()
            .where(T["document_parses"].c.id == pid)
            .values(
                status="ready",
                completed_pages=len(pages),
                manifest_sha256=inspected.sha256,
                source_text_sha256=digest("\n".join(texts)),
                published_at=sa.func.now(),
            )
        )
        conn.execute(
            T["audit_events"]
            .insert()
            .values(
                **scope,
                action="synthetic_parse_published",
                subject_json={"parse_id": str(pid)},
                safe_payload={"raw_file_id": str(fid), "raw_sha256": inspected.sha256, "mapping": FIXTURE_SCHEMA},
            )
        )
        return pid
