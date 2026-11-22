"""Workspace management and reversible library operations; no cloud calls."""

from difflib import SequenceMatcher
from uuid import UUID
import re
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from scholarship.api.auth import Identity, identity
from scholarship.api.offline import demo_actor
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.models.configuration import load_selection

router = APIRouter()
MAX_COMPARE_CHARS = 120000
MAX_DIFFERENCES = 100


class Workspace(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


@router.post("/projects", status_code=201)
def create_workspace(body: Workspace, user: Identity = Depends(demo_actor)):
    if not body.name.strip():
        raise HTTPException(422, "workspace_name_required")
    with tenant_transaction(user.user_id) as conn:
        conn.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"), {"k": f"workspace:{user.user_id}"}
        )
        previous = (
            conn.execute(
                sa.select(T["projects"]).where(
                    T["projects"].c.name == body.name.strip(), T["projects"].c.deleted_at.is_(None)
                )
            )
            .mappings()
            .first()
        )
        if previous:
            raise HTTPException(409, "workspace_name_exists")
        row = (
            conn.execute(
                T["projects"]
                .insert()
                .values(
                    owner_id=user.user_id,
                    name=body.name.strip(),
                    description=body.description,
                    preferences={"model_configuration": {"preset": load_selection().preset, "overrides": {}}},
                )
                .returning(T["projects"])
            )
            .mappings()
            .one()
        )
        return {k: row[k] for k in ("id", "name", "description")}


class PaperEdit(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=2000)
    archived: bool | None = None
    current_version_id: UUID | None = None


@router.patch("/papers/{paper_id}")
def edit_paper(paper_id: UUID, body: PaperEdit, user: Identity = Depends(demo_actor)):
    with tenant_transaction(user.user_id) as conn:
        row = (
            conn.execute(sa.select(T["papers"]).where(T["papers"].c.id == paper_id).with_for_update())
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "paper_not_found")
        values = {"updated_at": sa.func.now()}
        if body.title is not None:
            if not body.title.strip():
                raise HTTPException(422, "title_required")
            values.update(title=body.title.strip(), normalized_title=" ".join(body.title.casefold().split()))
        if body.current_version_id is not None:
            if not conn.scalar(
                sa.select(T["paper_versions"].c.id).where(
                    T["paper_versions"].c.id == body.current_version_id,
                    T["paper_versions"].c.paper_id == paper_id,
                    T["paper_versions"].c.deleted_at.is_(None),
                )
            ):
                raise HTTPException(409, "current_version_scope_mismatch")
            values["current_version_id"] = body.current_version_id
        if body.archived is not None:
            values["deleted_at"] = sa.func.now() if body.archived else None
        conn.execute(T["papers"].update().where(T["papers"].c.id == paper_id).values(**values))
        conn.execute(
            T["audit_events"]
            .insert()
            .values(
                owner_id=user.user_id,
                project_id=row["project_id"],
                action="paper_metadata_edit",
                subject_json={"paper_id": str(paper_id)},
                safe_payload=body.model_dump(mode="json", exclude_none=True),
            )
        )
    return {"id": paper_id, "saved": True}


@router.get("/projects/{project_id}/archived-papers")
def archived(project_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        return [
            dict(r)
            for r in conn.execute(
                sa.select(T["papers"].c.id, T["papers"].c.title).where(
                    T["papers"].c.project_id == project_id, T["papers"].c.deleted_at.is_not(None)
                )
            ).mappings()
        ]


def source_lines(conn, version):
    row = conn.execute(sa.select(T["paper_versions"]).where(T["paper_versions"].c.id == version)).mappings().first()
    if not row:
        raise HTTPException(404, "version_not_found")
    pid = conn.scalar(
        sa.select(T["document_parses"].c.id)
        .where(
            T["document_parses"].c.version_id == version,
            T["document_parses"].c.status.in_(["ready", "partial"]),
        )
        .order_by((T["document_parses"].c.mode == "deep").desc(), T["document_parses"].c.revision_no.desc())
        .limit(1)
    )
    if not pid:
        raise HTTPException(409, "parse_required_for_comparison")
    rows = conn.execute(
        sa.select(T["document_blocks"].c.id, T["document_blocks"].c.canonical_text, T["document_pages"].c.page_no)
        .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
        .where(T["document_blocks"].c.parse_id == pid)
        .order_by(T["document_pages"].c.page_no, T["document_blocks"].c.ordinal)
    ).mappings()
    lines, size, truncated = [], 0, False
    for b in rows:
        if size + len(b["canonical_text"]) > MAX_COMPARE_CHARS:
            truncated = True
            break
        size += len(b["canonical_text"])
        for line in b["canonical_text"].splitlines():
            if line.strip():
                lines.append({"text": line, "page": b["page_no"], "block_id": str(b["id"])})
    if not lines:
        raise HTTPException(409, "comparison_requires_text_or_ocr")
    return row, pid, lines, truncated


@router.get("/paper-versions/{version_id}/compare/{other_id}")
def compare(version_id: UUID, other_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        a, ap, left, at = source_lines(conn, version_id)
        b, bp, right, bt = source_lines(conn, other_id)
        if a["project_id"] != b["project_id"]:
            raise HTTPException(404, "comparison_scope_mismatch")

    def norm(s):
        return re.sub(r"\s+", " ", s).strip().casefold()

    matcher = SequenceMatcher(None, [norm(x["text"]) for x in left], [norm(x["text"]) for x in right], autojunk=True)
    changes = [
        {"kind": tag, "left": left[i:j], "right": right[k:end]}
        for tag, i, j, k, end in matcher.get_opcodes()
        if tag != "equal"
    ]
    return {
        "left": {"version_id": version_id, "parse_id": ap},
        "right": {"version_id": other_id, "parse_id": bp},
        "line_similarity": round(matcher.ratio(), 4),
        "changes": changes[:MAX_DIFFERENCES],
        "truncated": at or bt or len(changes) > MAX_DIFFERENCES,
        "notice": "本地文本行差异，排版/OCR 差异也会计入；不等于语义相同或科学结论变化。",
    }


@router.get("/projects/{project_id}/content-candidates")
def content_candidates(project_id: UUID, user: Identity = Depends(identity)):
    """Compare parsed first pages, including OCR, without trusting uploaded names."""
    from scholarship.domains.document_identity import fingerprint, similarity

    with tenant_transaction(user.user_id) as conn:
        versions = (
            conn.execute(
                sa.select(T["paper_versions"], T["papers"].c.title)
                .join(T["papers"], T["papers"].c.id == T["paper_versions"].c.paper_id)
                .where(
                    T["paper_versions"].c.project_id == project_id,
                    T["paper_versions"].c.deleted_at.is_(None),
                    T["papers"].c.deleted_at.is_(None),
                )
                .order_by(T["paper_versions"].c.created_at.desc())
                .limit(101)
            )
            .mappings()
            .all()
        )
        signatures = []
        for v in versions[:100]:
            parse = conn.scalar(
                sa.select(T["document_parses"].c.id)
                .where(
                    T["document_parses"].c.version_id == v["id"],
                    T["document_parses"].c.status.in_(["ready", "partially_succeeded"]),
                )
                .order_by((T["document_parses"].c.mode == "deep").desc(), T["document_parses"].c.revision_no.desc())
                .limit(1)
            )
            if not parse:
                continue
            blocks = conn.execute(
                sa.select(T["document_blocks"].c.canonical_text)
                .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
                .where(T["document_blocks"].c.parse_id == parse, T["document_pages"].c.page_no <= 3)
                .order_by(T["document_pages"].c.page_no, T["document_blocks"].c.ordinal)
                .limit(300)
            ).scalars()
            signature = fingerprint(" ".join(blocks)[:90000])
            if signature:
                signatures.append((v, parse, signature))
        matches = []
        for i, (v, p, sig) in enumerate(signatures):
            for w, q, other in signatures[i + 1 :]:
                score = similarity(sig, other)
                a, b = v["metadata_json"].get("arxiv_revision"), w["metadata_json"].get("arxiv_revision")
                if score >= 0.65 or (a and b and a["id"] == b["id"]):
                    advice = "先比较作者、标识和正文，再决定是否为同一作品。"
                    if a and b and a["id"] == b["id"] and a["version"] != b["version"]:
                        advice = f"同一 arXiv 标识的 v{a['version']} / v{b['version']}；建议首选 v{max(a['version'], b['version'])}，保留旧版证据。"
                    matches.append(
                        {
                            "left_version": str(v["id"]),
                            "right_version": str(w["id"]),
                            "left_title": v["title"],
                            "right_title": w["title"],
                            "left_parse": str(p),
                            "right_parse": str(q),
                            "similarity": round(score, 4),
                            "basis": "pdf_arxiv_identifier" if a and b and a["id"] == b["id"] else "text_shingles",
                            "advice": advice,
                        }
                    )
        return {
            "candidates": matches[:100],
            "versions_checked": len(versions[:100]),
            "truncated": len(versions) > 100 or len(matches) > 100,
            "notice": "依据 PDF 正文 arXiv 标识与前 3 页文字／OCR 的词组重合；这是身份核对候选，不是语义等价判定。",
        }
