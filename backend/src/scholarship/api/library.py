"""Authenticated PDF library. Original filenames never become storage paths."""

import asyncio
import json
import sys
from pydantic import BaseModel
from typing import Literal
import hashlib
import os
from pathlib import Path
import tempfile
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, Form
from fastapi.responses import FileResponse
import sqlalchemy as sa

from scholarship.api.auth import identity, mutation_identity, Identity
from scholarship.config import DATA_DIR
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.domains.runs import emit

# USER CONFIGURATION
MAX_PDF_BYTES = 128 * 1024 * 1024
MAX_PAGES = 2000
FILE_ROOT = DATA_DIR / "files"
router = APIRouter()


def version_row(conn, vid):
    v = conn.execute(sa.select(T["paper_versions"]).where(T["paper_versions"].c.id == vid)).mappings().first()
    if not v:
        raise HTTPException(404, "version_not_found")
    return v


def stored_file(conn, vid):
    v = version_row(conn, vid)
    f = conn.execute(sa.select(T["file_objects"]).where(T["file_objects"].c.id == v["file_id"])).mappings().one()
    if f["mime_type"] != "application/pdf":
        raise HTTPException(404, "original_pdf_unavailable")
    path = (FILE_ROOT / f["storage_key"]).resolve()
    if not path.is_relative_to(FILE_ROOT.resolve()) or not path.is_file():
        raise HTTPException(404, "file_unavailable")
    return path


def schedule(conn, scope, vid):
    version = version_row(conn, vid)
    revision = 1 + conn.scalar(
        sa.select(sa.func.coalesce(sa.func.max(T["document_parses"].c.revision_no), 0)).where(
            T["document_parses"].c.version_id == vid
        )
    )
    stored_file(conn, vid)
    pages = version["metadata_json"].get("page_count")
    if not isinstance(pages, int) or not 0 < pages <= MAX_PAGES:
        raise HTTPException(409, "invalid_version_page_count")
    parse = conn.scalar(
        T["document_parses"]
        .insert()
        .values(
            **scope,
            version_id=vid,
            parser_name="pdfplumber_preview",
            parser_version="0.11.10",
            config_sha256=hashlib.sha256(b"page-text-v1").hexdigest(),
            revision_no=revision,
            mode="preview",
            status="pending",
            expected_pages=pages,
            completed_pages=0,
        )
        .returning(T["document_parses"].c.id)
    )
    run = conn.scalar(
        T["runs"]
        .insert()
        .values(
            **scope,
            kind="preview_parse",
            status="queued",
            idempotency_key=str(parse),
            request_sha256=hashlib.sha256(str(parse).encode()).hexdigest(),
            input_json={"parse_id": str(parse), "version_id": str(vid)},
            config_json={"mode": "preview"},
            input_scope_json={},
            next_event_seq=1,
            event_floor=1,
        )
        .returning(T["runs"].c.id)
    )
    conn.execute(
        T["task_outbox"]
        .insert()
        .values(
            **scope,
            run_id=run,
            event_key=str(run),
            command="preview_parse",
            payload_json={},
            available_at=sa.func.now(),
            delivery_attempts=0,
        )
    )
    emit(conn, run, "queued", {"message": "PDF 预览解析已排队。", "parse_id": str(parse)})
    return {"parse_id": parse, "run_id": run}


@router.post("/projects/{project_id}/uploads", status_code=202)
async def upload(
    project_id: UUID,
    file: UploadFile,
    paper_id: UUID | None = Form(None),
    user: Identity = Depends(mutation_identity),
    idempotency_key: str = Header(min_length=1, max_length=128),
):
    FILE_ROOT.mkdir(parents=True, exist_ok=True)
    with tenant_transaction(user.user_id) as conn:
        if not conn.scalar(sa.select(T["projects"].c.id).where(T["projects"].c.id == project_id)):
            raise HTTPException(404, "project_not_found")
    fd, tmp = tempfile.mkstemp(dir=FILE_ROOT, prefix=".incoming-")
    size = 0
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise HTTPException(413, "pdf_too_large")
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        try:
            with open(tmp, "rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise ValueError()
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "scholarship.workers.inspect_pdf",
                tmp,
                Path(file.filename or "document.pdf").name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                output, _ = await asyncio.wait_for(process.communicate(), timeout=30)
            except (TimeoutError, asyncio.CancelledError):
                process.kill()
                await process.wait()
                raise HTTPException(422, "pdf_validation_timeout")
            if process.returncode:
                raise HTTPException(422, "invalid_pdf")
            info = json.loads(output)
            if info.get("error"):
                raise HTTPException(422, info["error"])
            pages = info["pages"]
            if not 0 < pages <= MAX_PAGES:
                raise HTTPException(422, "pdf_page_limit")
            title = info["title"]
            identifiers = info.get("identifiers", [])
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(422, "invalid_pdf")
        sha = digest.hexdigest()
        scope = {"owner_id": user.user_id, "project_id": project_id}
        with tenant_transaction(user.user_id) as conn:
            conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"),
                {"k": str(user.user_id) + str(project_id)},
            )
            existing = (
                conn.execute(
                    sa.select(T["uploads"]).where(
                        T["uploads"].c.project_id == project_id, T["uploads"].c.idempotency_key == idempotency_key
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                prior = conn.scalar(
                    sa.select(T["file_objects"].c.sha256).where(T["file_objects"].c.id == existing["file_id"])
                )
                if prior != sha:
                    raise HTTPException(409, "idempotency_conflict")
                result = upload_result(conn, existing)
                if paper_id is not None and result.get("version_id"):
                    selected_version = version_row(conn, result["version_id"])
                    if selected_version["paper_id"] != paper_id:
                        raise HTTPException(409, "idempotency_target_conflict")
                return result
            conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"), {"k": str(user.user_id) + sha}
            )
            f = conn.execute(sa.select(T["file_objects"]).where(T["file_objects"].c.sha256 == sha)).mappings().first()
            if f:
                fid = f["id"]
            else:
                key = f"{user.user_id}/{sha}.pdf"
                dest = FILE_ROOT / key
                dest.parent.mkdir(exist_ok=True)
                os.replace(tmp, dest)
                fid = conn.scalar(
                    T["file_objects"]
                    .insert()
                    .values(
                        owner_id=user.user_id,
                        sha256=sha,
                        byte_size=size,
                        mime_type="application/pdf",
                        storage_key=key,
                        state="ready",
                    )
                    .returning(T["file_objects"].c.id)
                )
            uid = conn.scalar(
                T["uploads"]
                .insert()
                .values(
                    **scope,
                    file_id=fid,
                    display_name=Path(file.filename or "document.pdf").name,
                    origin="local_upload",
                    status="accepted",
                    idempotency_key=idempotency_key,
                )
                .returning(T["uploads"].c.id)
            )
            previous = (
                conn.execute(
                    sa.select(T["paper_versions"]).where(
                        T["paper_versions"].c.project_id == project_id, T["paper_versions"].c.file_id == fid
                    )
                )
                .mappings()
                .first()
            )
            if previous:
                return {"upload_id": uid, "version_id": previous["id"], "reused": True}
            title = title or Path(file.filename or "未命名文献").stem
            normalized = " ".join(title.casefold().split())
            candidates = (
                conn.execute(
                    sa.select(T["papers"].c.id, T["papers"].c.title).where(
                        T["papers"].c.project_id == project_id, T["papers"].c.normalized_title == normalized
                    )
                )
                .mappings()
                .all()
            )
            for ident in identifiers:
                matches = (
                    conn.execute(
                        sa.select(T["papers"].c.id, T["papers"].c.title)
                        .join(T["paper_identifiers"], T["paper_identifiers"].c.paper_id == T["papers"].c.id)
                        .where(
                            T["papers"].c.project_id == project_id,
                            T["paper_identifiers"].c.scheme == ident["scheme"],
                            T["paper_identifiers"].c.normalized_value == ident["normalized_value"],
                        )
                    )
                    .mappings()
                    .all()
                )
                candidates.extend(c for c in matches if c["id"] not in [x["id"] for x in candidates])
            if paper_id is not None:
                selected = (
                    conn.execute(
                        sa.select(T["papers"]).where(
                            T["papers"].c.id == paper_id,
                            T["papers"].c.project_id == project_id,
                            T["papers"].c.deleted_at.is_(None),
                        )
                    )
                    .mappings()
                    .first()
                )
                if not selected:
                    raise HTTPException(404, "paper_not_found")
                paper = paper_id
                candidates = [c for c in candidates if c["id"] != paper_id]
            else:
                paper = conn.scalar(
                    T["papers"]
                    .insert()
                    .values(**scope, title=title, normalized_title=normalized, display_metadata={})
                    .returning(T["papers"].c.id)
                )
            vid = conn.scalar(
                T["paper_versions"]
                .insert()
                .values(
                    **scope,
                    paper_id=paper,
                    file_id=fid,
                    metadata_json={
                        "schema_version": 1,
                        "title": title,
                        "page_count": pages,
                        "source": "pdf_metadata_filename_and_first_page_candidates",
                        "identifier_candidates": identifiers,
                    },
                )
                .returning(T["paper_versions"].c.id)
            )
            for ident in identifiers:
                exists = conn.scalar(
                    sa.select(T["paper_identifiers"].c.id).where(
                        T["paper_identifiers"].c.project_id == project_id,
                        T["paper_identifiers"].c.scheme == ident["scheme"],
                        T["paper_identifiers"].c.normalized_value == ident["normalized_value"],
                    )
                )
                if not exists:
                    conn.execute(T["paper_identifiers"].insert().values(**scope, paper_id=paper, **ident))
            review = None
            if candidates:
                review = conn.scalar(
                    T["duplicate_reviews"]
                    .insert()
                    .values(
                        **scope,
                        upload_id=uid,
                        candidate_json={
                            "new_version_id": str(vid),
                            "papers": [{"id": str(c["id"]), "title": c["title"]} for c in candidates],
                        },
                    )
                    .returning(T["duplicate_reviews"].c.id)
                )
                conn.execute(T["uploads"].update().where(T["uploads"].c.id == uid).values(status="waiting_user"))
            job = schedule(conn, scope, vid)
            return {"upload_id": uid, "version_id": vid, "review_id": review, "reused": False, **job}
    finally:
        Path(tmp).unlink(missing_ok=True)
        await file.close()


@router.get("/projects/{project_id}/papers")
def papers(project_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        rows = (
            conn.execute(
                sa.select(
                    T["papers"].c.id,
                    T["papers"].c.title,
                    T["paper_versions"].c.id.label("version_id"),
                    T["paper_versions"].c.metadata_json,
                )
                .join(T["paper_versions"], T["paper_versions"].c.paper_id == T["papers"].c.id)
                .where(
                    T["papers"].c.project_id == project_id,
                    T["papers"].c.deleted_at.is_(None),
                    T["paper_versions"].c.deleted_at.is_(None),
                    T["paper_versions"].c.metadata_json["synthetic"].as_boolean().is_distinct_from(True),
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


@router.get("/paper-versions/{version_id}/file")
def original(version_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        path = stored_file(conn, version_id)
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="paper.pdf"', "Cache-Control": "private, no-store"},
    )


@router.post("/paper-versions/{version_id}/parses", status_code=202)
def reparse(version_id: UUID, user: Identity = Depends(mutation_identity)):
    with tenant_transaction(user.user_id) as conn:
        v = version_row(conn, version_id)
        conn.execute(
            sa.select(T["paper_versions"].c.id).where(T["paper_versions"].c.id == version_id).with_for_update()
        )
        return schedule(conn, {"owner_id": user.user_id, "project_id": v["project_id"]}, version_id)


@router.get("/paper-versions/{version_id}/parses")
def parses(version_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        version_row(conn, version_id)
        rows = (
            conn.execute(
                sa.select(T["document_parses"])
                .where(T["document_parses"].c.version_id == version_id)
                .order_by(T["document_parses"].c.revision_no.desc())
            )
            .mappings()
            .all()
        )
        result = []
        for r in rows:
            run = (
                conn.execute(
                    sa.select(T["runs"].c.id, T["runs"].c.error_json, T["runs"].c.result_json).where(
                        T["runs"].c.kind == "preview_parse", T["runs"].c.input_json["parse_id"].astext == str(r["id"])
                    )
                )
                .mappings()
                .first()
            )
            result.append(
                {
                    **dict(r),
                    "run_id": run["id"] if run else None,
                    "error_json": run["error_json"] if run else None,
                    "result_json": run["result_json"] if run else None,
                }
            )
        return result


@router.get("/parses/{parse_id}/pages/{page_no}")
def page(parse_id: UUID, page_no: int, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        row = (
            conn.execute(
                sa.select(T["document_pages"]).where(
                    T["document_pages"].c.parse_id == parse_id, T["document_pages"].c.page_no == page_no
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "page_not_found")
        parse_version = conn.scalar(
            sa.select(T["document_parses"].c.version_id).where(T["document_parses"].c.id == parse_id)
        )
        version_row(conn, parse_version)
        blocks = (
            conn.execute(
                sa.select(T["document_blocks"])
                .where(T["document_blocks"].c.page_id == row["id"])
                .order_by(T["document_blocks"].c.ordinal)
            )
            .mappings()
            .all()
        )
        return {"page": dict(row), "needs_ocr": row["text_layer_chars"] == 0, "blocks": [dict(b) for b in blocks]}


@router.get("/projects/{project_id}/duplicate-reviews")
def reviews(project_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        return [
            dict(r)
            for r in conn.execute(
                sa.select(T["duplicate_reviews"]).where(
                    T["duplicate_reviews"].c.project_id == project_id, T["duplicate_reviews"].c.decision.is_(None)
                )
            ).mappings()
        ]


class ReviewDecision(BaseModel):
    decision: Literal["keep_both", "reject"]


@router.post("/duplicate-reviews/{review_id}/decision")
def decide(review_id: UUID, body: ReviewDecision, user: Identity = Depends(mutation_identity)):
    with tenant_transaction(user.user_id) as conn:
        row = (
            conn.execute(
                sa.select(T["duplicate_reviews"]).where(T["duplicate_reviews"].c.id == review_id).with_for_update()
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "review_not_found")
        if row["decision"] and row["decision"] != body.decision:
            raise HTTPException(409, "review_already_decided")
        if row["decision"] == body.decision:
            return {"decision": body.decision}
        conn.execute(
            T["duplicate_reviews"]
            .update()
            .where(T["duplicate_reviews"].c.id == review_id)
            .values(decision=body.decision, decided_at=sa.func.now())
        )
        conn.execute(
            T["uploads"]
            .update()
            .where(T["uploads"].c.id == row["upload_id"])
            .values(status="rejected" if body.decision == "reject" else "accepted")
        )
        if body.decision == "reject":
            version = version_row(conn, UUID(row["candidate_json"]["new_version_id"]))
            conn.execute(
                T["paper_versions"]
                .update()
                .where(T["paper_versions"].c.id == version["id"])
                .values(deleted_at=sa.func.now())
            )
            remaining = conn.scalar(
                sa.select(sa.func.count())
                .select_from(T["paper_versions"])
                .where(
                    T["paper_versions"].c.paper_id == version["paper_id"], T["paper_versions"].c.deleted_at.is_(None)
                )
            )
            if not remaining:
                conn.execute(
                    T["papers"].update().where(T["papers"].c.id == version["paper_id"]).values(deleted_at=sa.func.now())
                )

        conn.execute(
            T["audit_events"]
            .insert()
            .values(
                owner_id=user.user_id,
                project_id=row["project_id"],
                action="duplicate_review",
                subject_json={"review_id": str(review_id)},
                safe_payload={"decision": body.decision},
            )
        )
        return {"decision": body.decision}


@router.post("/parses/{parse_id}/resume", status_code=202)
def resume(parse_id: UUID, user: Identity = Depends(mutation_identity)):
    with tenant_transaction(user.user_id) as conn:
        p = (
            conn.execute(sa.select(T["document_parses"]).where(T["document_parses"].c.id == parse_id).with_for_update())
            .mappings()
            .first()
        )
        if not p:
            raise HTTPException(404, "parse_not_found")
        if p["status"] != "failed" and not (p["status"] == "partial" and p["completed_pages"] < p["expected_pages"]):
            raise HTTPException(409, "only_failed_preview_can_resume")
        run = (
            conn.execute(
                sa.select(T["runs"])
                .where(T["runs"].c.input_json["parse_id"].astext == str(parse_id), T["runs"].c.kind == "preview_parse")
                .with_for_update()
            )
            .mappings()
            .one()
        )
        attempts = conn.scalar(
            sa.select(sa.func.count()).select_from(T["run_steps"]).where(T["run_steps"].c.run_id == run["id"])
        )
        if attempts >= 3:
            raise HTTPException(409, "attempt_limit_create_new_revision")
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == run["id"])
            .values(status="queued", lease_token=None, lease_until=None, error_json=None, finished_at=None)
        )
        conn.execute(
            T["task_outbox"]
            .update()
            .where(T["task_outbox"].c.run_id == run["id"])
            .values(available_at=sa.func.now(), lease_token=None, lease_until=None)
        )
        conn.execute(
            T["document_parses"].update().where(T["document_parses"].c.id == parse_id).values(status="pending")
        )
        emit(conn, run["id"], "queued", {"message": "保留已完成页，重试未完成的预览页。"})
        return {"parse_id": parse_id, "run_id": run["id"]}


def upload_result(conn, row):
    result = {"upload_id": row["id"], "status": row["status"], "reused": True}
    version = conn.scalar(
        sa.select(T["paper_versions"].c.id)
        .where(T["paper_versions"].c.file_id == row["file_id"], T["paper_versions"].c.project_id == row["project_id"])
        .order_by(T["paper_versions"].c.created_at, T["paper_versions"].c.id)
        .limit(1)
    )
    if version:
        result["version_id"] = version
        parse = conn.scalar(
            sa.select(T["document_parses"].c.id)
            .where(T["document_parses"].c.version_id == version)
            .order_by(T["document_parses"].c.revision_no)
            .limit(1)
        )
        if parse:
            result["parse_id"] = parse
            result["run_id"] = conn.scalar(
                sa.select(T["runs"].c.id).where(
                    T["runs"].c.kind == "preview_parse", T["runs"].c.input_json["parse_id"].astext == str(parse)
                )
            )
    result["review_id"] = conn.scalar(
        sa.select(T["duplicate_reviews"].c.id).where(T["duplicate_reviews"].c.upload_id == row["id"])
    )
    return result


@router.get("/uploads/{upload_id}")
def upload_status(upload_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        row = conn.execute(sa.select(T["uploads"]).where(T["uploads"].c.id == upload_id)).mappings().first()
        if not row:
            raise HTTPException(404, "upload_not_found")
        return upload_result(conn, row)
