"""Bounded retrieval feedback. Unknown calls are never treated as a reason to retry."""

import re
from typing import TypedDict
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import StateGraph, START, END
import sqlalchemy as sa
from scholarship.adapters.providers import Evidence, ContractError
from scholarship.config import settings
from scholarship.domains.deep_parse import version_scope
from scholarship.domains.indexing import hybrid, call_step, finish_call, block_rows
from scholarship.domains.offline_workflow import validate_selected, persist_answer
from scholarship.domains.retrieval_quality import substantive, query_plan, FACETS
from scholarship.domains.runs import emit
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T


class State(TypedDict, total=False):
    structured: bool
    round: int
    plan: dict
    candidates: list
    trace: list
    usable: bool
    selected: list
    answer: dict
    retry: bool
    result: dict


def clipped(text, limit):
    return text.encode()[: max(0, limit)].decode("utf-8", errors="ignore")


def parent_context(owner, project, candidates):
    """Replace a child hit with its bounded, exact parent paragraph; never concatenate sources."""
    result, seen = [], set()
    with tenant_transaction(owner) as conn:
        for c in candidates:
            if c["block_id"] in seen:
                continue
            b = (
                conn.execute(
                    sa.select(T["document_blocks"]).where(
                        T["document_blocks"].c.id == UUID(c["block_id"]),
                        T["document_blocks"].c.parse_id == UUID(c["parse_id"]),
                        T["document_blocks"].c.project_id == project,
                    )
                )
                .mappings()
                .one()
            )
            if not substantive(b["canonical_text"], b["kind"]):
                continue
            seen.add(c["block_id"])
            start = max(0, c["block_start"] - 200)
            text = clipped(b["canonical_text"][start:], 1800)
            result.append(
                {
                    **c,
                    "id": f"parent:{c['block_id']}:{start}",
                    "text": text,
                    "block_start": start,
                    "retrieval_mode": "parent_paragraph",
                }
            )
    return result


def section_candidates(owner, project, scope, facets):
    """Scoped section browsing is a source-reading tool, not generated factual evidence."""
    result = []
    for g in scope:
        with tenant_transaction(owner) as conn:
            version_scope(conn, UUID(g["version_id"]))
            valid = conn.scalar(
                sa.select(T["index_generations"].c.id).where(
                    T["index_generations"].c.id == UUID(g["id"]),
                    T["index_generations"].c.project_id == project,
                    T["index_generations"].c.parse_id == UUID(g["parse_id"]),
                    T["index_generations"].c.version_id == UUID(g["version_id"]),
                    T["index_generations"].c.status == "ready",
                )
            )
            if not valid:
                raise ContractError("scope_generation_unavailable")
            rows = block_rows(conn, UUID(g["parse_id"]))
        section, used, references = "", set(), False
        for b in rows:
            text = b["canonical_text"].strip()
            if b["kind"] == "heading":
                section = text
                references = bool(re.search(r"references|bibliography", text, re.I))
                continue
            if references or not substantive(text, b["kind"]):
                continue
            matches = [
                name
                for name, _, _, headings in FACETS
                if name in facets and re.search(headings, section, re.I) and name not in used
            ]
            if not matches and b["page_no"] == 1 and len(text) > 180 and "abstract" not in used:
                matches = ["abstract"]
            if not matches:
                continue
            used.update(matches)
            result.append(
                {
                    "id": "section:" + str(b["id"]),
                    "text": clipped(b["canonical_text"], 1800),
                    "block_id": str(b["id"]),
                    "block_start": 0,
                    "bbox": b["bbox"],
                    "page": b["page_no"],
                    "version_id": g["version_id"],
                    "parse_id": g["parse_id"],
                    "generation_id": g["id"],
                    "retrieval_mode": "section_browse",
                    "facets": matches,
                }
            )
    return result


def run_graph(owner, project, run, row, gateway, *, interrupt_after=None):
    if gateway is None or gateway.selection.snapshot() != row["config_json"]["models"]:
        raise ContractError("workflow_model_snapshot_mismatch")
    policy = row["config_json"]["qa_policy"]
    rounds, maximum, budget = (policy.get(k) for k in ("max_rounds", "max_queries", "context_bytes"))
    if (
        type(rounds) is not int
        or not 1 <= rounds <= 2
        or type(maximum) is not int
        or not 1 <= maximum <= 5
        or type(budget) is not int
        or not 2000 <= budget <= 12000
    ):
        raise ContractError("invalid_qa_policy")
    scope = row["input_scope_json"]["generations"]
    question = row["input_json"]["question"]
    contextual = row["input_json"].get("query_context", question)
    with tenant_transaction(owner) as conn:
        dimensions = conn.scalar(
            sa.select(T["index_profiles"].c.dimensions).where(T["index_profiles"].c.id == UUID(scope[0]["profile_id"]))
        )

    def event(message):
        with tenant_transaction(owner) as conn:
            emit(conn, run, "progress", {"message": message})

    def invoke(key, role, operation, args):
        from dataclasses import asdict

        hashed = {**args, "evidence": [asdict(e) for e in args["evidence"]]} if "evidence" in args else args
        step = call_step(owner, project, run, key, hashed)
        reply = gateway.invoke(key, role, operation, args)
        finish_call(
            owner,
            step,
            {
                "result": reply["value"],
                "usage": reply["usage"],
                "simulated": reply["simulated"],
                "model": reply["requested_model"],
            },
        )
        return reply["value"]

    def plan(state):
        event("正在拆解研究问题，规划跨语言检索与原文章节阅读。")
        from scholarship.domains.structured_retrieval import author_evidence, table_comparison_evidence

        structured = author_evidence(owner, project, scope, question) or table_comparison_evidence(
            owner, project, scope, question
        )
        if structured:
            return {
                "structured": True,
                "answer": structured["answer"],
                "selected": structured["selected"],
                "trace": structured["trace"],
            }
        value = query_plan(contextual)
        return {
            "round": 0,
            "plan": value,
            "trace": [
                {
                    "method": "query_plan",
                    "facets": value["facets"],
                    "queries": value["queries"],
                    "planner": "explicit_research_vocabulary",
                }
            ],
        }

    def retrieve(state):
        n = state["round"]
        event(f"第 {n + 1}/{rounds} 轮：跨语言检索正文，并读取相邻段落。")
        queries = state["plan"]["queries"][:maximum]
        vectors = invoke(
            f"query_embedding:{n}",
            "embedding",
            "embed",
            {"texts": queries, "input_kind": "query", "expected_dimensions": dimensions},
        )
        groups, traces = [], []
        for query, vector in zip(queries, vectors, strict=True):
            r = hybrid(owner, project, scope, query, vector, substantive_only=True)
            groups.append(r["candidates"])
            traces.append({"query": query, "retrieval": r["trace"]})
        found = [group[i] for i in range(20) for group in groups if i < len(group)]
        expanded = parent_context(owner, project, found)
        sections = (
            section_candidates(owner, project, scope, state["plan"]["facets"] or ["problem", "method", "experiment"])
            if n or len(state["plan"]["facets"]) >= 2
            else []
        )
        # Each query gets a fair share; duplicate parent blocks carry no extra weight.
        unique = {}
        for item in [*sections, *expanded]:
            unique.setdefault(item["block_id"], item)
        candidates = list(unique.values())[:20]
        validate_selected(owner, project, candidates)
        return {
            "candidates": candidates,
            "trace": [
                *state["trace"],
                {
                    "method": "retrieval_round",
                    "round": n + 1,
                    "queries": traces,
                    "substantive_parents": len(expanded),
                    "section_blocks": len(sections),
                    "candidate_count": len(candidates),
                },
            ],
        }

    def assess(state):
        count = len(state["candidates"])
        needed = min(3, max(1, len(state["plan"]["facets"])))
        usable = count >= needed
        return {
            "usable": usable,
            "trace": [
                *state["trace"],
                {
                    "method": "evidence_quality_gate",
                    "round": state["round"] + 1,
                    "substantive_blocks": count,
                    "minimum_diverse_blocks": needed,
                    "passed": usable,
                    "semantic_sufficiency_verified": False,
                },
            ],
        }

    def rank(state):
        docs = state["candidates"]
        if not docs:
            return {"selected": []}
        event("正在重排正文证据，控制章节覆盖与上下文长度。")
        ranked = invoke(
            f"rerank:{state['round']}",
            "rerank",
            "rank",
            {"query": question, "documents": [d["text"] for d in docs], "top_n": min(8, len(docs))},
        )
        ranked_docs = [docs[r["index"]] for r in ranked]
        # Broad questions retain actual section evidence rather than eight variants of one abstract.
        anchors = [d for d in docs if d.get("facets")] if len(state["plan"]["facets"]) >= 2 else []
        selected, seen, used = [], set(), 0
        for d in [*anchors, *ranked_docs]:
            if d["block_id"] in seen or len(selected) >= 8:
                continue
            text = clipped(d["text"], budget - used) if used < budget else ""
            if not substantive(text):
                continue
            selected.append({**d, "text": text})
            seen.add(d["block_id"])
            used += len(text.encode())
        return {"selected": selected}

    def answer(state):
        selected = state["selected"]
        validate_selected(owner, project, selected)
        if not selected:
            return {
                "answer": {
                    "answer": "当前索引未提供可核验的正文证据，请检查解析与索引质量。",
                    "claims": [],
                    "insufficient_evidence": True,
                }
            }
        event("正在依据正文生成带引用的回答；不支持的部分应明确说明。")
        value = invoke(
            f"answer:{state['round']}",
            "chat",
            "answer",
            {
                "question": question,
                "evidence": [Evidence(d["id"], d["text"], d["page"]) for d in selected],
                "citation_mode": "quote_id_v2"
                if row["config_json"]["answer_contract"] == "quote-menu-v2"
                else "quote_id",
            },
        )
        return {"answer": value}

    def judge(state):
        retry = bool(state["answer"]["insufficient_evidence"] and state["round"] + 1 < rounds)
        return {
            "retry": retry,
            "trace": [
                *state["trace"],
                {
                    "method": "answer_feedback",
                    "round": state["round"] + 1,
                    "insufficient_evidence": state["answer"]["insufficient_evidence"],
                    "repair_requested": retry,
                },
            ],
        }

    def repair(state):
        event("当前证据覆盖不足，改用章节阅读与更聚焦的英文检索；最多修正一轮。")
        plan = dict(state["plan"])
        expanded = plan["queries"][1:] or ["abstract introduction proposed method experimental evaluation results"]
        plan["queries"] = expanded[:maximum]
        return {"round": state["round"] + 1, "plan": plan, "retry": False}

    def publish(state):
        from scholarship.domains.cancellation import check_cancelled

        check_cancelled(owner, run)
        return {
            "result": persist_answer(owner, project, run, question, state["answer"], state["selected"], state["trace"])
        }

    with psycopg.connect(
        settings().dsn("checkpoint"),
        autocommit=True,
        row_factory=dict_row,
        prepare_threshold=0,
        options="-c search_path=agent",
    ) as conn:
        builder = StateGraph(State)
        for name, node in [
            ("plan", plan),
            ("retrieve", retrieve),
            ("assess", assess),
            ("rank", rank),
            ("answer", answer),
            ("judge", judge),
            ("repair", repair),
            ("publish", publish),
        ]:
            builder.add_node(name, node)
        builder.add_edge(START, "plan")
        builder.add_conditional_edges("plan", lambda s: "publish" if s.get("structured") else "retrieve")
        builder.add_edge("retrieve", "assess")
        builder.add_conditional_edges(
            "assess", lambda s: "repair" if not s["usable"] and s["round"] + 1 < rounds else "rank"
        )
        builder.add_edge("rank", "answer")
        builder.add_edge("answer", "judge")
        builder.add_conditional_edges("judge", lambda s: "repair" if s["retry"] else "publish")
        builder.add_edge("repair", "retrieve")
        builder.add_edge("publish", END)
        graph = builder.compile(checkpointer=PostgresSaver(conn), interrupt_after=interrupt_after)
        cfg = {"configurable": {"thread_id": f"adaptive:{owner}:{run}"}}
        previous = graph.get_state(cfg)
        if previous.values.get("result") and not previous.next:
            return previous.values["result"]
        value = graph.invoke(None if previous.next else {}, cfg, durability="sync")
        return value.get("result")
