"""Durable page-level preview. No model, OCR, or hidden indexing fallback."""

from datetime import datetime, timedelta, timezone
import hashlib
from uuid import UUID, uuid4
import pdfplumber
import sqlalchemy as sa
from scholarship.api.library import stored_file
from scholarship.infrastructure.database import tenant_transaction, engine
from scholarship.infrastructure.schema import tables as T
from scholarship.domains.runs import emit
from scholarship.domains.cancellation import RunCancelled, check_cancelled
from scholarship.workers.celery_app import app

# USER CONFIGURATION
LEASE_SECONDS = 30


@app.task(name="scholarship.preview_parse")
def preview_parse(owner, project, run_id):
    owner, project, run_id = UUID(owner), UUID(project), UUID(run_id)
    token = uuid4()
    # Session advisory lock prevents two processes from changing the same parse,
    # even when an individual page takes longer than the lease.
    with engine().connect() as lock:
        key = str(run_id)
        if not lock.scalar(sa.text("SELECT pg_try_advisory_lock(hashtextextended(:k,0))"), {"k": key}):
            return
        try:
            with tenant_transaction(owner) as conn:
                run = (
                    conn.execute(
                        sa.select(T["runs"])
                        .where(T["runs"].c.id == run_id, T["runs"].c.project_id == project)
                        .with_for_update()
                    )
                    .mappings()
                    .first()
                )
                if not run or run["kind"] != "preview_parse" or run["status"] not in ["queued", "running"]:
                    return
                attempts = conn.scalar(
                    sa.select(sa.func.coalesce(sa.func.max(T["run_steps"].c.attempt_no), 0)).where(
                        T["run_steps"].c.run_id == run_id
                    )
                )
                if attempts >= 3:
                    conn.execute(
                        T["runs"]
                        .update()
                        .where(T["runs"].c.id == run_id)
                        .values(
                            status="failed",
                            error_json={"code": "attempt_limit"},
                            lease_until=None,
                            finished_at=sa.func.now(),
                        )
                    )
                    emit(conn, run_id, "failed", {"message": "解析恢复次数已达上限，请创建新修订。"})
                    return
                conn.execute(
                    T["run_steps"]
                    .update()
                    .where(T["run_steps"].c.run_id == run_id, T["run_steps"].c.status == "running")
                    .values(status="failed", error_json={"code": "lease_expired"})
                )
                step_id = conn.scalar(
                    T["run_steps"]
                    .insert()
                    .values(
                        owner_id=owner,
                        project_id=project,
                        run_id=run_id,
                        step_key="preview_pages",
                        attempt_no=attempts + 1,
                        tool_name="pdfplumber_preview",
                        status="running",
                        input_sha256=run["request_sha256"],
                        lease_token=token,
                    )
                    .returning(T["run_steps"].c.id)
                )
                pid = UUID(run["input_json"]["parse_id"])
                vid = UUID(run["input_json"]["version_id"])
                conn.execute(
                    T["runs"]
                    .update()
                    .where(T["runs"].c.id == run_id)
                    .values(
                        status="running",
                        lease_token=token,
                        lease_until=datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS),
                    )
                )
                conn.execute(
                    T["document_parses"].update().where(T["document_parses"].c.id == pid).values(status="processing")
                )
                emit(conn, run_id, "running", {"message": "正在逐页提取本地预览。"})
            with tenant_transaction(owner) as conn:
                path = stored_file(conn, vid)
                if not conn.scalar(
                    sa.select(T["document_parses"].c.id).where(
                        T["document_parses"].c.id == pid,
                        T["document_parses"].c.project_id == project,
                        T["document_parses"].c.version_id == vid,
                        T["document_parses"].c.mode == "preview",
                    )
                ):
                    raise ValueError("parse_scope_mismatch")
            scope = {"owner_id": owner, "project_id": project}
            failures = []
            with pdfplumber.open(path) as pdf:
                total = len(pdf.pages)
                for number in range(1, total + 1):
                    check_cancelled(owner, run_id)
                    with tenant_transaction(owner) as conn:
                        prior = (
                            conn.execute(
                                sa.select(T["document_pages"]).where(
                                    T["document_pages"].c.parse_id == pid, T["document_pages"].c.page_no == number
                                )
                            )
                            .mappings()
                            .first()
                        )
                    if prior and prior["status"] == "ready":
                        continue
                    try:
                        pg = pdf.pages[number - 1]
                        text = pg.extract_text(layout=False) or ""
                        width, height = float(pg.width), float(pg.height)
                        with tenant_transaction(owner) as conn:
                            active = conn.scalar(
                                sa.select(T["runs"].c.lease_token).where(T["runs"].c.id == run_id).with_for_update()
                            )
                            if active != token:
                                return
                            if prior:
                                conn.execute(
                                    T["document_pages"].delete().where(T["document_pages"].c.id == prior["id"])
                                )
                            page = conn.scalar(
                                T["document_pages"]
                                .insert()
                                .values(
                                    **scope,
                                    parse_id=pid,
                                    page_no=number,
                                    width=width,
                                    height=height,
                                    rotation=pg.rotation or 0,
                                    status="ready",
                                    text_layer_chars=len(text),
                                )
                                .returning(T["document_pages"].c.id)
                            )
                            if text:
                                conn.execute(
                                    T["document_blocks"]
                                    .insert()
                                    .values(
                                        **scope,
                                        parse_id=pid,
                                        page_id=page,
                                        ordinal=0,
                                        kind="other",
                                        raw_text=text,
                                        canonical_text=text,
                                        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                                        locator_precision="page",
                                        source_locator={"page": number, "method": "page_text_preview"},
                                        structured_json={"preview_only": True, "reading_order_unverified": True},
                                    )
                                )
                            done = conn.scalar(
                                sa.select(sa.func.count())
                                .select_from(T["document_pages"])
                                .where(T["document_pages"].c.parse_id == pid, T["document_pages"].c.status == "ready")
                            )
                            conn.execute(
                                T["document_parses"]
                                .update()
                                .where(T["document_parses"].c.id == pid)
                                .values(completed_pages=done)
                            )
                            conn.execute(
                                T["runs"]
                                .update()
                                .where(T["runs"].c.id == run_id)
                                .values(
                                    heartbeat_at=sa.func.now(),
                                    lease_until=datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS),
                                )
                            )
                            emit(
                                conn,
                                run_id,
                                "progress",
                                {"message": f"预览页 {number}/{total}", "page": number, "total": total},
                            )
                        pg.close()
                    except Exception as exc:
                        failures.append({"page": number, "code": type(exc).__name__})
                with tenant_transaction(owner) as conn:
                    empty = conn.scalar(
                        sa.select(sa.func.count())
                        .select_from(T["document_pages"])
                        .where(T["document_pages"].c.parse_id == pid, T["document_pages"].c.text_layer_chars == 0)
                    )
                    partial = bool(failures or empty)
                    conn.execute(
                        T["document_parses"]
                        .update()
                        .where(T["document_parses"].c.id == pid)
                        .values(status="partial" if partial else "ready", published_at=sa.func.now())
                    )
                    result = {
                        "parse_id": str(pid),
                        "pages": total,
                        "needs_ocr_pages": empty,
                        "failed_pages": failures,
                        "preview_only": True,
                        "reading_order_unverified": True,
                    }
                    status = "partially_succeeded" if partial else "succeeded"
                    conn.execute(
                        T["runs"]
                        .update()
                        .where(T["runs"].c.id == run_id, T["runs"].c.lease_token == token)
                        .values(
                            status=status,
                            result_json=result,
                            lease_token=None,
                            lease_until=None,
                            finished_at=sa.func.now(),
                        )
                    )
                    conn.execute(
                        T["run_steps"]
                        .update()
                        .where(T["run_steps"].c.id == step_id)
                        .values(status="succeeded", output_json=result)
                    )
                    emit(
                        conn,
                        run_id,
                        status,
                        {
                            "message": "预览完成；缺少文本的页面需要 OCR。"
                            if partial
                            else "本地预览完成，尚未深解析。",
                            "result": result,
                        },
                    )
        except Exception as exc:
            with tenant_transaction(owner) as conn:
                active = conn.scalar(
                    sa.select(T["runs"].c.lease_token).where(T["runs"].c.id == run_id).with_for_update()
                )
                if active == token or (active is None and run and run["status"] == "queued"):
                    conn.execute(
                        T["runs"]
                        .update()
                        .where(T["runs"].c.id == run_id)
                        .values(
                            status="cancelled" if isinstance(exc, RunCancelled) else "failed",
                            error_json={"code": "preview_cancelled" if isinstance(exc, RunCancelled) else "preview_failed", "type": type(exc).__name__},
                            lease_token=None,
                            lease_until=None,
                            finished_at=sa.func.now(),
                        )
                    )
                    if "pid" in locals():
                        conn.execute(
                            T["document_parses"]
                            .update()
                            .where(T["document_parses"].c.id == pid, T["document_parses"].c.status != "ready")
                            .values(status="cancelled" if isinstance(exc, RunCancelled) else "failed")
                        )
                    if "step_id" in locals():
                        conn.execute(
                            T["run_steps"]
                            .update()
                            .where(T["run_steps"].c.id == step_id)
                            .values(status="failed", error_json={"code": type(exc).__name__})
                        )
                    emit(
                        conn,
                        run_id,
                        "cancelled" if isinstance(exc, RunCancelled) else "failed",
                        {"message": "已在页边界取消，已提取内容保留。" if isinstance(exc, RunCancelled) else "预览解析失败，可保留已完成页并重试。", "code": type(exc).__name__},
                    )
        finally:
            lock.execute(sa.text("SELECT pg_advisory_unlock(hashtextextended(:k,0))"), {"k": key})
            lock.commit()
