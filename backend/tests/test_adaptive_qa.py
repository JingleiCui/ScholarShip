"""Real scoped storage/checkpoints with scripted, explicitly simulated model responses."""

import json
from uuid import UUID, uuid4
import httpx
import pytest
import sqlalchemy as sa
from scholarship.domains.adaptive_qa import run_graph, clipped
from scholarship.domains.research import enqueue_research, project_selection
from scholarship.domains.retrieval_quality import query_plan, index_chunks
from scholarship.domains.studio.document_engine import text_blocks
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.models.fixtures import transport_factory
from scholarship.models.gateway import SimulatedGateway
from scholarship.models.contracts import ProviderFailure
from scholarship.workers.offline import execute_offline
from test_live_business import imported
from test_offline_business import case as case


def test_overlapping_baselines_merge_but_columns_and_headings_stay_separate():
    words = []
    for text, x, top in [
        ("The proposed method improves training stability.", 40, 50),
        ("It uses statistics from each training example.", 40, 59.4),
        ("A separate column must keep its own paragraph.", 330, 50),
    ]:
        words.append(dict(text=text, x0=x, x1=x + 220, top=top, bottom=top + 10))
    merged = text_blocks(words, "pdf-text-layer", paragraphs=True)
    assert any("stability.\nIt uses" in b["text"] for b in merged)
    assert len(merged) == 2


def test_new_index_filters_fragments_without_altering_original_spans_or_legacy():
    block = {
        "canonical_text": "7 Conclusion",
        "kind": "heading",
        "structured_json": {"pipeline_policy": {"revision": "local-regions-ocr-v3"}},
    }
    assert not list(index_chunks(block))
    assert list(index_chunks({**block, "structured_json": {}}))
    prose = "The proposed method uses statistics computed from each training case. " * 30
    for a, z, text, size in index_chunks({**block, "kind": "paragraph", "canonical_text": prose}):
        assert prose[a:z] == text and len(text.encode()) == size
    assert clipped("甲😀乙", 5) == "甲"
    plan = query_plan("文章解决什么问题，创新点、方法、实验是什么？")
    assert len(plan["facets"]) == 4 and any("experiments" in q for q in plan["queries"])


def setup_run(case, client):
    owner, project = case
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["projects"]
            .update()
            .where(T["projects"].c.id == project)
            .values(preferences={"model_configuration": {"preset": "mock"}})
        )
    vid, _ = imported(case, client)
    build = enqueue_research(owner, project, uuid4().hex, "research_build", [vid], dispatch=False)
    execute_offline(str(owner), str(project), str(build["id"]))
    run = enqueue_research(
        owner, project, uuid4().hex, "research_qa", [vid], question="NVIDIA P100 GPUs?", dispatch=False
    )
    with tenant_transaction(owner) as conn:
        row = dict(conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == run["id"])).mappings().one())
        row["config_json"] = {
            **row["config_json"],
            "qa_policy": {"revision": "adaptive-evidence-v1", "max_rounds": 2, "max_queries": 5, "context_bytes": 8000},
        }
        conn.execute(T["runs"].update().where(T["runs"].c.id == run["id"]).values(config_json=row["config_json"]))
    return run["id"], row


@pytest.mark.parametrize("always_insufficient", [False, True])
def test_answer_feedback_repairs_once_and_preserves_exact_citations(case, signed_client, always_insufficient):
    owner, project = case
    run, row = setup_run(case, signed_client)
    selection = project_selection(owner, project)
    factory = transport_factory(selection)
    chats = []

    def transport(role):
        if role != "chat":
            return factory(role)

        def reply(request):
            payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
            chats.append(payload)
            value = {"answer": "Insufficient evidence", "claims": [], "insufficient_evidence": True}
            if len(chats) == 2 and not always_insufficient:
                evidence = next(e for e in payload["evidence"] if "P100" in e["text"])
                value = {
                    "answer": "The source reports 8 NVIDIA P100 GPUs.",
                    "insufficient_evidence": False,
                    "claims": [
                        {
                            "text": "The source reports 8 NVIDIA P100 GPUs.",
                            "citations": [{"evidence_id": evidence["id"], "quote_id": "q0"}],
                        }
                    ],
                }
            return httpx.Response(
                200,
                json={
                    "model": selection.roles["chat"].model,
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                    "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
                },
            )

        return httpx.MockTransport(reply)

    with SimulatedGateway(owner, run, selection, transport) as gateway:
        # Stop at a checkpoint, then resume with the same durable call intents.
        assert run_graph(owner, project, run, row, gateway, interrupt_after=["judge"]) is None
        result = run_graph(owner, project, run, row, gateway)
    assert len(chats) == 2
    feedback = [t for t in result["trace"] if t["method"] == "answer_feedback"]
    assert len(feedback) == 2 and feedback[0]["repair_requested"] and not feedback[1]["repair_requested"]
    assert result["insufficient_evidence"] == always_insufficient
    if always_insufficient:
        assert result["citations"] == []
    else:
        assert result["citations"] and "P100" in result["citations"][0]["quote"]
        with tenant_transaction(owner) as conn:
            cite = result["citations"][0]
            text = conn.scalar(
                sa.select(T["document_blocks"].c.canonical_text).where(
                    T["document_blocks"].c.id == UUID(cite["block_id"])
                )
            )
            assert cite["quote"] in text
    assert result["model_simulated"] is True


def test_unknown_transport_failure_propagates_without_repair_or_second_call(case, signed_client):
    owner, project = case
    run, row = setup_run(case, signed_client)

    class Unknown:
        selection = project_selection(owner, project)
        calls = 0

        def invoke(self, *args):
            self.calls += 1
            raise ProviderFailure("injected_unknown", unknown=True)

    gateway = Unknown()
    with pytest.raises(ProviderFailure):
        run_graph(owner, project, run, row, gateway)
    assert gateway.calls == 1
