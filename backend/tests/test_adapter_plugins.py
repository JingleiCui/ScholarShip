"""A new compatible provider/model is connected by catalog entries only."""

from copy import deepcopy
from dataclasses import replace
from pprint import pformat
from uuid import uuid4

import httpx
import pytest

from scholarship.models import catalog
from scholarship.models.configuration import load_selection, restore_snapshot, fingerprint
from scholarship.models.adapters import ProviderClient
from scholarship.models.gateway import SimulatedGateway
from scholarship.domains.offline_workflow import enqueue_model_qa, qa_graph
from test_model_pipeline import indexed
from test_offline_business import case as case


@pytest.fixture
def custom_catalog(tmp_path, monkeypatch):
    config = catalog.configuration()
    config["PROVIDER_DEFINITIONS"]["compatible"] = {
        "roles": {
            "chat": "openai_chat",
            "translation": "openai_chat",
            "embedding": "openai_embedding",
            "rerank": "rerank",
        },
        "base_urls": ["https://models.example.test/v1"],
        "key_env": "COMPATIBLE_API_KEY",
    }
    for name, roles in {"chat": ["chat", "translation"], "embed": ["embedding"], "rank": ["rerank"]}.items():
        config["MODELS"]["custom_" + name] = {
            "provider": "compatible",
            "model": "custom-" + name,
            "base_url": "https://models.example.test/v1",
            "json_mode": name == "chat",
        }
        config["MODEL_CAPABILITIES"]["custom_" + name] = roles
    config["PRESETS"]["custom"] = {
        "chat": "custom_chat",
        "translation": "custom_chat",
        "embedding": "custom_embed",
        "rerank": "custom_rank",
        "parser": "mock_parser",
    }
    file = tmp_path / "model_config.py"
    file.write_text("\n".join(f"{key} = {pformat(value)}" for key, value in config.items()))
    monkeypatch.setattr(catalog, "CONFIG_PATH", file)
    return config, file


def test_config_only_provider_runs_complete_pipeline(case, custom_catalog):
    selection = load_selection("custom")
    versions, provider = indexed(case, selection)
    owner, project = case
    run = enqueue_model_qa(owner, project, uuid4().hex, versions, "Alpha accuracy?", selection)["id"]
    with SimulatedGateway(owner, run, selection, provider.transport) as gateway:
        result = qa_graph(owner, project, run, model_gateway=gateway)
        assert result["answer"] == "91 percent" and result["citations"]
    assert selection.roles["embedding"].dimensions is None
    assert {"embedding", "rerank", "chat"}.issubset(provider.sent)


def test_capabilities_prevent_chat_model_from_becoming_embedder(custom_catalog):
    with pytest.raises(ValueError, match="model_capability_mismatch"):
        load_selection("custom", {"embedding": "custom_chat"})
    with pytest.raises(ValueError):
        replace(load_selection("custom").roles["chat"], base_url="https://other.example/v1").validate("chat")


def test_cache_invalidates_and_binding_snapshot_detects_parameter_drift(custom_catalog):
    config, file = custom_catalog
    before = load_selection("custom")
    config["PROVIDER_DEFINITIONS"]["compatible"]["chat_options"] = {"reasoning": {"enabled": False}}
    file.write_text("\n".join(f"{key} = {pformat(value)}" for key, value in config.items()))
    after = load_selection("custom")
    assert before.snapshot()["fingerprint"] != after.snapshot()["fingerprint"]
    assert before.bindings["embedding"] == after.bindings["embedding"]
    assert restore_snapshot(before.snapshot()).snapshot() == before.snapshot()
    client = ProviderClient(
        after.roles["chat"], "chat", transport=httpx.MockTransport(lambda r: pytest.fail("must not send"))
    )
    assert client.binding != before.bindings["chat"]
    client.close()


def test_v1_snapshot_restores_without_rewriting_history():
    selection = load_selection("free_api")
    changed = selection.snapshot()
    changed["bindings"]["chat"]["revision"] = "tampered"
    assert selection.snapshot()["bindings"]["chat"]["revision"] != "tampered"
    body = load_selection("free_api").snapshot()
    body.pop("bindings")
    body["revision"] = "models-v1"
    body.pop("fingerprint")
    body["fingerprint"] = fingerprint(body)
    assert restore_snapshot(deepcopy(body)).snapshot() == body


def test_configuration_is_cached_but_returned_data_is_not_shared(custom_catalog, monkeypatch):
    _, file = custom_catalog
    catalog._read.cache_clear()
    original = catalog.runpy.run_path
    reads = []
    monkeypatch.setattr(catalog.runpy, "run_path", lambda path: reads.append(path) or original(path))
    first = catalog.configuration(file)
    first["MODELS"].clear()
    second = catalog.configuration(file)
    assert second["MODELS"] and len(reads) == 1
