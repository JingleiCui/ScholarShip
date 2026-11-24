"""Local real-executor contracts on isolated PostgreSQL; no actual inference."""

import json
from uuid import uuid4
import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from scholarship.adapters.providers import ExternalDisabled
from scholarship.domains.offline_workflow import enqueue
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.models.configuration import load_selection
from scholarship.models.local import LocalGateway
from scholarship.models.routing import require_local
from scholarship.models.contracts import ProviderFailure
from scholarship.models.runtime import register_embedding_profile
from scholarship.models.contracts import ModelResult
from test_offline_business import case as case


@pytest.mark.parametrize("invalid", [None, "missing_prefix", "wrong_quote_pair"])
def test_quote_v2_constrains_short_ids_and_maps_only_exact_source_pairs(invalid):
    from scholarship.models.adapters import ProviderClient
    from scholarship.adapters.providers import Evidence, ContractError

    selection = load_selection("local")
    sources = [
        Evidence("parent:original-source-uuid:0", "Layer normalization normalizes one training case. 中文 $x^2$.", 1),
        Evidence("section:another-source-uuid", "A longer original evidence paragraph. " * 20, 2),
    ]
    seen = []

    def reply(request):
        wire = json.loads(request.content)
        seen.append(wire)
        payload = json.loads(wire["messages"][-1]["content"])
        assert [e["id"] for e in payload["evidence"]] == ["e0", "e1"]
        assert all(e.id not in json.dumps(wire) for e in sources)
        pairs = wire["format"]["properties"]["claims"]["items"]["properties"]["citations"]["items"]["anyOf"]
        assert pairs[0]["properties"]["evidence_id"]["const"] == "e0"
        assert pairs[0]["properties"]["quote_id"]["enum"] == ["q0"]
        assert "q1" in pairs[1]["properties"]["quote_id"]["enum"]
        citation = {"evidence_id": "e0", "quote_id": "q0"}
        if invalid == "missing_prefix":
            citation["evidence_id"] = "original-source-uuid:0"
        elif invalid == "wrong_quote_pair":
            citation["quote_id"] = "q1"
        value = {
            "answer": "Layer normalization uses per-case statistics.",
            "insufficient_evidence": False,
            "claims": [{"text": "per-case statistics", "citations": [citation]}],
        }
        return httpx.Response(
            200,
            json={
                "model": selection.roles["chat"].model,
                "done": True,
                "message": {"content": json.dumps(value)},
                "prompt_eval_count": 20,
                "eval_count": 20,
            },
        )

    client = ProviderClient(selection.roles["chat"], "chat", transport=httpx.MockTransport(reply))
    try:
        if invalid:
            with pytest.raises(ContractError, match="invalid_quote_menu_citation"):
                client.answer("有什么创新？", sources, citation_mode="quote_id_v2")
        else:
            result = client.answer("有什么创新？", sources, citation_mode="quote_id_v2")
            cite = result.value["claims"][0]["citations"][0]
            assert cite["evidence_id"] == sources[0].id and cite["quote"] == sources[0].text
        assert len(seen) == 1  # A bad response never triggers an implicit repair request.
    finally:
        client.close()


def pinned(case):
    owner, project = case
    selection = load_selection("local")
    run = enqueue(owner, project, "research_build", uuid4().hex, {}, dispatch=False)["id"]
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == run)
            .values(config_json={"mode": "local_inference", "models": selection.snapshot()})
        )
    return owner, project, run, selection


def test_local_gate_is_independent_but_cannot_select_cloud():
    require_local(load_selection("local"), ("chat", "embedding", "rerank"))
    with pytest.raises(ExternalDisabled, match="local_provider_required"):
        require_local(load_selection("free_api"), ("chat",))


def test_local_receipt_idempotency_profile_and_no_cloud_budget(case):
    owner, project, run, selection = pinned(case)
    sent = []

    def response(request):
        sent.append(request)
        assert request.url.host == "127.0.0.1" and "authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "model": selection.roles["embedding"].model,
                "embeddings": [[1.0] + [0.0] * 1023],
                "prompt_eval_count": 5,
            },
        )

    with LocalGateway(
        owner, project, run, selection, transport_factory=lambda _: httpx.MockTransport(response)
    ) as gateway:
        result = gateway.invoke("embed", "embedding", "embed", {"texts": ["local fixture"]})
        assert gateway.invoke("embed", "embedding", "embed", {"texts": ["local fixture"]}) == result
    assert len(sent) == 1 and result["simulated"] is False
    with tenant_transaction(owner) as conn:
        call = conn.scalar(sa.text("SELECT id FROM model_runtime.calls WHERE run_id=:r"), {"r": run})
        assert (
            conn.scalar(
                sa.text("SELECT count(*) FROM model_runtime.local_admissions WHERE call_id=:c AND settled"), {"c": call}
            )
            == 1
        )
        assert (
            conn.scalar(
                sa.text("SELECT count(*) FROM model_runtime.production_admissions WHERE call_id=:c"), {"c": call}
            )
            == 0
        )
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(T["external_calls"])
                .where(T["external_calls"].c.project_id == project)
            )
            == 0
        )
    model_result = ModelResult(**{k: v for k, v in result.items() if k != "cost_verification"})
    assert register_embedding_profile(owner, selection.roles["embedding"], model_result, call_id=call)
    with pytest.raises(DBAPIError), tenant_transaction(uuid4()) as conn:
        conn.execute(sa.text("SELECT model_runtime.settle_local(:id,5)"), {"id": call})


def test_unknown_local_call_never_resent(case):
    owner, project, run, selection = pinned(case)
    sent = []

    def fail(request):
        sent.append(request)
        raise httpx.ReadTimeout("fixture")

    with LocalGateway(owner, project, run, selection, transport_factory=lambda _: httpx.MockTransport(fail)) as gateway:
        for _ in range(2):
            with pytest.raises(ProviderFailure):
                gateway.invoke("one", "embedding", "embed", {"texts": ["x"]})
    assert len(sent) == 1


def test_ollama_figure_uses_images_and_schema():
    from scholarship.models.adapters import ProviderClient
    import base64

    selection = load_selection("local")

    def reply(request):
        body = json.loads(request.content)
        assert body["messages"][-1]["images"] and isinstance(body["format"], dict)
        assert body["think"] is False
        assert body["format"]["$defs"]["Node"]["properties"]["bbox"]["items"]["enum"] == [
            round(i / 100, 2) for i in range(101)
        ]
        description = {
            "figure_type": "unreadable",
            "visible_text": [],
            "nodes": [],
            "edges": [],
            "summary": "",
            "uncertainties": ["fixture"],
        }
        return httpx.Response(
            200,
            json={
                "model": selection.roles["chat"].model,
                "done": True,
                "message": {"content": json.dumps(description)},
            },
        )

    with_client = ProviderClient(selection.roles["chat"], "chat", transport=httpx.MockTransport(reply))
    try:
        result = with_client.describe_figure(base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode(), "image/png")
        assert result.value["figure_type"] == "unreadable"
    finally:
        with_client.close()


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"models": [{"name": "chat"}]}, "models_missing"),
        ({"models": [{"name": "chat"}, {"name": "embed"}]}, "models_present"),
        ([], "invalid_response"),
    ],
)
def test_inventory_is_read_only_not_inference(payload, expected):
    from local_models_doctor import inspect_ollama

    def reply(request):
        assert request.method == "GET" and request.url.path == "/api/tags"
        assert "authorization" not in request.headers
        return httpx.Response(200, json=payload)

    result = inspect_ollama("http://127.0.0.1:11434", {"chat", "embed"}, transport=httpx.MockTransport(reply))
    assert result["state"] == expected
    assert not result.get("inference_verified", False)


@pytest.mark.parametrize(
    "response,state",
    [
        (httpx.Response(302, headers={"location": "https://example.com"}), "http_error"),
        (httpx.Response(200, content=b"x" * (1024 * 1024 + 1)), "response_too_large"),
    ],
)
def test_inventory_limits(response, state):
    from local_models_doctor import inspect_ollama

    assert (
        inspect_ollama("http://127.0.0.1:11434", {"chat"}, transport=httpx.MockTransport(lambda _: response))["state"]
        == state
    )


def test_inventory_rejects_remote_endpoint():
    from local_models_doctor import inspect_ollama

    result = inspect_ollama(
        "https://example.com", {"chat"}, transport=httpx.MockTransport(lambda _: pytest.fail("network forbidden"))
    )
    assert result["state"] == "invalid_local_endpoint"


def test_invalid_local_contract_retains_diagnostic_file(case):
    from scholarship.adapters.providers import ContractError
    import base64

    owner, project, run, selection = pinned(case)

    def reply(request):
        return httpx.Response(
            200,
            json={
                "model": selection.roles["chat"].model,
                "done": True,
                "message": {"content": '{"invalid":"fixture"}'},
                "prompt_eval_count": 4,
                "eval_count": 4,
            },
        )

    with LocalGateway(
        owner, project, run, selection, transport_factory=lambda _: httpx.MockTransport(reply)
    ) as gateway:
        with pytest.raises(ContractError):
            gateway.invoke(
                "bad-vision",
                "chat",
                "describe_figure",
                {"image_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode(), "mime": "image/png"},
            )
    with tenant_transaction(owner) as conn:
        row = (
            conn.execute(sa.text("SELECT state,receipt_json FROM model_runtime.calls WHERE run_id=:r"), {"r": run})
            .mappings()
            .one()
        )
        assert row["state"] == "unknown" and row["receipt_json"]["diagnostic_file_id"]


def test_usage_reconciliation_keeps_successful_receipt_without_profile(case):
    owner, project, run, selection = pinned(case)

    def reply(request):
        return httpx.Response(200, json={"embeddings": [[1.0] + [0.0] * 1023], "prompt_eval_count": 99999999})

    with LocalGateway(
        owner, project, run, selection, transport_factory=lambda _: httpx.MockTransport(reply)
    ) as gateway:
        with pytest.raises(ProviderFailure, match="local_settlement_reconciliation_required"):
            gateway.invoke("usage", "embedding", "embed", {"texts": ["fixture"]})
    with tenant_transaction(owner) as conn:
        row = (
            conn.execute(sa.text("SELECT id,state FROM model_runtime.calls WHERE run_id=:r"), {"r": run})
            .mappings()
            .one()
        )
        assert row["state"] == "succeeded"
        assert not conn.scalar(
            sa.text("SELECT settled FROM model_runtime.local_admissions WHERE call_id=:c"), {"c": row["id"]}
        )
