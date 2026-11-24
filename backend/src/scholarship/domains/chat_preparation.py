"""Upload-atomic local preparation, durable children and read-only progress."""

from uuid import UUID
import sqlalchemy as sa
from scholarship.adapters.providers import ContractError, ExternalDisabled
from scholarship.domains.deep_parse import version_scope
from scholarship.domains.retrieval_profile import canonical, digest
from scholarship.domains.runs import emit, public_run
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.models.catalog import configuration
from scholarship.models.configuration import load_selection


def schedule(conn, scope, version, *, request_key=None, extras=None):
    """Persist with upload/outbox in the SAME transaction; never call a model here."""
    v = version_scope(conn, version)
    if v["project_id"] != scope["project_id"]:
        raise ContractError("version_project_mismatch")
    conn.execute(sa.select(T["paper_versions"].c.id).where(T["paper_versions"].c.id == version).with_for_update())
    # Concurrent upload/recovery requests share an in-flight preparation.
    active = (
        conn.execute(
            sa.select(T["runs"])
            .where(
                T["runs"].c.project_id == scope["project_id"],
                T["runs"].c.config_json["operation"].astext == "prepare_chat",
                T["runs"].c.input_json["arguments"]["version_id"].astext == str(version),
                T["runs"].c.status.in_(["queued", "running", "waiting_user"]),
            )
            .order_by(T["runs"].c.created_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if active:
        return public_run(active)
    previous = (
        conn.execute(
            sa.select(T["runs"])
            .where(
                T["runs"].c.project_id == scope["project_id"],
                T["runs"].c.config_json["operation"].astext == "prepare_chat",
                T["runs"].c.input_json["arguments"]["version_id"].astext == str(version),
            )
            .order_by(T["runs"].c.created_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    child_id = (previous["result_json"] or {}).get("index_run_id") if previous else None
    if child_id:
        state = conn.scalar(sa.select(T["runs"].c.status).where(T["runs"].c.id == UUID(child_id)))
        if state in {"queued", "running", "waiting_user"}:
            return public_run(previous)
    prefs = conn.scalar(sa.select(T["projects"].c.preferences).where(T["projects"].c.id == scope["project_id"]))
    choice = prefs.get("model_configuration", {})
    selection = load_selection(choice.get("preset"), choice.get("overrides"))
    from scholarship.domains.studio.document_engine import policy_snapshot

    auto = configuration().get("AUTO_DOCUMENT_PREPARATION", {})
    args = {
        "version_id": str(version),
        "recognize_figures": auto.get("recognize_figures", True),
        "translate_document": auto.get("translate_document", True),
        "language": "zh-CN",
    }
    if extras is False:
        args.update(recognize_figures=False, translate_document=False)
    config = {
        "revision": "research-studio-v1",
        "operation": "prepare_chat",
        "mode": "local",
        "models": selection.snapshot(),
        "local_document_policy": policy_snapshot(configuration().get("LOCAL_DOCUMENT_POLICY")),
        "local_index_request_limit": auto.get("max_local_index_requests", 128),
    }
    key = "prepare-chat:" + digest(
        canonical(
            {
                "version": str(version),
                "models": selection.snapshot(),
                "policy": config["local_document_policy"],
                "local_index_request_limit": config["local_index_request_limit"],
                "arguments": args,
                "request": request_key,
            }
        )
    )
    old = (
        conn.execute(
            sa.select(T["runs"]).where(
                T["runs"].c.project_id == scope["project_id"], T["runs"].c.idempotency_key == key
            )
        )
        .mappings()
        .first()
    )
    if old:
        return public_run(old)
    run = (
        conn.execute(
            T["runs"]
            .insert()
            .values(
                **scope,
                kind="research_studio",
                status="queued",
                idempotency_key=key,
                request_sha256=digest(canonical(args)),
                input_json={"arguments": args},
                config_json=config,
                input_scope_json={},
                next_event_seq=1,
                event_floor=1,
            )
            .returning(T["runs"])
        )
        .mappings()
        .one()
    )
    conn.execute(
        T["task_outbox"]
        .insert()
        .values(
            **scope,
            run_id=run["id"],
            event_key="studio:" + str(run["id"]),
            command="research_studio",
            payload_json={},
            available_at=sa.func.now(),
            delivery_attempts=0,
        )
    )
    emit(conn, run["id"], "queued", {"stage": "queued", "message": "PDF 已保存，等待自动解析与问答准备。"})
    return public_run(run)


def execute_preparation(owner, project, run, job):
    from scholarship.domains.studio.document_pipeline import parse_document, prepare_document
    from scholarship.domains.research import enqueue_research
    from scholarship.domains.cancellation import check_cancelled

    args, config = job["input_json"]["arguments"], job["config_json"]
    version = UUID(args["version_id"])
    with tenant_transaction(owner) as conn:
        emit(conn, run, "progress", {"stage": "parse", "message": "正在本地解析版面，按页检查是否需要 OCR。"})
    result = parse_document(owner, project, version, config["local_document_policy"], run)
    check_cancelled(owner, run)
    from dataclasses import replace
    from scholarship.models.configuration import restore_snapshot
    from scholarship.models.routing import require_local

    index_selection = restore_snapshot(config["models"])
    try:
        require_local(index_selection, ("embedding",))
    except ExternalDisabled:
        # No model request has been sent; changing configuration can safely recover this task.
        raise ContractError("local_preparation_configuration_required") from None
    cap = config.get("local_index_request_limit", 128)
    if type(cap) is not int or not 1 <= cap <= 512:
        raise ContractError("invalid_local_index_request_limit")
    index_selection = replace(index_selection, max_requests_per_run=cap)
    # Index FIRST: translation/figure failures must never gate evidence-based chat.
    index = enqueue_research(
        owner,
        project,
        f"prepare:{run}:index",
        "research_build",
        [version],
        mode="local_inference",
        pinned_selection=index_selection.snapshot(),
    )
    with tenant_transaction(owner) as conn:
        emit(
            conn,
            run,
            "progress",
            {
                "stage": "index_queued",
                "message": "解析完成，已自动排队切片、向量化与索引校验。",
                "index_run_id": str(index["id"]),
            },
        )
    result.update(index_run_id=str(index["id"]), children=[])
    if args.get("recognize_figures") or args.get("translate_document"):
        try:
            enriched = prepare_document(owner, project, run, job)
            result["children"] = enriched["children"]
        except (ContractError, ExternalDisabled):
            # The index task has already been durably saved. Preserve this failure separately.
            result["enrichment_error"] = "识图或翻译准备未完成，请在阅读与解析中查看并处理；不影响问答索引任务。"
    return result


def progress(owner, version):
    with tenant_transaction(owner) as conn:
        v = version_scope(conn, version)
        rows = (
            conn.execute(
                sa.select(T["runs"])
                .where(
                    T["runs"].c.project_id == v["project_id"],
                    sa.or_(
                        T["runs"].c.input_json["arguments"]["version_id"].astext == str(version),
                        T["runs"].c.input_json["version_ids"].contains([str(version)]),
                    ),
                )
                .order_by(T["runs"].c.created_at.desc())
                .limit(100)
            )
            .mappings()
            .all()
        )
        prep = next((r for r in rows if r["config_json"].get("operation") == "prepare_chat"), None)
        result = (prep["result_json"] or {}) if prep else {}
        linked = result.get("index_run_id")
        index = next((r for r in rows if str(r["id"]) == linked), None) if linked else None
        if not index:
            index = next((r for r in rows if r["kind"] == "research_build"), None)
        parse = (
            conn.execute(
                sa.select(T["document_parses"])
                .where(
                    T["document_parses"].c.version_id == version,
                    T["document_parses"].c.mode == "deep",
                    T["document_parses"].c.status == "ready",
                )
                .order_by(T["document_parses"].c.revision_no.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        current = index if index and (not prep or prep["status"] == "succeeded") else prep
        event = None
        if current:
            event = conn.execute(
                sa.select(T["run_events"].c.public_payload)
                .where(
                    T["run_events"].c.run_id == current["id"],
                    T["run_events"].c.public_payload["stage"].astext.is_not(None),
                )
                .order_by(T["run_events"].c.seq.desc())
                .limit(1)
            ).scalar()
        generation = (
            conn.execute(sa.select(T["index_generations"]).where(T["index_generations"].c.id == v["active_index_id"]))
            .mappings()
            .first()
            if v["active_index_id"]
            else None
        )
        ready = bool(generation and generation["status"] == "ready" and parse and generation["parse_id"] == parse["id"])
        if ready:
            from scholarship.domains.indexing import profile_matches

            profile = (
                conn.execute(sa.select(T["index_profiles"]).where(T["index_profiles"].c.id == generation["profile_id"]))
                .mappings()
                .one()
            )
            prefs = conn.scalar(sa.select(T["projects"].c.preferences).where(T["projects"].c.id == v["project_id"]))
            choice = prefs.get("model_configuration", {})
            ready = profile_matches(
                profile, load_selection(choice.get("preset"), choice.get("overrides")), simulated=False
            )

        def safe(row):
            return {"id": str(row["id"]), "status": row["status"], "error": row["error_json"]} if row else None

        children = []
        for child in result.get("children", []):
            row = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == UUID(child["id"]))).mappings().first()
            if row:
                children.append({**safe(row), "operation": child["operation"]})
        return {
            "version_id": str(version),
            "preparation": safe(prep),
            "index": safe(index),
            "parse_ready": bool(parse),
            "index_ready": ready,
            "stage": (event or {}).get("stage"),
            "detail": event,
            "pages": parse["completed_pages"] if parse else None,
            "chunks": generation["verified_points"] if ready else None,
            "extras": children,
            "enrichment_error": result.get("enrichment_error"),
        }
