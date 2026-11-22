"""Image evidence is a separate immutable artifact, never a fabricated OCR block."""

import base64
from pathlib import Path
from uuid import UUID
import sqlalchemy as sa
from scholarship.adapters.providers import ContractError
from scholarship.config import settings
from scholarship.domains.deep_parse import version_scope
from scholarship.domains.retrieval_profile import digest, canonical
from scholarship.domains.studio.artifacts import publish
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.models.figure_contract import MAX_IMAGE_BYTES, image_data


def source(conn, block_id):
    b, p, f, page = (T[n] for n in ("document_blocks", "document_parses", "file_objects", "document_pages"))
    row = (
        conn.execute(
            sa.select(
                b.c.id,
                b.c.parse_id,
                b.c.project_id,
                b.c.canonical_text,
                b.c.bbox,
                p.c.version_id,
                p.c.status.label("parse_status"),
                page.c.page_no,
                f.c.id.label("file_id"),
                f.c.storage_key,
                f.c.sha256,
                f.c.byte_size,
                f.c.mime_type,
            )
            .join(p, p.c.id == b.c.parse_id)
            .join(page, page.c.id == b.c.page_id)
            .join(f, f.c.id == b.c.asset_file_id)
            .where(b.c.id == block_id, b.c.kind == "figure", f.c.state == "ready")
        )
        .mappings()
        .first()
    )
    if not row or row["parse_status"] != "ready":
        raise ContractError("ready_figure_image_required")
    version_scope(conn, row["version_id"])
    return dict(row)


def describe(owner, project, run, inputs, gateway):
    with tenant_transaction(owner) as conn:
        row = source(conn, UUID(inputs["block_ids"][0]))
    if row["project_id"] != project:
        raise ContractError("figure_project_mismatch")
    root = (Path(settings().scholar_data_dir) / "files").resolve()
    path = (root / row["storage_key"]).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size != row["byte_size"]:
        raise ContractError("figure_source_integrity")
    if row["byte_size"] > MAX_IMAGE_BYTES:
        raise ContractError("figure_image_size_limit")
    raw = path.read_bytes()
    if digest(raw) != row["sha256"]:
        raise ContractError("figure_source_integrity")
    image_data(raw, row["mime_type"])
    receipt = gateway.invoke(
        "figure:" + str(row["id"]),
        "chat",
        "describe_figure",
        {"image_base64": base64.b64encode(raw).decode(), "mime": row["mime_type"], "caption": row["canonical_text"]},
    )
    node_receipt = None
    selection = getattr(gateway, "selection", None)
    strategy = (
        (selection.bindings or {}).get("chat", {}).get("definition", {}).get("figure_policy") if selection else None
    )
    if strategy == "nodes_then_edges" and receipt["value"]["nodes"]:
        node_receipt = receipt
        receipt = gateway.invoke(
            "figure-edges:" + str(row["id"]),
            "chat",
            "describe_figure_edges",
            {
                "image_base64": base64.b64encode(raw).decode(),
                "mime": row["mime_type"],
                "caption": row["canonical_text"],
                "description": node_receipt["value"],
            },
        )
    result = {
        "description": receipt["value"],
        "source": {
            "block_id": str(row["id"]),
            "parse_id": str(row["parse_id"]),
            "version_id": str(row["version_id"]),
            "image_sha256": row["sha256"],
            "page": row["page_no"],
            "bbox": row["bbox"],
        },
        "review_state": "unreviewed",
        "extraction_strategy": strategy or "single_stage",
        "stages": 2 if node_receipt else 1,
        "model_simulated": receipt["simulated"],
        "provider": receipt["provider"],
        "model": receipt["requested_model"],
    }
    with tenant_transaction(owner) as conn:
        aid = publish(
            conn,
            owner,
            project,
            run,
            "figure",
            "figure.json",
            canonical(result).encode(),
            mime="application/json",
            suffix="json",
            metadata=result,
            inputs=[
                {"role": "figure_image", "source_file_id": row["file_id"]},
                {"role": "parse", "source_parse_id": row["parse_id"]},
            ],
        )
    return {**result, "artifact_version_id": str(aid)}


def correct(owner, project, run, inputs):
    from scholarship.models.figure_contract import validate_description

    description = validate_description(canonical(inputs["description"]))
    with tenant_transaction(owner) as conn:
        previous = (
            conn.execute(
                sa.select(T["artifact_versions"], T["artifacts"].c.project_id)
                .join(T["artifacts"], T["artifacts"].c.id == T["artifact_versions"].c.artifact_id)
                .where(
                    T["artifact_versions"].c.id == UUID(inputs["artifact_version_id"]),
                    T["artifacts"].c.kind == "figure",
                )
            )
            .mappings()
            .first()
        )
        if not previous or previous["project_id"] != project:
            raise ContractError("figure_artifact_not_found")
        origin = previous["metadata_json"]["source"]
        row = source(conn, UUID(origin["block_id"]))
        if row["project_id"] != project or origin["image_sha256"] != row["sha256"]:
            raise ContractError("figure_source_integrity")
        result = {
            "source": origin,
            "description": description,
            "review_state": "unreviewed",
            "method": "manual_correction",
            "reason": inputs["reason"],
            "previous_artifact_version_id": inputs["artifact_version_id"],
            "model_simulated": False,
            "external_requests": 0,
        }
        aid = publish(
            conn,
            owner,
            project,
            run,
            "figure",
            "figure.json",
            canonical(result).encode(),
            mime="application/json",
            suffix="json",
            metadata=result,
            inputs=[
                {"role": "previous_figure", "source_artifact_version_id": previous["id"]},
                {"role": "parse", "source_parse_id": row["parse_id"]},
            ],
        )
    return {**result, "artifact_version_id": str(aid)}
