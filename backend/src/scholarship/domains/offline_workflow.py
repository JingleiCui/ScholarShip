"""Durable synthetic build and evidence workflow. No provider networking exists."""

from typing import TypedDict
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import StateGraph, START, END
import sqlalchemy as sa
from fastapi import HTTPException

from scholarship.adapters.providers import Evidence, ContractError
from scholarship.adapters.synthetic import adapters
from scholarship.config import settings
from scholarship.domains.billing import OfflineExecutor
from scholarship.domains.deep_parse import fixture_bundle, import_synthetic, persist_file, version_scope
from scholarship.domains.indexing import finish_call, snapshot, hybrid, build_index, call_step, EVIDENCE_LIMIT
from scholarship.domains.retrieval_profile import canonical, digest
from scholarship.domains.runs import emit, public_run
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.models.configuration import load_selection

# USER CONFIGURATION: tiny explicit fixtures, not competition acceptance answers.
DEMO_DOCS = [
    (
        "合成样本 A · Alpha",
        [
            [
                "SYNTHETIC FIXTURE A. This is not a research paper.",
                "Alpha method achieves an accuracy of 91 percent on the synthetic dataset.",
            ],
            ["Alpha experiment uses 120 synthetic samples. The learning rate is 0.001."],
        ],
    ),
    (
        "合成样本 B · Beta",
        [
            [
                "SYNTHETIC FIXTURE B. This is not a research paper.",
                "Beta method achieves an accuracy of 87 percent on the synthetic dataset.",
            ],
            ["Beta experiment uses 80 synthetic samples. The learning rate is 0.002."],
        ],
    ),
]


def enqueue(owner, project, kind, key, inputs, *, dispatch=True):
    if not 1 <= len(key) <= 128:
        raise HTTPException(422, "invalid_idempotency_key")
    h = digest(canonical({"kind": kind, "inputs": inputs}))
    with tenant_transaction(owner) as conn:
        conn.execute(sa.select(T["projects"].c.id).where(T["projects"].c.id == project).with_for_update())
        if not conn.scalar(sa.select(T["projects"].c.id).where(T["projects"].c.id == project)):
            raise HTTPException(404, "project_not_found")
        old = (
            conn.execute(
                sa.select(T["runs"]).where(T["runs"].c.project_id == project, T["runs"].c.idempotency_key == key)
            )
            .mappings()
            .first()
        )
        if old:
            if old["request_sha256"] != h:
                raise HTTPException(409, "idempotency_conflict")
            return public_run(old)
        scope = snapshot(owner, project, [UUID(v) for v in inputs["version_ids"]]) if kind == "offline_qa" else []
        row = (
            conn.execute(
                T["runs"]
                .insert()
                .values(
                    owner_id=owner,
                    project_id=project,
                    kind=kind,
                    status="queued",
                    idempotency_key=key,
                    request_sha256=h,
                    input_json=inputs,
                    config_json={
                        "mode": "synthetic",
                        "revision": "offline-business-v1",
                        "models": load_selection(
                            "mock",
                            overrides={
                                role: key
                                for role, key in {
                                    "chat": "mock_chat",
                                    "translation": "mock_chat",
                                    "embedding": "mock_embedding",
                                    "rerank": "mock_rerank",
                                    "parser": "mock_parser",
                                }.items()
                            },
                        ).snapshot(),
                    },
                    input_scope_json={"generations": scope},
                    next_event_seq=1,
                    event_floor=1,
                )
                .returning(T["runs"])
            )
            .mappings()
            .one()
        )
        if dispatch:
            conn.execute(
                T["task_outbox"]
                .insert()
                .values(
                    owner_id=owner,
                    project_id=project,
                    run_id=row["id"],
                    event_key=f"{kind}:{row['id']}",
                    command=kind,
                    payload_json={},
                    available_at=sa.func.now(),
                    delivery_attempts=0,
                )
            )
        emit(conn, row["id"], "queued", {"message": "离线合成任务已保存；不调用真实供应商。"})
        return public_run(row)


def demo_versions(owner, project):
    versions = []
    with tenant_transaction(owner) as conn:
        conn.execute(sa.select(T["projects"].c.id).where(T["projects"].c.id == project).with_for_update())
        if not conn.scalar(sa.select(T["projects"].c.id).where(T["projects"].c.id == project)):
            raise HTTPException(404, "project_not_found")
        for title, pages in DEMO_DOCS:
            raw = canonical({"synthetic": True, "title": title, "pages": pages}).encode()
            f = persist_file(conn, owner, raw, "json", "application/json")
            v = conn.scalar(
                sa.select(T["paper_versions"].c.id).where(
                    T["paper_versions"].c.project_id == project,
                    T["paper_versions"].c.file_id == f,
                    T["paper_versions"].c.deleted_at.is_(None),
                )
            )
            if not v:
                paper = conn.scalar(
                    T["papers"]
                    .insert()
                    .values(
                        owner_id=owner,
                        project_id=project,
                        title=title,
                        normalized_title=title,
                        display_metadata={"synthetic": True},
                    )
                    .returning(T["papers"].c.id)
                )
                v = conn.scalar(
                    T["paper_versions"]
                    .insert()
                    .values(
                        owner_id=owner,
                        project_id=project,
                        paper_id=paper,
                        file_id=f,
                        metadata_json={"synthetic": True, "page_count": len(pages), "source": "offline-fixture-v1"},
                    )
                    .returning(T["paper_versions"].c.id)
                )
            versions.append(v)
    return versions


def build_demo(owner, project, run):
    versions = demo_versions(owner, project)
    result = []
    for vid, (_, pages) in zip(versions, DEMO_DOCS, strict=True):
        pid = import_synthetic(owner, vid, fixture_bundle(pages), set(range(1, len(pages) + 1)))
        gid = build_index(owner, vid, pid, run)
        result.append({"version_id": str(vid), "parse_id": str(pid), "generation_id": str(gid)})
    return {"synthetic": True, "mode": "offline_fixture", "documents": result, "external_requests": 0}


class QAState(TypedDict, total=False):
    retrieval: dict
    selected: list
    answer: dict
    result: dict
    structured: dict


def validate_selected(owner, project, selected):
    """Revalidate after every checkpoint boundary and before final publication."""
    with tenant_transaction(owner) as conn:
        for item in selected:
            v = version_scope(conn, UUID(item["version_id"]))
            if v["project_id"] != project:
                raise ContractError("evidence_project_mismatch")
            b = (
                conn.execute(
                    sa.select(T["document_blocks"]).where(
                        T["document_blocks"].c.id == UUID(item["block_id"]),
                        T["document_blocks"].c.parse_id == UUID(item["parse_id"]),
                    )
                )
                .mappings()
                .first()
            )
            if (
                not b
                or b["canonical_text"][item["block_start"] : item["block_start"] + len(item["text"])] != item["text"]
            ):
                raise ContractError("evidence_changed")


def persist_answer(owner, project, run, question, answer, selected, trace):
    validate_selected(owner, project, selected)
    with tenant_transaction(owner) as conn:
        row = (
            conn.execute(
                sa.select(T["runs"]).where(T["runs"].c.id == run, T["runs"].c.project_id == project).with_for_update()
            )
            .mappings()
            .one()
        )
        if row["result_json"]:
            return row["result_json"]
        # Hold source carrier locks through publication to serialize soft deletion.
        for vid in sorted({item["version_id"] for item in selected}):
            conn.execute(
                sa.select(T["paper_versions"].c.id).where(T["paper_versions"].c.id == UUID(vid)).with_for_update()
            )
            version_scope(conn, UUID(vid))
        model_simulated = row["config_json"]["mode"] != "cloud"
        source_synthetic = any(
            version_scope(conn, UUID(v))["metadata_json"].get("synthetic", False)
            for v in row["input_json"].get("version_ids", [])
        )
        scope = {"owner_id": owner, "project_id": project}
        conversation = None
        if not row["input_json"].get("parent_run_id"):
            conversation = conn.scalar(
                T["conversations"]
                .insert()
                .values(
                    **scope,
                    title="文献问答 · 离线验收" if model_simulated else "文献证据问答",
                    checkpoint_thread_id=f"offline:{run}",
                    context_json={
                        "synthetic": model_simulated,
                        "source_synthetic": source_synthetic,
                        "models": row["config_json"].get("models"),
                    },
                    lock_version=0,
                )
                .returning(T["conversations"].c.id)
            )
        first_ordinal = 0
        if row["input_json"].get("parent_run_id"):
            # Reuse the source-pinned conversation and fence concurrent branches.
            conversation = UUID(row["input_json"]["conversation_id"])
            conn.execute(
                sa.select(T["conversations"].c.id).where(T["conversations"].c.id == conversation).with_for_update()
            ).scalar_one()
            last = (
                conn.execute(
                    sa.select(T["messages"])
                    .where(T["messages"].c.conversation_id == conversation)
                    .order_by(T["messages"].c.ordinal.desc())
                    .limit(1)
                )
                .mappings()
                .one()
            )
            if (
                last["ordinal"] != row["input_json"]["expected_message_ordinal"]
                or str(last["run_id"]) != row["input_json"]["parent_run_id"]
            ):
                raise ContractError("followup_conversation_advanced")
            first_ordinal = last["ordinal"] + 1
        conn.execute(T["runs"].update().where(T["runs"].c.id == run).values(conversation_id=conversation))
        conn.execute(
            T["messages"]
            .insert()
            .values(
                **scope,
                conversation_id=conversation,
                run_id=run,
                ordinal=first_ordinal,
                role="user",
                content_text=question,
                verification_state="not_applicable",
                final_at=sa.func.now(),
            )
        )
        mid = conn.scalar(
            T["messages"]
            .insert()
            .values(
                **scope,
                conversation_id=conversation,
                run_id=run,
                ordinal=first_ordinal + 1,
                role="assistant",
                content_text=answer["answer"],
                verification_state="insufficient" if answer["insufficient_evidence"] else "partially_supported",
                final_at=sa.func.now(),
            )
            .returning(T["messages"].c.id)
        )
        byid = {i["id"]: i for i in selected}
        citations = []
        for ordinal, claim in enumerate(answer["claims"]):
            claim_id = conn.scalar(
                T["claims"]
                .insert()
                .values(
                    **scope,
                    run_id=run,
                    message_id=mid,
                    ordinal=ordinal,
                    claim_text=claim["text"],
                    claim_kind="factual",
                    verification_state="partially_supported",
                )
                .returning(T["claims"].c.id)
            )
            for citation in claim["citations"]:
                item = byid[citation["evidence_id"]]
                a = item["block_start"] + citation["start"]
                z = item["block_start"] + citation["end"]
                eid = conn.scalar(
                    T["evidence_items"]
                    .insert()
                    .values(
                        **scope,
                        run_id=run,
                        block_id=UUID(item["block_id"]),
                        evidence_kind="block",
                        quote_text=citation["quote"],
                        char_start=a,
                        char_end=z,
                        locator_json={
                            "version_id": item["version_id"],
                            "parse_id": item["parse_id"],
                            "page": item["page"],
                            "bbox": item["bbox"],
                            "synthetic": source_synthetic,
                            "model_simulated": model_simulated,
                        },
                        retrieval_json={
                            "generation_id": item["generation_id"],
                            "chunk_id": None if item.get("retrieval_mode") else item["id"],
                            "mode": item.get("retrieval_mode", "dense_bm25_rrf_rerank"),
                        },
                    )
                    .returning(T["evidence_items"].c.id)
                )
                conn.execute(
                    T["claim_evidence"]
                    .insert()
                    .values(**scope, run_id=run, claim_id=claim_id, evidence_id=eid, relation="context")
                )
                citations.append(
                    {
                        "id": str(eid),
                        "quote": citation["quote"],
                        "page": item["page"],
                        "version_id": item["version_id"],
                        "parse_id": item["parse_id"],
                        "bbox": item["bbox"],
                        "block_id": item["block_id"],
                    }
                )
        result = {
            "synthetic": model_simulated,
            "model_simulated": model_simulated,
            "source_synthetic": source_synthetic,
            "answer": answer["answer"],
            "answer_method": answer.get("method", "model_evidence_qa"),
            "model_used": not answer.get("method", "").startswith("deterministic_"),
            "insufficient_evidence": answer["insufficient_evidence"],
            "verification": "exact_citation_spans_only",
            "citations": citations,
            "trace": trace,
            "scope": row["input_scope_json"],
            "limitations": ("合成响应；" if model_simulated else "")
            + "只核对逐字引文，不证明科研事实、数值或推理正确。",
            "external_requests": 0 if model_simulated else None,
        }
        conn.execute(
            T["runs"].update().where(T["runs"].c.id == run).values(conversation_id=conversation, result_json=result)
        )
        return result


def qa_graph(owner, project, run, *, interrupt_after=None, model_gateway=None):
    with tenant_transaction(owner) as conn:
        row = (
            conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == run, T["runs"].c.project_id == project))
            .mappings()
            .one()
        )
        if row["kind"] not in ({"model_qa", "research_qa"} if model_gateway is not None else {"offline_qa"}):
            raise ContractError("qa_run_required")
        original_question = row["input_json"]["question"]
        question = row["input_json"].get("query_context", original_question)
        scope = row["input_scope_json"]["generations"]
    embed, rerank, answerer = adapters() if model_gateway is None else (None, None, None)
    executor = OfflineExecutor(owner) if model_gateway is None else None
    expected_dimensions = None
    if model_gateway is not None:
        if row["config_json"].get("models") != model_gateway.selection.snapshot():
            raise ContractError("workflow_model_snapshot_mismatch")
        with tenant_transaction(owner) as conn:
            expected_dimensions = (
                conn.scalar(
                    sa.select(T["index_profiles"].c.dimensions).where(
                        T["index_profiles"].c.id == UUID(scope[0]["profile_id"])
                    )
                )
                if scope
                else None
            )

    def perform(step, intent, legacy, role, arguments, *, maximum_input):
        if model_gateway is None:
            return executor.execute(step, intent, legacy, role, arguments, maximum_input=maximum_input)
        operation = {"embedding": "embed", "rerank": "rank", "chat": "answer"}[role]
        actual = dict(arguments)
        if role == "embedding":
            actual.update(input_kind="query", expected_dimensions=expected_dimensions)
        receipt = model_gateway.invoke(intent, role, operation, actual)
        if receipt["simulated"] != (row["config_json"]["mode"] != "cloud"):
            raise ContractError("workflow_simulation_mode_mismatch")
        return {
            "result": receipt["value"],
            "usage": receipt["usage"],
            "model": receipt["requested_model"],
            "simulated": receipt["simulated"],
        }

    def retrieve(state):
        if row["config_json"].get("answer_contract") == "quote-menu-v1":
            from scholarship.domains.structured_retrieval import author_evidence

            structured = author_evidence(owner, project, scope, original_question)
            if structured:
                return {
                    "retrieval": {"candidates": structured["selected"], "trace": structured["trace"]},
                    "structured": structured,
                }
            if row["config_json"].get("structural_evidence") == "native-table-decimal-v2":
                from scholarship.domains.structured_retrieval import table_comparison_evidence

                calculated = table_comparison_evidence(owner, project, scope, original_question)
                if calculated:
                    return {
                        "retrieval": {"candidates": calculated["selected"], "trace": calculated["trace"]},
                        "structured": calculated,
                    }
        arguments = {"texts": [question]}
        step = call_step(owner, project, run, "query_embedding", arguments)
        receipt = perform(step, "query_embedding", embed, "embedding", arguments, maximum_input=len(question.encode()))
        finish_call(owner, step, receipt)
        retrieval = hybrid(owner, project, scope, question, receipt["result"][0])
        if row["config_json"].get("structural_evidence") in {"native-table-v1", "native-table-decimal-v2"}:
            from scholarship.domains.structured_retrieval import table_evidence

            tables = table_evidence(owner, project, scope, question)
            if tables:
                table_blocks = {t["block_id"] for t in tables}
                retrieval["candidates"] = (
                    tables + [c for c in retrieval["candidates"] if c["block_id"] not in table_blocks]
                )[:20]
                retrieval["trace"].append(
                    {"method": "native_table_lexical_supplement", "count": len(tables), "not_embedding_points": True}
                )
        return {"retrieval": retrieval}

    def rank(state):
        if state.get("structured"):
            return {"selected": state["structured"]["selected"]}
        candidates = state["retrieval"]["candidates"]
        validate_selected(owner, project, candidates)
        if not candidates:
            return {"selected": []}
        arguments = {
            "query": question,
            "documents": [i["text"] for i in candidates],
            "top_n": min(EVIDENCE_LIMIT, len(candidates)),
        }
        step = call_step(owner, project, run, "rerank", arguments)
        receipt = perform(
            step,
            "rerank",
            rerank,
            "rerank",
            arguments,
            maximum_input=sum(len(t.encode()) + len(question.encode()) for t in arguments["documents"]),
        )
        finish_call(owner, step, receipt)
        return {"selected": [candidates[r["index"]] for r in receipt["result"]]}

    def answer(state):
        if state.get("structured"):
            return {"answer": state["structured"]["answer"]}
        selected = state["selected"]
        validate_selected(owner, project, selected)
        if model_gateway is not None and not selected:
            return {"answer": {"answer": "缺少可核验的原文证据。", "claims": [], "insufficient_evidence": True}}
        evidence = [Evidence(i["id"], i["text"], i["page"]) for i in selected]
        arguments = {"question": question, "evidence": evidence}
        if row["config_json"].get("answer_contract") == "quote-menu-v1":
            arguments["citation_mode"] = "quote_id"
        step = call_step(owner, project, run, "answer", {"question": question, "selected": selected})
        receipt = perform(
            step,
            "answer",
            answerer,
            "chat",
            arguments,
            maximum_input=4096 + sum(len(e.text.encode()) for e in evidence),
        )
        finish_call(owner, step, receipt)
        return {"answer": receipt["result"]}

    def publish(state):
        from scholarship.domains.cancellation import check_cancelled
        check_cancelled(owner, run)
        return {
            "result": persist_answer(
                owner, project, run, original_question, state["answer"], state["selected"], state["retrieval"]["trace"]
            )
        }

    with psycopg.connect(
        settings().dsn("checkpoint"),
        autocommit=True,
        row_factory=dict_row,
        prepare_threshold=0,
        options="-c search_path=agent",
    ) as conn:
        builder = StateGraph(QAState)
        for name, node in [("retrieve", retrieve), ("rerank", rank), ("answer", answer), ("publish", publish)]:
            builder.add_node(name, node)
        for a, b in [
            (START, "retrieve"),
            ("retrieve", "rerank"),
            ("rerank", "answer"),
            ("answer", "publish"),
            ("publish", END),
        ]:
            builder.add_edge(a, b)
        graph = builder.compile(checkpointer=PostgresSaver(conn), interrupt_after=interrupt_after)
        config = {"configurable": {"thread_id": f"offline:{owner}:{run}"}}
        previous = graph.get_state(config)
        if previous.values.get("result") and not previous.next:
            return previous.values["result"]
        result = graph.invoke(None if previous.next else {}, config, durability="sync")
        return result.get("result")


def enqueue_model_qa(owner, project, key, versions, question, selection):
    """Developer contract workflow: synthetic parses/profiles only, no dispatch."""
    scope = snapshot(owner, project, versions, model_selection=selection)
    inputs = {
        "version_ids": [str(v) for v in versions],
        "question": question,
        "model_fingerprint": selection.snapshot()["fingerprint"],
        "scope": scope,
    }
    run = enqueue(owner, project, "model_qa", key, inputs, dispatch=False)
    with tenant_transaction(owner) as conn:
        row = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == run["id"]).with_for_update()).mappings().one()
        config = {"mode": "synthetic", "revision": "model-workflow-v1", "models": selection.snapshot()}
        if row["config_json"].get("revision") == "model-workflow-v1" and row["config_json"] != config:
            raise ContractError("workflow_configuration_conflict")
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == run["id"])
            .values(config_json=config, input_scope_json={"generations": scope})
        )
    return run
