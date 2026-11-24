"""Immutable local parse publication and local-only downstream task orchestration."""

from collections import defaultdict
from uuid import UUID
import sqlalchemy as sa

from scholarship.adapters.providers import ContractError
from scholarship.api.library import stored_file
from scholarship.domains.deep_parse import persist_file, version_scope
from scholarship.domains.retrieval_profile import canonical, digest
from scholarship.domains.runs import emit
from scholarship.domains.studio.document_engine import extract, policy_snapshot, REVISION
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T

LIMITATIONS = (
    "本地文字/OCR与区域排序；表格线检测、图片区域为启发式。"
    "无边框表格、公式语义、复杂跨栏及图连线需人工核验；未审阅图识别不进入可信索引。"
)


def parse_document(owner, project, version_id, policy, run=None):
    policy = policy_snapshot(policy)
    with tenant_transaction(owner) as conn:
        v = version_scope(conn, version_id)
        if v["project_id"] != project:
            raise ContractError("version_project_mismatch")
        path = stored_file(conn, version_id)
        source_hash = digest(path.read_bytes())
        config_hash = digest(canonical({"source": source_hash, "policy": policy}))
        old = conn.scalar(
            sa.select(T["document_parses"].c.id).where(
                T["document_parses"].c.version_id == version_id,
                T["document_parses"].c.parser_name == "local-document",
                T["document_parses"].c.config_sha256 == config_hash,
                T["document_parses"].c.status == "ready",
            )
        )
        if old:
            return {"parse_id": str(old), "cached": True, "external_requests": 0, "limitations": LIMITATIONS}

    def progress(stage, values):
        if run:
            from scholarship.domains.cancellation import check_cancelled

            check_cancelled(owner, run)
            with tenant_transaction(owner) as conn:
                emit(
                    conn,
                    run,
                    "progress",
                    {
                        "stage": "parse",
                        "message": f"本地解析 · 第 {values['page']}/{values.get('total', v['metadata_json'].get('page_count', '?'))} 页"
                        + (" · OCR 已完成" if values.get("ocr") else ""),
                        **values,
                    },
                )

    pages, assets = extract(path, policy, progress)
    if digest(path.read_bytes()) != source_hash:
        raise ContractError("local_document_source_changed")
    with tenant_transaction(owner) as conn:
        conn.execute(
            sa.select(T["paper_versions"].c.id).where(T["paper_versions"].c.id == version_id).with_for_update()
        )
        version_scope(conn, version_id)
        old = conn.scalar(
            sa.select(T["document_parses"].c.id).where(
                T["document_parses"].c.version_id == version_id,
                T["document_parses"].c.config_sha256 == config_hash,
                T["document_parses"].c.parser_name == "local-document",
            )
        )
        if old:
            return {"parse_id": str(old), "cached": True, "external_requests": 0, "limitations": LIMITATIONS}
        if run:
            from scholarship.domains.cancellation import check_cancelled

            check_cancelled(owner, run)
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
                parser_name="local-document",
                parser_version=REVISION,
                config_sha256=config_hash,
                revision_no=rev,
                mode="deep",
                status="processing",
                expected_pages=len(pages),
                completed_pages=0,
            )
            .returning(T["document_parses"].c.id)
        )
        files = {key: persist_file(conn, owner, raw, "png", "image/png") for key, raw in assets.items()}
        section = []
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
                    rotation=page["rotation"],
                    status="ready",
                    page_sha256=digest(canonical(page)),
                    text_layer_chars=0 if page["ocr_performed"] else sum(len(b["text"]) for b in page["blocks"]),
                )
                .returning(T["document_pages"].c.id)
            )
            for ordinal, b in enumerate(page["blocks"]):
                if b["kind"] == "heading":
                    section = [b["text"]]
                conn.execute(
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
                        asset_file_id=files.get(b.get("asset")),
                        source_locator={
                            "mapping": REVISION,
                            "source_sha256": source_hash,
                            "fixture": False,
                            "ocr_performed": page["ocr_performed"],
                            "pdf_bbox": b["pdf_bbox"],
                            "crop_box": page["crop_box"],
                            "rotation": page["rotation"],
                            "page_idx": page["number"] - 1,
                        },
                        structured_json={
                            "method": b["method"],
                            "confidence": b["confidence"],
                            "semantic_review": "unreviewed",
                            "heading_level": 1 if b["kind"] == "heading" else None,
                            "reading_order": "recursive-whitespace-unverified",
                            "section_path": section,
                            "pipeline_policy": policy,
                            "nul_glyph_replacements": b["missing_glyphs"],
                        },
                    )
                )
        conn.execute(
            T["document_parses"]
            .update()
            .where(T["document_parses"].c.id == pid)
            .values(
                status="ready",
                completed_pages=len(pages),
                published_at=sa.func.now(),
                manifest_sha256=digest(canonical(pages)),
                source_text_sha256=digest("\n".join(b["text"] for p in pages for b in p["blocks"])),
            )
        )
    return {
        "parse_id": str(pid),
        "pages": len(pages),
        "external_requests": 0,
        "ocr_performed": any(p["ocr_performed"] for p in pages),
        "ocr_pages": [p["number"] for p in pages if p["ocr_performed"]],
        "empty_text_pages": [p["number"] for p in pages if not any(b["text"].strip() for b in p["blocks"])],
        "figures": len(assets),
        "limitations": LIMITATIONS,
    }


def prepare_document(owner, project, run, job):
    from scholarship.domains.studio.workflow import submit

    inputs = job["input_json"]["arguments"]
    policy = job["config_json"]["local_document_policy"]
    result = parse_document(owner, project, UUID(inputs["version_id"]), policy, run)
    pid = UUID(result["parse_id"])
    with tenant_transaction(owner) as conn:
        blocks = (
            conn.execute(
                sa.select(T["document_blocks"], T["document_pages"].c.page_no)
                .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
                .where(T["document_blocks"].c.parse_id == pid)
                .order_by(T["document_pages"].c.page_no, T["document_blocks"].c.ordinal)
            )
            .mappings()
            .all()
        )
    pages = defaultdict(list)
    figures = []
    for b in blocks:
        if b["canonical_text"].strip():
            pages[b["page_no"]].append(str(b["id"]))
        if b["kind"] == "figure" and b["asset_file_id"]:
            figures.append(str(b["id"]))
    planned = []
    if inputs.get("recognize_figures", True):
        planned += [("figure", {"block_ids": [bid]}, "figure:" + bid) for bid in figures]
    if inputs.get("translate_document", True):
        for page, ids in pages.items():
            for start in range(0, len(ids), 100):
                planned.append(
                    (
                        "translate",
                        {
                            "parse_id": str(pid),
                            "block_ids": ids[start : start + 100],
                            "language": inputs.get("language", "zh-CN"),
                        },
                        f"translate:{page}:{start}",
                    )
                )
    if len(planned) > policy["max_model_tasks"]:
        raise ContractError("document_model_task_limit_reduce_scope")
    children = []
    for operation, args, key in planned:
        from scholarship.domains.cancellation import check_cancelled

        check_cancelled(owner, run)
        # Children use parent's pinned bindings; later UI model changes cannot redirect this preparation.
        child = submit(
            owner,
            project,
            f"doc:{run}:{key}",
            operation,
            {**args, "preparation_run_id": str(run)},
            "local_inference",
            pinned_selection=job["config_json"]["models"],
        )
        children.append({"id": str(child["id"]), "operation": operation, "scope": key})
    return {
        **result,
        "children": children,
        "stage": "local_parse_ready_children_scheduled",
        "translation_tasks": sum(c["operation"] == "translate" for c in children),
        "figure_tasks": sum(c["operation"] == "figure" for c in children),
        "note": "解析已完成；识图和翻译按独立本地任务执行，需查看子任务状态；未审阅图不进入可信检索。",
    }


def alignment(owner, version_id, page_no=1):
    """Read-only source coverage. Never use a translation from another parse or source slice."""
    with tenant_transaction(owner) as conn:
        v = version_scope(conn, version_id)
        parse = (
            conn.execute(
                sa.select(T["document_parses"])
                .where(
                    T["document_parses"].c.version_id == version_id,
                    T["document_parses"].c.mode == "deep",
                    T["document_parses"].c.status == "ready",
                )
                .order_by(T["document_parses"].c.revision_no.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if not parse:
            return {
                "parse_id": None,
                "blocks": [],
                "pages": 0,
                "translation_blocks": 0,
                "total_blocks": 0,
                "tasks": [],
                "limitations": LIMITATIONS,
            }
        pid = parse["id"]
        blocks = (
            conn.execute(
                sa.select(T["document_blocks"], T["document_pages"].c.page_no)
                .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
                .where(T["document_blocks"].c.parse_id == pid)
                .order_by(T["document_pages"].c.page_no, T["document_blocks"].c.ordinal)
            )
            .mappings()
            .all()
        )
        segments = (
            conn.execute(
                sa.select(T["translation_segments"], T["translation_revisions"].c.revision_no)
                .join(
                    T["translation_revisions"],
                    T["translation_revisions"].c.id == T["translation_segments"].c.translation_id,
                )
                .where(
                    T["translation_segments"].c.parse_id == pid,
                    T["translation_segments"].c.status == "ready",
                    T["translation_revisions"].c.target_language == "zh-CN",
                )
                .order_by(T["translation_revisions"].c.revision_no.desc(), T["translation_segments"].c.segment_no)
            )
            .mappings()
            .all()
        )
        byblock = defaultdict(list)
        for s in segments:
            byblock[s["block_id"]].append(dict(s))
        rows, complete = [], 0
        for b in blocks:
            source = b["canonical_text"]
            candidates = byblock[b["id"]]
            revision = candidates[0]["translation_id"] if candidates else None
            selected = sorted(
                [s for s in candidates if s["translation_id"] == revision],
                key=lambda s: s["placeholders_json"].get("source_start", 0),
            )
            parts, cursor, valid = [], 0, True
            for s in selected:
                span = s["placeholders_json"]
                a, z = span.get("source_start", 0), span.get("source_end", len(source))
                if not 0 <= cursor <= a < z <= len(source) or digest(source[a:z]) != span.get("source_sha256"):
                    valid = False
                    break
                if a > cursor:
                    parts.append({"source": source[cursor:a], "translation": None, "state": "untranslated"})
                parts.append(
                    {
                        "source": source[a:z],
                        "translation": s["translated_text"],
                        "state": "ready",
                        "segment_id": str(s["id"]),
                        "translation_id": str(revision),
                        "model_simulated": bool(span.get("model_simulated")),
                        "human_edited": bool(span.get("human_edited")),
                    }
                )
                cursor = z
            if not valid:
                parts, cursor = [], 0
            if cursor < len(source):
                parts.append({"source": source[cursor:], "translation": None, "state": "untranslated"})
            translated = bool(source.strip() and parts and all(p["state"] == "ready" for p in parts))
            complete += translated
            if b["page_no"] == page_no:
                rows.append(
                    {
                        "id": str(b["id"]),
                        "canonical_text": source,
                        "kind": b["kind"],
                        "page_no": page_no,
                        "bbox": b["bbox"],
                        "parts": parts,
                        "translation_state": "ready"
                        if translated
                        else "partial"
                        if selected and valid
                        else "untranslated",
                        "translation_id": str(revision) if revision else None,
                        "source_locator": b["source_locator"],
                        "metadata": b["structured_json"],
                        "has_image": bool(b["asset_file_id"]),
                    }
                )
        tasks = (
            conn.execute(
                sa.select(
                    T["runs"].c.id,
                    T["runs"].c.status,
                    T["runs"].c.error_json,
                    T["runs"].c.input_json,
                    T["runs"].c.result_json,
                )
                .where(T["runs"].c.project_id == v["project_id"], T["runs"].c.kind == "research_studio")
                .order_by(T["runs"].c.created_at.desc())
                .limit(1000)
            )
            .mappings()
            .all()
        )
        related = []
        figure_ids = {str(b["id"]) for b in blocks if b["kind"] == "figure"}
        for task in tasks:
            args = task["input_json"].get("arguments", {})
            if (
                args.get("version_id") == str(version_id)
                or args.get("parse_id") == str(pid)
                or figure_ids.intersection(args.get("block_ids", []))
            ):
                related.append(
                    {
                        "id": str(task["id"]),
                        "status": task["status"],
                        "operation": task["input_json"].get("operation"),
                        "error": (task["error_json"] or {}).get("contract_code")
                        or (task["error_json"] or {}).get("code"),
                        "artifact_version_id": (task["result_json"] or {}).get("artifact_version_id"),
                    }
                )
        return {
            "parse_id": str(pid),
            "parser": parse["parser_name"],
            "pages": parse["completed_pages"],
            "blocks": rows,
            "translation_blocks": complete,
            "total_blocks": sum(bool(b["canonical_text"].strip()) for b in blocks),
            "ocr_pages": sorted({b["page_no"] for b in blocks if b["source_locator"].get("ocr_performed")}),
            "tasks": related,
            "limitations": LIMITATIONS,
        }
