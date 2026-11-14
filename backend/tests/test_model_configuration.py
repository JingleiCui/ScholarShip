"""Provider protocols tested against MockTransport; no real inference or uploads."""

from dataclasses import replace
import json
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from scholarship.adapters.providers import ContractError, ExternalDisabled, Evidence
from scholarship.models.configuration import load_selection, restore_snapshot, configuration_status
from scholarship.models.adapters import ProviderClient, ProviderFailure
from scholarship.models.runtime import ModelExecutor
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.domains.indexing import build_index
from test_offline_business import case as case, prepared, enqueue


def provider(role, handler, preset="free_api", **kwargs):
    return ProviderClient(load_selection(preset).roles[role], role, transport=httpx.MockTransport(handler), **kwargs)


def chat_response(content="hello", **extra):
    return {"model": "gpt-4o-mini", "choices": [{"finish_reason": "stop", "message": {"content": content}}], **extra}


def test_presets_snapshot_no_secrets_and_compatibility():
    selection = load_selection()
    assert selection.preset == "free_gemma"
    assert selection.roles["embedding"].dimensions is None
    assert restore_snapshot(selection.snapshot()).snapshot() == selection.snapshot()
    assert configuration_status()["roles"]["embedding"]["dimensions_state"] == "pending_first_valid_receipt"
    changed = load_selection("local")
    assert changed.snapshot()["fingerprint"] != selection.snapshot()["fingerprint"]
    body = selection.snapshot()
    body["roles"]["chat"]["model"] = "tampered"
    with pytest.raises(ValueError):
        restore_snapshot(body)


@pytest.mark.parametrize("preset", ["mock", "local", "free_api", "free_gemma"])
def test_valid_presets_and_individual_overrides(preset):
    selection = load_selection(preset)
    assert set(selection.roles) == {"chat", "translation", "embedding", "rerank", "parser"}
    override = load_selection(preset, {"parser": "mineru_cloud"})
    assert override.roles["parser"].provider == "mineru_cloud"
    assert override.roles["embedding"] == selection.roles["embedding"]


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "nvidia/nemotron-3.5-embed-8b"},
        {"base_url": "https://evil.example/api/v1"},
        {"base_url": "https://secret@openrouter.ai/api/v1"},
        {"base_url": "https://openrouter.ai/api/v1?api_key=secret"},
    ],
)
def test_disallow_paid_routes_and_untrusted_hosts(changes):
    with pytest.raises(ValueError):
        replace(load_selection().roles["embedding"], **changes).validate("embedding")


def test_no_live_transport_even_if_flags_changed(monkeypatch):
    monkeypatch.setenv("EXTERNAL_SERVICES_ENABLED", "true")
    with pytest.raises(ExternalDisabled):
        ProviderClient(load_selection().roles["chat"], "chat")
    with pytest.raises(ExternalDisabled):
        ProviderClient(load_selection().roles["chat"], "chat", transport=httpx.HTTPTransport())


def test_chat_generic_parameters_and_unknown_usage():
    seen = []

    def handler(r):
        seen.append(json.loads(r.content))
        return httpx.Response(200, json=chat_response())

    result = provider("chat", handler).generate([{"role": "user", "content": "hello"}])
    assert result.value == "hello" and result.usage == {"input_tokens": None, "output_tokens": None}
    assert result.provider == "chatanywhere" and result.requested_model == "gpt-4o-mini" and result.simulated
    assert "thinking" not in seen[0] and "tools" not in seen[0] and seen[0]["stream"] is False
    totals = provider(
        "chat", lambda r: httpx.Response(200, json=chat_response(usage={"total_tokens": 12, "completion_tokens": 4}))
    ).generate([{"role": "user", "content": "hello"}])
    assert totals.usage == {"input_tokens": None, "output_tokens": 4, "total_tokens": 12}


@pytest.mark.parametrize(
    "code,expected",
    [
        (401, "authentication_failed"),
        (403, "permission_denied"),
        (404, "model_or_endpoint_unavailable"),
        (429, "rate_limited"),
        (500, "provider_http_error"),
        (302, "provider_http_error"),
    ],
)
def test_no_retry_or_fallback_on_http_errors(code, expected):
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(code, headers={"Retry-After": "60"}, json={"error": "sensitive vendor body"})

    with pytest.raises(ProviderFailure) as e:
        provider("chat", handler).generate([{"role": "user", "content": "x"}])
    assert e.value.code == expected and len(calls) == 1 and "sensitive" not in str(e.value)
    assert e.value.unknown == (code >= 500)


def test_timeout_retains_unknown():
    def handler(r):
        raise httpx.ReadTimeout("private request data")

    with pytest.raises(ProviderFailure) as e:
        provider("chat", handler).generate([{"role": "user", "content": "x"}])
    assert e.value.unknown and "private" not in str(e.value)
    assert e.value.transport_kind == "read_timeout"


@pytest.mark.parametrize("kind", ["truncated", "tool", "empty", "json"])
def test_bad_chat_outputs_rejected(kind):
    d = chat_response()
    if kind == "truncated":
        d["choices"][0]["finish_reason"] = "length"
    elif kind == "tool":
        d["choices"][0]["message"]["tool_calls"] = [{}]
    elif kind == "empty":
        d["choices"][0]["message"]["content"] = ""
    else:
        with pytest.raises(ContractError):
            provider("chat", lambda r: httpx.Response(200, content=b"not json")).generate(
                [{"role": "user", "content": "x"}]
            )
        return
    with pytest.raises(ContractError):
        provider("chat", lambda r: httpx.Response(200, json=d)).generate([{"role": "user", "content": "x"}])


def test_exact_unicode_citations_and_json_mode():
    e = Evidence("e1", "研究🙂 accuracy is 91%.", 2)
    answer = {
        "answer": "91%",
        "insufficient_evidence": False,
        "claims": [
            {"text": "91%", "citations": [{"evidence_id": "e1", "quote": e.text, "start": 0, "end": len(e.text)}]}
        ],
    }
    seen = []

    def handler(r):
        seen.append(json.loads(r.content))
        return httpx.Response(200, json=chat_response(json.dumps(answer)))

    result = provider("chat", handler).answer("accuracy?", [e])
    assert result.value["claims"][0]["citations"][0]["page"] == 2
    assert seen[0]["response_format"] == {"type": "json_object"}
    answer["claims"][0]["citations"][0]["quote"] = "invented"
    with pytest.raises(ContractError):
        provider("chat", handler).answer("accuracy?", [e])


def test_no_evidence_no_request():
    def fail(r):
        raise AssertionError("must not send")

    result = provider("chat", fail).answer("x", [])
    assert result.value["insufficient_evidence"]


def test_ollama_chat_endpoint_without_cloud_headers():
    def handler(r):
        body = json.loads(r.content)
        assert r.url.path == "/api/chat" and "Authorization" not in r.headers
        assert body["think"] is False and body["options"]["num_ctx"] == 8192
        return httpx.Response(
            200,
            json={
                "model": "qwen3.5:9b",
                "message": {"content": "hello"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 3,
                "eval_count": 1,
            },
        )

    result = provider("chat", handler, "local").generate([{"role": "user", "content": "hello"}])
    assert result.usage["input_tokens"] == 3


def embedding_response(texts, dim=3):
    return {
        "model": "nvidia/nemotron-3.5-embed-8b:free",
        "data": [{"index": i, "embedding": [1.0] + [0.0] * (dim - 1)} for i, _ in enumerate(texts)],
    }


def test_embedding_native_dimensions_and_query_document_modes():
    seen = []

    def handler(r):
        d = json.loads(r.content)
        seen.append(d)
        assert r.url.path == "/api/v1/embeddings"
        return httpx.Response(200, json=embedding_response(d["input"], 5))

    client = provider("embedding", handler)
    result = client.embed(["x"])
    assert result.dimensions == 5 and len(result.value[0]) == 5
    assert "dimensions" not in seen[0] and seen[0]["input_type"] == "search_document"
    client.embed(["q"], input_kind="query")
    assert seen[-1]["input_type"] == "search_query"
    with pytest.raises(ContractError):
        client.embed(["x"], expected_dimensions=1024)


@pytest.mark.parametrize("invalid", ["index", "nan", "zero", "mixed", "model"])
def test_embedding_shape_guards(invalid):
    d = embedding_response(["x", "y"])
    if invalid == "index":
        d["data"][1]["index"] = 0
    elif invalid == "nan":
        d["data"][0]["embedding"][0] = float("nan")
    elif invalid == "zero":
        d["data"][0]["embedding"] = [0, 0, 0]
    elif invalid == "mixed":
        d["data"][0]["embedding"] = [1, 0]
    else:
        d["model"] = "paid-other-model"
    # httpx JSON encoding rejects NaN itself; use a raw malformed vendor body.
    with pytest.raises(ContractError):
        provider("embedding", lambda r: httpx.Response(200, content=json.dumps(d).encode())).embed(["x", "y"])


def test_rerank_endpoint_and_index_validation():
    def handler(r):
        assert r.url.path == "/api/v1/rerank"
        return httpx.Response(
            200, json={"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}]}
        )

    result = provider("rerank", handler).rank("query", ["a", "b"], 2)
    assert result.value[0]["index"] == 1 and result.usage["input_tokens"] is None
    with pytest.raises(ContractError):
        provider("rerank", lambda r: httpx.Response(200, json={"results": [{"index": 8, "relevance_score": 1}]})).rank(
            "q", ["a"], 1
        )


def test_execution_receipt_idempotency_and_no_monetary_charge(case):
    owner, project = case
    run = enqueue(owner, project, "offline_build", uuid4().hex, {})["id"]
    sent = []

    def handler(r):
        sent.append(r)
        return httpx.Response(200, json=chat_response())

    c = provider("chat", handler)
    selection = load_selection("free_api")
    executor = ModelExecutor(owner)
    args = {"messages": [{"role": "user", "content": "hello"}]}
    first = executor.execute(run, "chat", selection, "chat", c, args, "generate")
    assert executor.execute(run, "chat", selection, "chat", c, args, "generate") == first and len(sent) == 1
    with pytest.raises(DBAPIError):
        executor.execute(
            run, "chat", selection, "chat", c, {"messages": [{"role": "user", "content": "different"}]}, "generate"
        )
    with tenant_transaction(owner) as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM model_runtime.calls WHERE run_id=:r"), {"r": run}) == 1
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(T["external_calls"])
                .where(
                    T["external_calls"].c.step_id.in_(
                        sa.select(T["run_steps"].c.id).where(T["run_steps"].c.run_id == run)
                    )
                )
            )
            == 0
        )


def test_unknown_not_resent_and_request_quota(case):
    owner, project = case
    run = enqueue(owner, project, "offline_build", uuid4().hex, {})["id"]
    calls = []

    def handler(r):
        calls.append(r)
        raise httpx.ReadTimeout("timeout")

    selection = replace(load_selection("free_api"), max_requests_per_run=1)
    client = provider("chat", handler)
    e = ModelExecutor(owner)
    args = {"messages": [{"role": "user", "content": "x"}]}
    with pytest.raises(ProviderFailure):
        e.execute(run, "unknown", selection, "chat", client, args, "generate")
    with pytest.raises(ProviderFailure):
        e.execute(run, "unknown", selection, "chat", client, args, "generate")
    assert len(calls) == 1
    with pytest.raises(DBAPIError):
        e.execute(run, "another", selection, "chat", client, args, "generate")


def test_profile_switch_rebuild_failure_preserves_old_index(case):
    owner, project = case
    vid = prepared(case)[0]
    with tenant_transaction(owner) as conn:
        old = conn.scalar(sa.select(T["paper_versions"].c.active_index_id).where(T["paper_versions"].c.id == vid))
        pid = conn.scalar(sa.select(T["paper_versions"].c.active_parse_id).where(T["paper_versions"].c.id == vid))
    selection = load_selection()
    run = enqueue(owner, project, "offline_build", uuid4().hex, {})["id"]

    def handler(r):
        return httpx.Response(200, json=embedding_response(json.loads(r.content)["input"], 5))

    client = provider("embedding", handler)

    def crash(g):
        raise RuntimeError("before-pointer-switch")

    with pytest.raises(RuntimeError):
        build_index(owner, vid, pid, run, model_selection=selection, model_client=client, after_upsert=crash)
    with tenant_transaction(owner) as conn:
        assert (
            conn.scalar(sa.select(T["paper_versions"].c.active_index_id).where(T["paper_versions"].c.id == vid)) == old
        )
    new = build_index(owner, vid, pid, run, model_selection=selection, model_client=client)
    assert new != old
    with tenant_transaction(owner) as conn:
        g = conn.execute(sa.select(T["index_generations"]).where(T["index_generations"].c.id == new)).mappings().one()
        p = (
            conn.execute(sa.select(T["index_profiles"]).where(T["index_profiles"].c.id == g["profile_id"]))
            .mappings()
            .one()
        )
        assert p["dimensions"] == 5 and p["config_json"]["simulated"] is True and g["status"] == "ready"
        assert (
            conn.scalar(sa.select(T["index_generations"].c.status).where(T["index_generations"].c.id == old)) == "ready"
        )


def test_model_configuration_api_and_scope(signed_client, accounts):
    project = accounts["researcher"]["project"]["id"]
    base = f"/api/v1/projects/{project}/model-configuration"
    before = signed_client.get(base)
    assert before.status_code == 200
    key = before.json()["fingerprint"]
    r = signed_client.put(base, json={"preset": "local", "expected_fingerprint": key})
    assert r.status_code == 200, r.text
    assert r.json()["requires_reindex"] and r.json()["active_indexes_unchanged"] and not r.json()["execution_enabled"]
    assert signed_client.put(base, json={"preset": "mock", "expected_fingerprint": key}).status_code == 409
    assert (
        signed_client.get(f"/api/v1/projects/{accounts['reviewer']['project']['id']}/model-configuration").status_code
        == 404
    )
    assert (
        signed_client.put(
            base,
            json={
                "preset": "free_api",
                "expected_fingerprint": r.json()["fingerprint"],
                "overrides": {"chat": "http://evil"},
            },
        ).status_code
        == 422
    )
    assert signed_client.get("/api/v1/system/models").json()["live_validation_authorized"] is False


def test_call_records_owner_rls_and_direct_write_denied(case, accounts):
    owner, project = case
    run = enqueue(owner, project, "offline_build", uuid4().hex, {})["id"]
    selection = load_selection("free_api")
    client = provider("chat", lambda r: httpx.Response(200, json=chat_response()))
    ModelExecutor(owner).execute(
        run, "one", selection, "chat", client, {"messages": [{"role": "user", "content": "hello"}]}, "generate"
    )
    foreign = accounts["reviewer"]["user"]["id"]
    with tenant_transaction(foreign) as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM model_runtime.calls WHERE run_id=:id"), {"id": run}) == 0
    with pytest.raises(DBAPIError), tenant_transaction(owner) as conn:
        conn.execute(sa.text("UPDATE model_runtime.calls SET state='prepared' WHERE run_id=:id"), {"id": run})


def test_historical_task_snapshot_remains_mock_after_configuration_change(case):
    owner, project = case
    run = enqueue(owner, project, "offline_build", uuid4().hex, {})["id"]
    with tenant_transaction(owner) as conn:
        before = conn.scalar(sa.select(T["runs"].c.config_json).where(T["runs"].c.id == run))
        conn.execute(
            T["projects"]
            .update()
            .where(T["projects"].c.id == project)
            .values(preferences={"model_configuration": {"preset": "local"}})
        )
        after = conn.scalar(sa.select(T["runs"].c.config_json).where(T["runs"].c.id == run))
    assert before == after and after["models"]["preset"] == "mock"


def test_local_models_diagnostic_no_install_or_inference():
    import subprocess
    import sys
    from scholarship.config import PROJECT_ROOT

    r = subprocess.run(
        [sys.executable, "local_models.py"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True, timeout=10
    )
    assert json.loads(r.stdout)["inference_requests"] == 0


def test_gemma_chat_and_evidence_contract():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path == "/api/v1/chat/completions"
        assert body["model"] == "google/gemma-4-31b-it:free"
        assert body["reasoning"] == {"enabled": False}
        assert body["provider"] == {"allow_fallbacks": False, "require_parameters": True}
        text = "SCHOLARSHIP_OK"
        if body.get("response_format"):
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
        return httpx.Response(200, json={**chat_response(text), "model": "google/gemma-4-31b-it"})

    client = provider("chat", handler, preset="free_gemma", max_output=256)
    assert client.generate([{"role": "user", "content": "hello"}]).value == "SCHOLARSHIP_OK"
    result = client.answer("What accuracy?", [Evidence("alpha", "Alpha accuracy is 91 percent.", 1)])
    assert result.value["claims"][0]["citations"][0]["quote"] == "Alpha accuracy is 91 percent."
    assert len(seen) == 2 and "response_format" not in seen[0] and "response_format" in seen[1]
    assert load_selection("free_gemma").roles["translation"].model == "google/gemma-4-31b-it:free"
    assert load_selection("free_api").roles["chat"].model == "gpt-4o-mini"
    with pytest.raises(ValueError):
        replace(load_selection("free_gemma").roles["chat"], model="google/gemma-4-31b-it").validate("chat")
