"""Whole synthetic evidence flow against real PG/Qdrant and fake provider HTTP."""

from dataclasses import replace
import json
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa

from scholarship.models.configuration import load_selection
from scholarship.models.gateway import SimulatedGateway
from scholarship.models.adapters import ProviderFailure
from scholarship.domains.indexing import build_index, hybrid, snapshot
from scholarship.domains.offline_workflow import enqueue_model_qa, qa_graph
from scholarship.adapters.providers import ContractError
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from test_offline_business import case as case, prepared, enqueue


class SyntheticProvider:
    def __init__(self, selection):
        self.selection = selection
        self.sent = []
        self.embedding_inputs = []
        self.fail_role = None

    def transport(self, role):
        def reply(request):
            body = json.loads(request.content)
            self.sent.append(role)
            if self.fail_role == role:
                raise httpx.ReadTimeout("private request content")
            model = self.selection.roles[role].model
            if role == "embedding":
                self.embedding_inputs.extend(body["input"])
                dimensions = self.selection.roles[role].dimensions or 4096
                vectors = []
                for text in body["input"]:
                    vector = [0.0] * dimensions
                    vector[0 if "Alpha" in text else 1 if "Beta" in text else 2] = 1.0
                    vectors.append(vector)
                if request.url.path.endswith("/api/embed"):
                    return httpx.Response(200, json={"model": model, "embeddings": vectors})
                return httpx.Response(
                    200,
                    json={
                        "model": model,
                        "data": [{"index": i, "embedding": vector} for i, vector in enumerate(vectors)],
                    },
                )
            if role == "rerank":
                order = sorted(
                    range(len(body["documents"])),
                    key=lambda i: ("Alpha" not in body["documents"][i], "91 percent" not in body["documents"][i], i),
                )
                return httpx.Response(
                    200,
                    json={
                        "model": model,
                        "results": [
                            {"index": i, "relevance_score": 1 / (rank + 1)}
                            for rank, i in enumerate(order[: body["top_n"]])
                        ],
                    },
                )
            evidence = json.loads(body["messages"][-1]["content"])["evidence"][0]
            quote = evidence["text"]
            text = json.dumps(
                {
                    "answer": "91 percent",
                    "insufficient_evidence": False,
                    "claims": [
                        {
                            "text": "91 percent",
                            "citations": [
                                {"evidence_id": evidence["id"], "quote": quote, "start": 0, "end": len(quote)}
                            ],
                        }
                    ],
                }
            )
            if request.url.path.endswith("/api/chat"):
                return httpx.Response(200, json={"model": model, "done": True, "message": {"content": text}})
            return httpx.Response(
                200, json={"model": model, "choices": [{"finish_reason": "stop", "message": {"content": text}}]}
            )

        return httpx.MockTransport(reply)


def indexed(case, selection):
    owner, project = case
    versions = prepared(case)
    provider = SyntheticProvider(selection)
    run = enqueue(owner, project, "offline_build", uuid4().hex, {})["id"]
    with SimulatedGateway(owner, run, selection, provider.transport) as gateway:
        for vid in versions:
            with tenant_transaction(owner) as conn:
                pid = conn.scalar(
                    sa.select(T["paper_versions"].c.active_parse_id).where(T["paper_versions"].c.id == vid)
                )
            build_index(owner, vid, pid, run, model_selection=selection, model_client=gateway.client("embedding"))
    return versions, provider


@pytest.mark.parametrize("preset", ["free_gemma", "free_api", "local"])
def test_switchable_pipeline_indexes_queries_reranks_cites_and_resumes(case, preset):
    selection = load_selection(preset)
    owner, project = case
    versions, provider = indexed(case, selection)
    assert provider.embedding_inputs.count("SYNTHETIC FIXTURE A. This is not a research paper.") == 1
    run = enqueue_model_qa(owner, project, uuid4().hex, versions, "What is Alpha accuracy?", selection)["id"]
    with SimulatedGateway(owner, run, selection, provider.transport) as gateway:
        assert qa_graph(owner, project, run, model_gateway=gateway, interrupt_after=["rerank"]) is None
    sent_before = list(provider.sent)
    with SimulatedGateway(owner, run, selection, provider.transport) as gateway:
        result = qa_graph(owner, project, run, model_gateway=gateway)
        assert result["answer"] == "91 percent" and result["synthetic"] is True
        assert "91 percent" in result["citations"][0]["quote"]
        assert qa_graph(owner, project, run, model_gateway=gateway) == result
    assert provider.sent == sent_before + ["chat"]
    with tenant_transaction(owner) as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.count()).select_from(T["evidence_items"]).where(T["evidence_items"].c.run_id == run)
            )
            >= 1
        )
    scope = snapshot(owner, project, versions, model_selection=selection)
    with pytest.raises(ContractError, match="query_vector_profile_mismatch"):
        hybrid(owner, project, scope, "Alpha", [1.0, 0.0])
    changed = replace(
        selection,
        roles={
            **selection.roles,
            "embedding": load_selection("local" if preset != "local" else "free_api").roles["embedding"],
        },
    )
    with pytest.raises(ContractError, match="embedding_profile_requires_rebuild"):
        snapshot(owner, project, versions, model_selection=changed)


def test_pipeline_unknown_does_not_resend_or_publish(case):
    selection = load_selection("free_gemma")
    owner, project = case
    versions, provider = indexed(case, selection)
    run = enqueue_model_qa(owner, project, uuid4().hex, versions, "Alpha accuracy?", selection)["id"]
    provider.fail_role = "embedding"
    for _ in range(2):
        with SimulatedGateway(owner, run, selection, provider.transport) as gateway:
            with pytest.raises(ProviderFailure):
                qa_graph(owner, project, run, model_gateway=gateway)
    assert provider.sent[-1] == "embedding"
    with tenant_transaction(owner) as conn:
        assert (
            conn.scalar(
                sa.text("SELECT count(*) FROM model_runtime.calls WHERE run_id=:r AND state='unknown'"), {"r": run}
            )
            == 1
        )
        assert (
            conn.scalar(
                sa.select(sa.func.count()).select_from(T["evidence_items"]).where(T["evidence_items"].c.run_id == run)
            )
            == 0
        )
