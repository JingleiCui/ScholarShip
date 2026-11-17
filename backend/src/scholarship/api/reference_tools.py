"""Explicit human reference disambiguation with an audit trail."""

from uuid import UUID
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from scholarship.api.auth import Identity, identity
from scholarship.api.offline import demo_actor
from scholarship.domains.deep_parse import version_scope
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T

router = APIRouter(prefix="/studio")


class Resolution(BaseModel):
    paper_id: UUID | None = None
    reason: str = Field(min_length=3, max_length=1000)


@router.get("/parses/{parse_id}/reference-context")
def context(parse_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        parse = (
            conn.execute(sa.select(T["document_parses"]).where(T["document_parses"].c.id == parse_id))
            .mappings()
            .first()
        )
        if not parse:
            raise HTTPException(404, "parse_not_found")
        version_scope(conn, parse["version_id"])
        papers = conn.execute(
            sa.select(T["papers"].c.id, T["papers"].c.title).where(
                T["papers"].c.project_id == parse["project_id"], T["papers"].c.deleted_at.is_(None)
            )
        ).mappings()
        return {
            "project_id": parse["project_id"],
            "papers": [dict(r) for r in papers],
            "can_write": user.role == "researcher",
        }


@router.put("/references/{reference_id}/resolution")
def resolve(reference_id: UUID, body: Resolution, user: Identity = Depends(demo_actor)):
    if len(body.reason.strip()) < 3:
        raise HTTPException(422, "reference_review_reason_required")
    with tenant_transaction(user.user_id) as conn:
        ref = (
            conn.execute(
                sa.select(T["bibliographic_refs"]).where(T["bibliographic_refs"].c.id == reference_id).with_for_update()
            )
            .mappings()
            .first()
        )
        if not ref:
            raise HTTPException(404, "reference_not_found")
        parse = (
            conn.execute(sa.select(T["document_parses"]).where(T["document_parses"].c.id == ref["parse_id"]))
            .mappings()
            .one()
        )
        version_scope(conn, parse["version_id"])
        if body.paper_id and not conn.scalar(
            sa.select(T["papers"].c.id).where(
                T["papers"].c.id == body.paper_id,
                T["papers"].c.project_id == ref["project_id"],
                T["papers"].c.deleted_at.is_(None),
            )
        ):
            raise HTTPException(409, "reference_paper_scope_mismatch")
        from scholarship.domains.studio.reference_reviews import apply_reviews

        ref = apply_reviews(conn, [ref])[0]
        state = "verified" if body.paper_id else "unresolved"
        conn.execute(
            T["audit_events"]
            .insert()
            .values(
                owner_id=user.user_id,
                project_id=ref["project_id"],
                action="reference_identity_review",
                subject_json={"reference_id": str(reference_id)},
                safe_payload={
                    "old_paper_id": str(ref["resolved_paper_id"]) if ref["resolved_paper_id"] else None,
                    "paper_id": str(body.paper_id) if body.paper_id else None,
                    "reason": body.reason,
                },
            )
        )
        return {"resolution_state": state, "resolved_paper_id": body.paper_id}
