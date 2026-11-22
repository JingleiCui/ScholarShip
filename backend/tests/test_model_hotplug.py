"""Switches are tested with fake wire responses, never real model requests."""

from dataclasses import replace
import os
from pprint import pformat
from types import SimpleNamespace

import httpx
import pytest

from scholarship.adapters.providers import ExternalDisabled
from scholarship.models import catalog, routing
from scholarship.models.configuration import load_selection, configuration_status


@pytest.fixture
def hot_config(tmp_path, monkeypatch):
    config = catalog.configuration()
    path = tmp_path / "model_config.py"

    def save():
        path.write_text("\n".join(f"{k} = {pformat(v)}" for k, v in config.items()))

    save()
    monkeypatch.setattr(catalog, "CONFIG_PATH", path)
    monkeypatch.setattr(routing, "settings", lambda: SimpleNamespace(external_services_enabled=True))
    return config, path, save


def test_hot_switch_equal_size_equal_timestamp_and_snapshot(hot_config):
    config, path, save = hot_config
    config["ACTIVE_PRESET"] = "free_api"
    save()
    before = load_selection()
    stamp = path.stat()
    config["ACTIVE_PRESET"] = "free_gemma"
    save()
    # Change to a same-size model name, preserve the exact stat metadata.
    first = load_selection()
    raw = path.read_bytes().replace(b"qwen3.5:9b", b"qwen3.5:8b")
    stamp = path.stat()
    path.write_bytes(raw)
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert load_selection("local").roles["chat"].model == "qwen3.5:8b"
    assert first.roles["chat"].model != before.roles["chat"].model
    assert before.roles["chat"].model == "gpt-4o-mini"


def test_local_cloud_mixed_modes_and_live_revocation(hot_config):
    config, _, save = hot_config
    config.update(ALLOW_LOCAL_INFERENCE=True, ALLOW_REAL_REQUESTS=True)
    save()
    mixed = load_selection("free_api", {"chat": "ollama_chat"})
    routing.require_execution(mixed, ("chat", "embedding"))
    config["INFERENCE_MODE"] = "local"
    save()
    routing.require_execution(mixed, ("chat",))
    with pytest.raises(ExternalDisabled, match="deployment_mode_mismatch"):
        routing.require_execution(mixed, ("embedding",))
    config["INFERENCE_MODE"] = "cloud"
    save()
    with pytest.raises(ExternalDisabled, match="deployment_mode_mismatch"):
        routing.require_execution(mixed, ("chat",))
    config.update(INFERENCE_MODE="auto", ALLOW_LOCAL_INFERENCE=False)
    save()
    with pytest.raises(ExternalDisabled, match="configuration_closed"):
        routing.require_execution(mixed, ("chat",))
    routing.require_execution(mixed, ("embedding",))
    with pytest.raises(ExternalDisabled, match="real_model_required"):
        routing.require_execution(replace(load_selection("mock"), allow_cloud=True), ("chat",))


def test_explicit_local_openai_protocol_and_endpoint_validation(hot_config):
    config, _, save = hot_config
    config["PROVIDER_DEFINITIONS"]["compatible_local"] = {
        "deployment": "local",
        "roles": {"chat": "openai_chat"},
        "base_urls": ["http://127.0.0.1:1234/v1"],
    }
    config["MODELS"]["alternative"] = {
        "provider": "compatible_local",
        "model": "my-model",
        "base_url": "http://127.0.0.1:1234/v1",
    }
    config["MODEL_CAPABILITIES"]["alternative"] = ["chat"]
    save()
    selection = load_selection("local", {"chat": "alternative"})
    assert catalog.deployment(selection.roles["chat"].provider) == "local"
    assert configuration_status("local", {"chat": "alternative"})["roles"]["chat"]["deployment"] == "local"
    config["PROVIDER_DEFINITIONS"]["compatible_local"]["base_urls"] = ["http://remote.test:1234/v1"]
    config["MODELS"]["alternative"]["base_url"] = "http://remote.test:1234/v1"
    save()
    with pytest.raises(ValueError, match="untrusted_model_endpoint"):
        load_selection("local", {"chat": "alternative"})


def test_permission_revoked_between_prepare_and_wire_never_sends(hot_config):
    from scholarship.models.production import AdmissionTransport

    config, _, save = hot_config
    config["ALLOW_LOCAL_INFERENCE"] = True
    save()
    selection = load_selection("local")
    routing.require_execution(selection, ("chat",))
    config["ALLOW_LOCAL_INFERENCE"] = False
    save()
    transport = AdmissionTransport(
        None,
        None,
        None,
        "ollama",
        100,
        httpx.MockTransport(lambda r: pytest.fail("revoked request reached wire")),
        selection=selection,
        role="chat",
    )
    with pytest.raises(ExternalDisabled, match="configuration_closed"):
        transport.handle_request(httpx.Request("POST", selection.roles["chat"].base_url + "/api/chat"))
    assert transport.used is False
