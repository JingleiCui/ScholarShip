"""Official-question workflow with PG/Qdrant real and external HTTP mocked."""

from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa

from scholarship.adapters.providers import Evidence, ContractError
from scholarship.models.approvals import issue_approval
from scholarship.models.configuration import load_selection
from scholarship.models.live import LiveExecutor, make_manifest
from scholarship.models.paper_validation import PaperValidation, load_transformer_sample, run_paper_validation
from scholarship.models.adapters import ProviderFailure
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.health import qdrant_client
from test_offline_business import case as case
from test_bounded_live import fake_keys_and_block_external as fake_keys_and_block_external

HARDWARE = "We trained our models on one machine with 8 NVIDIA P100 GPUs."


def plan():
    return PaperValidation(
        {
            "kind": "public_pdf_excerpt",
            "filename": "public-test.pdf",
            "sha256": "a" * 64,
            "locators": [{"evidence_id": "hardware", "pdf_page": 7, "page_text_start": 20}],
        },
        (Evidence("hardware", HARDWARE, 7), Evidence("optimizer", "We used Adam optimizer.", 7)),
        "How many NVIDIA P100 GPUs were used?",
    )


def test_source_offsets_and_manifest_scope(tmp_path, monkeypatch):
    path = tmp_path / "public.pdf"
    path.write_bytes(b"fixture")
    text = (
        "5.2 Hardware\n"
        + HARDWARE
        + "\n5.3 Optimizer\nWe used the Adam optimizer beta 0.9. We varied the learning rate."
    )
    monkeypatch.setattr(
        "scholarship.models.paper_validation.PdfReader",
        lambda path: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: text)]),
    )
    item = load_transformer_sample(path)
    start = item.source["locators"][0]["page_text_start"]
    assert text[start : start + len(item.evidence[0].text)] == item.evidence[0].text
    assert item.source["sha256"] == hashlib.sha256(b"fixture").hexdigest()
    selection = replace(load_selection("free_gemma"), max_output_tokens=256)
    a = make_manifest(selection, item.requests(), data_scope=item.source)
    b = make_manifest(selection, item.requests(), data_scope={**item.source, "sha256": "b" * 64})
    assert a != b and a["data_scope"]["pdf_uploads"] == 0


@pytest.mark.parametrize("failure", [None, "rerank_429", "dimension"])
def test_paper_workflow_real_stores_citations_and_stops_on_failure(case, database, failure):
    owner, project = case
    item = plan()
    selection = replace(load_selection("free_gemma"), max_output_tokens=256, max_requests_per_run=4)
    grant = issue_approval(
        database,
        key=uuid4().hex,
        selection=selection,
        requests=item.requests(),
        total_limit=4,
        owner_id=owner,
        project_id=project,
        approved=True,
        data_scope=item.source,
    )
    sent = []

    def reply(request):
        body = json.loads(request.content)
        sent.append(request.url.path)
        if request.url.path.endswith("/embeddings"):
            count = 8 if not (failure == "dimension" and body["input_type"] == "search_query") else 7
            values = []
            for text in body["input"]:
                vector = [0.0] * count
                vector[0 if "P100" in text else 1] = 1.0
                values.append(vector)
            return httpx.Response(
                200, json={"model": body["model"], "data": [{"index": i, "embedding": v} for i, v in enumerate(values)]}
            )
        if request.url.path.endswith("/rerank"):
            if failure == "rerank_429":
                return httpx.Response(429, headers={"Retry-After": "30"})
            return httpx.Response(
                200,
                json={
                    "model": body["model"],
                    "results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.1}],
                },
            )
        data = {
            "answer": "8 NVIDIA P100 GPUs",
            "insufficient_evidence": False,
            "claims": [
                {
                    "text": "8 NVIDIA P100 GPUs",
                    "citations": [{"evidence_id": "hardware", "quote": HARDWARE, "start": 0, "end": len(HARDWARE)}],
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}],
            },
        )

    executor = LiveExecutor(owner, grant["id"], selection)

    class Gateway:
        def invoke(self, *request):
            return executor.execute(*request, inner=httpx.MockTransport(reply))

    gateway = Gateway()
    gateway.selection = selection
    try:
        if failure:
            with pytest.raises(ProviderFailure if failure == "rerank_429" else ContractError):
                run_paper_validation(gateway, item, owner_id=owner, project_id=project, grant_id=grant["id"])
            if failure == "rerank_429":
                before = list(sent)
                with pytest.raises(ProviderFailure):
                    run_paper_validation(gateway, item, owner_id=owner, project_id=project, grant_id=grant["id"])
                assert sent == before
            assert not any(path.endswith("/chat/completions") for path in sent)
        else:
            result = run_paper_validation(gateway, item, owner_id=owner, project_id=project, grant_id=grant["id"])
            assert result["passed"] and result["business_index_published"] is False
            assert result["citations"][0]["page_text_start"] == 20
            assert result["citations"][0]["quote"] == HARDWARE and result["dimensions"] == 8
            before = list(sent)
            assert run_paper_validation(gateway, item, owner_id=owner, project_id=project, grant_id=grant["id"])[
                "passed"
            ]
            assert sent == before
            with tenant_transaction(owner) as conn:
                assert (
                    conn.scalar(
                        sa.text("SELECT used_requests FROM model_runtime.grants WHERE id=:id"), {"id": grant["id"]}
                    )
                    == 4
                )
    finally:
        client = qdrant_client()
        collection = "ss_live_probe_" + grant["id"].hex
        try:
            if client.collection_exists(collection):
                client.delete_collection(collection)
        finally:
            client.close()


def test_answer_can_change_provider_without_resending_retrieval(case, database, monkeypatch):
    from scholarship.models.gateway import ReuseRetrievalGateway

    owner, project = case
    item = plan()
    original = replace(load_selection("free_gemma"), max_output_tokens=256, max_requests_per_run=4)
    key = uuid4().hex
    parent = issue_approval(
        database,
        key=key,
        selection=original,
        requests=item.requests(),
        total_limit=4,
        owner_id=owner,
        project_id=project,
        approved=True,
        data_scope=item.source,
    )
    original_sent = []

    def original_reply(request):
        body = json.loads(request.content)
        original_sent.append(request.url.path)
        if request.url.path.endswith("/embeddings"):
            vectors = [[1.0, 0.0, 0.0] if "P100" in text else [0.0, 1.0, 0.0] for text in body["input"]]
            return httpx.Response(
                200,
                json={"model": body["model"], "data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]},
            )
        if request.url.path.endswith("/rerank"):
            return httpx.Response(
                200,
                json={
                    "model": body["model"],
                    "results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.1}],
                },
            )
        return httpx.Response(429)

    executor = LiveExecutor(owner, parent["id"], original)
    for request in item.requests()[:-1]:
        executor.execute(*request, inner=httpx.MockTransport(original_reply))
    with pytest.raises(ProviderFailure):
        executor.execute(*item.requests()[-1], inner=httpx.MockTransport(original_reply))
    selected = replace(load_selection("free_api"), max_output_tokens=256, max_requests_per_run=1)
    scope = {"source": item.source, "reuse_grant_id": str(parent["id"])}
    child = issue_approval(
        database,
        key=uuid4().hex,
        selection=selected,
        requests=[item.requests()[-1]],
        total_limit=5,
        prior_keys=[key],
        approved=True,
        data_scope=scope,
    )
    answer_sent = []

    def answer(request):
        assert request.url.host == "api.chatanywhere.tech"
        answer_sent.append(request.url.path)
        content = json.dumps(
            {
                "answer": "8 NVIDIA P100 GPUs",
                "insufficient_evidence": False,
                "claims": [
                    {
                        "text": "8 NVIDIA P100 GPUs",
                        "citations": [{"evidence_id": "hardware", "quote": HARDWARE, "start": 0, "end": len(HARDWARE)}],
                    }
                ],
            }
        )
        return httpx.Response(
            200, json={"model": "gpt-4o-mini", "choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        )

    monkeypatch.setattr("scholarship.models.gateway.network_transport", lambda provider: httpx.MockTransport(answer))
    try:
        with ReuseRetrievalGateway(owner, child["id"], selected, parent["id"], item.source) as gateway:
            result = run_paper_validation(gateway, item, owner_id=owner, project_id=project, grant_id=child["id"])
        assert result["passed"] and answer_sent == ["/v1/chat/completions"] and len(original_sent) == 4
        with tenant_transaction(owner) as conn:
            assert (
                conn.scalar(sa.text("SELECT used_requests FROM model_runtime.grants WHERE id=:id"), {"id": child["id"]})
                == 1
            )
    finally:
        client = qdrant_client()
        try:
            name = "ss_live_probe_" + child["id"].hex
            if client.collection_exists(name):
                client.delete_collection(name)
        finally:
            client.close()
