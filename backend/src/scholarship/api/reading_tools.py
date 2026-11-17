"""Human translation revisions and portable exports; preserve original receipts."""

from collections import Counter
from uuid import UUID
import re
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from scholarship.api.auth import Identity, identity
from scholarship.api.offline import demo_actor
from scholarship.api.studio import segments
from scholarship.domains.retrieval_profile import digest, canonical
from scholarship.domains.studio.translation import PROTECTED
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T

router = APIRouter(prefix="/studio")


class TranslationEdit(BaseModel):
    segment_id: UUID
    expected_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str = Field(min_length=1, max_length=24000)
    reason: str = Field(min_length=1, max_length=500)


def quality_flags(source, target):
    flags = []
    if (
        re.search(r"\ball but (?:a )?few\b", source, re.I)
        and "少数" in target
        and not any(x in target for x in ["除", "绝大多数", "几乎所有"])
    ):
        flags.append("否定范围可能颠倒：all but a few 意为除少数外的大多数，请人工核对。")
    src = set(re.findall(r"(?<!\w)\d+(?:\.\d+)?%?", source))
    tgt = set(re.findall(r"(?<!\w)\d+(?:\.\d+)?%?", target))
    if src - tgt:
        flags.append("部分原文数字未原样出现在译文中，请核对单位、数字和格式。")
    return flags


@router.get("/translations/{translation_id}/review")
def review(translation_id: UUID, user: Identity = Depends(identity)):
    rows = segments(translation_id, user)
    return [
        {
            **r,
            "text_sha256": digest(r["translated_text"]),
            "quality_flags": quality_flags(r["canonical_text"], r["translated_text"]),
        }
        for r in rows
    ]


@router.post("/translations/{translation_id}/revisions", status_code=201)
def revise(translation_id: UUID, body: TranslationEdit, user: Identity = Depends(demo_actor)):
    if not body.text.strip() or not body.reason.strip():
        raise HTTPException(422, "translation_and_reason_required")
    with tenant_transaction(user.user_id) as conn:
        old = (
            conn.execute(sa.select(T["translation_revisions"]).where(T["translation_revisions"].c.id == translation_id))
            .mappings()
            .first()
        )
        if not old:
            raise HTTPException(404, "translation_not_found")
        if old["status"] != "ready":
            raise HTTPException(409, "ready_translation_required")
        conn.execute(
            sa.select(T["document_parses"].c.id).where(T["document_parses"].c.id == old["parse_id"]).with_for_update()
        )
        rows = (
            conn.execute(
                sa.select(T["translation_segments"]).where(T["translation_segments"].c.translation_id == translation_id)
            )
            .mappings()
            .all()
        )
        target = next((r for r in rows if r["id"] == body.segment_id), None)
        if not target:
            raise HTTPException(404, "translation_segment_not_found")
        if digest(target["translated_text"]) != body.expected_text_sha256:
            raise HTTPException(409, "translation_revision_conflict")
        # Preserve existing formula/code/URL/reference literals and HTML tags exactly.
        literals = Counter(
            PROTECTED.findall(target["translated_text"]) + re.findall(r"<[^>]+>", target["translated_text"])
        )
        edited = Counter(PROTECTED.findall(body.text) + re.findall(r"<[^>]+>", body.text))
        if literals != edited:
            raise HTTPException(422, "protected_translation_literals_changed")
        fingerprint = digest(canonical({"parent": str(translation_id), "edit": body.model_dump(mode="json")}))
        prior = conn.scalar(
            sa.select(T["translation_revisions"].c.id).where(
                T["translation_revisions"].c.parse_id == old["parse_id"],
                T["translation_revisions"].c.config_sha256 == fingerprint,
            )
        )
        if prior:
            return {"translation_id": prior, "cached": True}
        revision = 1 + conn.scalar(
            sa.select(sa.func.coalesce(sa.func.max(T["translation_revisions"].c.revision_no), 0)).where(
                T["translation_revisions"].c.parse_id == old["parse_id"]
            )
        )
        scope = {"owner_id": user.user_id, "project_id": old["project_id"]}
        tid = conn.scalar(
            T["translation_revisions"]
            .insert()
            .values(
                **scope,
                parse_id=old["parse_id"],
                target_language=old["target_language"],
                model_name=old["model_name"],
                prompt_revision="human-review-v1",
                glossary_sha256=old["glossary_sha256"],
                config_sha256=fingerprint,
                revision_no=revision,
                status="processing",
            )
            .returning(T["translation_revisions"].c.id)
        )
        for row in rows:
            changed = row["id"] == body.segment_id
            metadata = {
                **row["placeholders_json"],
                "parent_translation_id": str(translation_id),
                "parent_segment_id": str(row["id"]),
            }
            if changed:
                metadata.update(human_edited=True, review_reason=body.reason, human_editor=str(user.user_id))
            conn.execute(
                T["translation_segments"]
                .insert()
                .values(
                    **scope,
                    translation_id=tid,
                    parse_id=old["parse_id"],
                    block_id=row["block_id"],
                    segment_no=row["segment_no"],
                    translated_text=body.text if changed else row["translated_text"],
                    placeholders_json=metadata,
                    status=row["status"],
                )
            )
        conn.execute(
            T["translation_revisions"].update().where(T["translation_revisions"].c.id == tid).values(status="ready")
        )
        conn.execute(
            T["audit_events"]
            .insert()
            .values(
                **scope,
                action="translation_human_revision",
                subject_json={"parent": str(translation_id), "translation_id": str(tid)},
                safe_payload={"segment_id": str(body.segment_id), "reason": body.reason, "model_calls": 0},
            )
        )
        return {"translation_id": tid, "revision_no": revision, "model_calls": 0}


@router.get("/translations/{translation_id}/export")
def export_translation(translation_id: UUID, user: Identity = Depends(identity)):
    rows = segments(translation_id, user)
    if not rows:
        raise HTTPException(404, "translation_not_found")
    text = [
        "# ScholarShip 双语阅读导出",
        "",
        f"解析修订：{rows[0]['parse_id']}；翻译修订：{translation_id}",
        "",
        "译文须人工核对；模拟、人工修订标记逐段保留。",
        "",
    ]
    for r in rows:
        meta = r["placeholders_json"]
        label = "模拟结果" if meta.get("model_simulated") else "模型原译／待核验"
        if meta.get("human_edited"):
            label += "；人工修订"
        text += [
            f"## PDF 第 {r['page_no']} 页 · {r['block_id']}",
            "",
            r["canonical_text"],
            "",
            f"### 译文（{label}）",
            "",
            r["translated_text"],
            "",
        ]
    return Response(
        "\n".join(text),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="scholarship-translation.md"',
            "Cache-Control": "private, no-store",
        },
    )
