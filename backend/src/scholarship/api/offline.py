"""Explicit offline demo routes. Neither arbitrary schemas nor models are accepted."""

from uuid import UUID
from typing import Annotated
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
import sqlalchemy as sa
from scholarship.api.auth import Identity, identity, mutation_identity
from scholarship.domains.offline_workflow import enqueue
from scholarship.domains.deep_parse import version_scope
from scholarship.adapters.providers import ContractError
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T

router = APIRouter(prefix="/offline")


def demo_actor(user: Identity = Depends(mutation_identity)):
    if user.role != "researcher":
        raise HTTPException(403, "researcher_required_for_offline_execution")
    return user


@router.post("/projects/{project_id}/build", status_code=202)
def build(
    project_id: UUID, user: Identity = Depends(demo_actor), idempotency_key: str = Header(min_length=1, max_length=128)
):
    return enqueue(user.user_id, project_id, "offline_build", idempotency_key, {})


class Question(BaseModel):
    question: Annotated[str, Field(min_length=1, max_length=1000)]
    version_ids: Annotated[list[UUID], Field(min_length=1, max_length=8)]


@router.post("/projects/{project_id}/questions", status_code=202)
def ask(
    project_id: UUID,
    body: Question,
    user: Identity = Depends(demo_actor),
    idempotency_key: str = Header(min_length=1, max_length=128),
):
    if not body.question.strip() or len(body.question.encode()) > 4000:
        raise HTTPException(422, "question_limit")
    try:
        return enqueue(
            user.user_id,
            project_id,
            "offline_qa",
            idempotency_key,
            {"question": body.question, "version_ids": sorted({str(v) for v in body.version_ids})},
        )
    except ContractError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/projects/{project_id}/documents")
def documents(project_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        return [
            dict(r)
            for r in conn.execute(
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
                    T["paper_versions"].c.metadata_json["synthetic"].as_boolean().is_(True),
                )
            ).mappings()
        ]


@router.get("/evidence/{evidence_id}")
def evidence(evidence_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        e = (
            conn.execute(sa.select(T["evidence_items"]).where(T["evidence_items"].c.id == evidence_id))
            .mappings()
            .first()
        )
        if not e or e["evidence_kind"] != "block":
            raise HTTPException(404, "evidence_not_found")
        b = (
            conn.execute(
                sa.select(T["document_blocks"], T["document_pages"].c.page_no)
                .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
                .where(T["document_blocks"].c.id == e["block_id"])
            )
            .mappings()
            .one()
        )
        parse = (
            conn.execute(sa.select(T["document_parses"]).where(T["document_parses"].c.id == b["parse_id"]))
            .mappings()
            .one()
        )
        v = version_scope(conn, parse["version_id"])
        if b["canonical_text"][e["char_start"] : e["char_end"]] != e["quote_text"]:
            raise HTTPException(409, "evidence_integrity_error")
        return {
            "quote": e["quote_text"],
            "text": b["canonical_text"],
            "start": e["char_start"],
            "end": e["char_end"],
            "page": b["page_no"],
            "bbox": b["bbox"],
            "parse_id": parse["id"],
            "version_id": v["id"],
            "synthetic": parse["parser_name"] == "synthetic-deep",
            "original_pdf_available": not v["metadata_json"].get("synthetic", False),
        }


@router.get("/projects/{project_id}/runs")
def history(project_id: UUID, user: Identity = Depends(identity)):
    from scholarship.domains.runs import public_run

    with tenant_transaction(user.user_id) as conn:
        rows = conn.execute(
            sa.select(T["runs"])
            .where(T["runs"].c.project_id == project_id, T["runs"].c.kind.in_(["offline_build", "offline_qa"]))
            .order_by(T["runs"].c.created_at.desc())
            .limit(10)
        ).mappings()
        return [public_run(r) for r in rows]
