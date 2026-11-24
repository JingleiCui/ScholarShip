"""Tenant-scoped real-document product routes; cloud admission remains closed."""

from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import sqlalchemy as sa
from scholarship.api.auth import Identity, identity
from scholarship.api.offline import demo_actor, evidence
from scholarship.adapters.providers import ContractError, ExternalDisabled
from scholarship.domains.research import enqueue_research, project_selection
from scholarship.domains.indexing import profile_matches
from scholarship.domains.deep_parse import version_scope
from scholarship.domains.runs import public_run
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.config import settings
from pathlib import Path
from scholarship.domains.retrieval_profile import digest

router = APIRouter(prefix="/research")


@router.get("/versions/{version_id}/preparation")
def preparation_progress(version_id: UUID, user: Identity = Depends(identity)):
    from scholarship.domains.chat_preparation import progress

    return progress(user.user_id, version_id)


@router.post("/versions/{version_id}/prepare", status_code=202)
def prepare_for_chat(
    version_id: UUID, user: Identity = Depends(demo_actor), idempotency_key: str = Header(min_length=1, max_length=128)
):
    from scholarship.domains.chat_preparation import schedule

    with tenant_transaction(user.user_id) as conn:
        v = version_scope(conn, version_id)
        # Recovery prepares chat only; it never silently repeats failed translations.
        return schedule(
            conn,
            {"owner_id": user.user_id, "project_id": v["project_id"]},
            version_id,
            request_key=idempotency_key,
            extras=False,
        )


class ResearchRequest(BaseModel):
    version_ids: list[UUID] = Field(min_length=1, max_length=8)
    mode: Literal["offline_acceptance", "cloud", "local_inference"] = "offline_acceptance"
    question: str | None = Field(default=None, min_length=1, max_length=1000)
    parent_run_id: UUID | None = None


def submit(project_id, body, user, key, kind):
    if kind == "research_qa" and (not body.question or not body.question.strip()):
        raise HTTPException(422, "question_required")
    try:
        return enqueue_research(
            user.user_id,
            project_id,
            key,
            kind,
            body.version_ids,
            question=body.question,
            mode=body.mode,
            parent_run_id=body.parent_run_id,
        )
    except ExternalDisabled:
        raise HTTPException(409, "云端业务未启用：需单独配置正式额度和预算准入。") from None
    except ContractError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/projects/{project_id}/indexes", status_code=202)
def build(
    project_id: UUID,
    body: ResearchRequest,
    user: Identity = Depends(demo_actor),
    idempotency_key: str = Header(min_length=1, max_length=128),
):
    return submit(project_id, body, user, idempotency_key, "research_build")


@router.post("/projects/{project_id}/questions", status_code=202)
def ask(
    project_id: UUID,
    body: ResearchRequest,
    user: Identity = Depends(demo_actor),
    idempotency_key: str = Header(min_length=1, max_length=128),
):
    return submit(project_id, body, user, idempotency_key, "research_qa")


@router.get("/projects/{project_id}/documents")
def documents(project_id: UUID, user: Identity = Depends(identity)):
    selection = project_selection(user.user_id, project_id)
    with tenant_transaction(user.user_id) as conn:
        rows = (
            conn.execute(
                sa.select(
                    T["paper_versions"].c.id,
                    T["paper_versions"].c.active_index_id,
                    T["paper_versions"].c.active_parse_id,
                    T["papers"].c.title,
                )
                .join(T["papers"], T["papers"].c.id == T["paper_versions"].c.paper_id)
                .where(
                    T["paper_versions"].c.project_id == project_id,
                    T["paper_versions"].c.deleted_at.is_(None),
                    T["papers"].c.deleted_at.is_(None),
                    T["paper_versions"].c.metadata_json["synthetic"].as_boolean().is_distinct_from(True),
                )
            )
            .mappings()
            .all()
        )
        result = []
        for r in rows:
            native = conn.scalar(
                sa.select(T["document_parses"].c.id)
                .where(
                    T["document_parses"].c.version_id == r["id"],
                    T["document_parses"].c.parser_name.in_(["mineru-native", "pdfplumber-structure", "local-document"]),
                    T["document_parses"].c.status == "ready",
                )
                .order_by(T["document_parses"].c.revision_no.desc())
                .limit(1)
            )
            model_simulated = None
            readiness = {"can_query_offline": False, "can_query_cloud": False, "can_query_local": False}
            if r["active_index_id"]:
                active_profile = (
                    conn.execute(
                        sa.select(T["index_profiles"], T["index_generations"].c.status.label("generation_status"))
                        .join(T["index_generations"], T["index_generations"].c.profile_id == T["index_profiles"].c.id)
                        .where(T["index_generations"].c.id == r["active_index_id"])
                    )
                    .mappings()
                    .first()
                )
                if active_profile:
                    model_simulated = active_profile["config_json"].get("simulated")
                    if active_profile["generation_status"] == "ready":
                        readiness = {
                            "can_query_offline": profile_matches(active_profile, selection, simulated=True),
                            "can_query_cloud": profile_matches(active_profile, selection, simulated=False),
                            "can_query_local": profile_matches(active_profile, selection, simulated=False),
                        }
            result.append(
                {
                    **dict(r),
                    **readiness,
                    "native_parse_id": native,
                    "model_simulated": model_simulated,
                    "native_index_stale": bool(native and r["active_index_id"] and native != r["active_parse_id"]),
                }
            )
        return result


@router.get("/projects/{project_id}/runs")
def history(project_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        rows = (
            conn.execute(
                sa.select(T["runs"])
                .where(T["runs"].c.project_id == project_id, T["runs"].c.kind.in_(["research_build", "research_qa"]))
                .order_by(T["runs"].c.created_at.desc())
                .limit(100)
            )
            .mappings()
            .all()
        )
        reviews = {}
        if rows:
            for event in conn.execute(
                sa.select(T["audit_events"])
                .where(
                    T["audit_events"].c.project_id == project_id,
                    T["audit_events"].c.action == "research_answer_source_review",
                    T["audit_events"].c.subject_json["run_id"].astext.in_([str(r["id"]) for r in rows]),
                )
                .order_by(T["audit_events"].c.created_at.desc())
            ).mappings():
                reviews.setdefault(event["subject_json"]["run_id"], event["safe_payload"])
        return [
            {
                **public_run(r),
                "question": r["input_json"].get("question"),
                "mode": r["config_json"].get("mode"),
                "model_preset": r["config_json"].get("models", {}).get("preset"),
                "source_version_ids": r["input_json"].get("version_ids", []),
                "conversation_id": str(r["conversation_id"]) if r["conversation_id"] else None,
                "parent_run_id": r["input_json"].get("parent_run_id"),
                "scientific_review": reviews.get(str(r["id"])),
            }
            for r in rows
        ]


@router.get("/evidence/{evidence_id}")
def locate(evidence_id: UUID, user: Identity = Depends(identity)):
    result = evidence(evidence_id, user)
    with tenant_transaction(user.user_id) as conn:
        locator = conn.scalar(
            sa.select(T["document_blocks"].c.source_locator)
            .join(T["evidence_items"], T["evidence_items"].c.block_id == T["document_blocks"].c.id)
            .where(T["evidence_items"].c.id == evidence_id)
        )
        return {
            **result,
            "pdf_bbox": locator.get("pdf_bbox"),
            "crop_box": locator.get("crop_box"),
            "rotation": locator.get("rotation"),
        }


@router.get("/parses/{parse_id}/outline")
def outline(parse_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        p = (
            conn.execute(sa.select(T["document_parses"]).where(T["document_parses"].c.id == parse_id))
            .mappings()
            .first()
        )
        if not p:
            raise HTTPException(404, "parse_not_found")
        version_scope(conn, p["version_id"])
        rows = conn.execute(
            sa.select(
                T["document_blocks"].c.id,
                T["document_blocks"].c.parent_block_id,
                T["document_blocks"].c.canonical_text,
                T["document_blocks"].c.structured_json,
                T["document_pages"].c.page_no,
            )
            .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
            .where(T["document_blocks"].c.parse_id == parse_id, T["document_blocks"].c.kind.in_(["title", "heading"]))
            .order_by(T["document_pages"].c.page_no, T["document_blocks"].c.ordinal)
        ).mappings()
        return [dict(r) for r in rows]


@router.get("/blocks/{block_id}/asset")
def asset(block_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        row = (
            conn.execute(
                sa.select(T["file_objects"], T["document_parses"].c.version_id)
                .join(T["document_blocks"], T["document_blocks"].c.asset_file_id == T["file_objects"].c.id)
                .join(T["document_parses"], T["document_parses"].c.id == T["document_blocks"].c.parse_id)
                .where(T["document_blocks"].c.id == block_id, T["file_objects"].c.state == "ready")
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "asset_not_found")
        version_scope(conn, row["version_id"])
        root = (Path(settings().scholar_data_dir) / "files").resolve()
        path = (root / row["storage_key"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or row["mime_type"] not in {"image/jpeg", "image/png"}:
            raise HTTPException(409, "asset_unavailable")
        if path.stat().st_size != row["byte_size"] or digest(path.read_bytes()) != row["sha256"]:
            raise HTTPException(409, "asset_integrity_error")
        return FileResponse(path, media_type=row["mime_type"], headers={"Cache-Control": "private, no-store"})
