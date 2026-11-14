"""Trusted, cached provider/model catalog. No secrets or network requests."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import re
import runpy
from urllib.parse import urlsplit

from scholarship.config import PROJECT_ROOT, settings

CONFIG_PATH = PROJECT_ROOT / "model_config.py"


@lru_cache(maxsize=8)
def _read(path, stamp):
    return runpy.run_path(path)


def configuration(path=None):
    file = Path(path or CONFIG_PATH)
    stat = file.stat()
    # A copy prevents callers mutating the shared catalog or a stored snapshot.
    return deepcopy({k: v for k, v in _read(str(file), (stat.st_mtime_ns, stat.st_size)).items() if k.isupper()})


def provider_definition(provider, config=None):
    config = config if config is not None else configuration()
    entry = config.get("PROVIDER_DEFINITIONS", {}).get(provider)
    if not isinstance(entry, dict) or not isinstance(entry.get("roles"), dict):
        raise ValueError("unknown_provider_plugin")
    from scholarship.models.protocols import protocol_roles

    if any(role not in protocol_roles(protocol) for role, protocol in entry["roles"].items()):
        raise ValueError("protocol_role_mismatch")
    key = entry.get("key_env")
    if key is not None and (not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*_API_KEY", key)):
        raise ValueError("invalid_credential_environment_name")
    options = entry.get("chat_options", {})
    if not isinstance(options, dict) or set(options) - {"reasoning", "provider"}:
        raise ValueError("unsupported_chat_options")
    return entry


def validate_binding(spec, role, config=None):
    c = config if config is not None else configuration()
    definition = provider_definition(spec.provider, c)
    if role not in definition["roles"]:
        raise ValueError("provider_role_mismatch")
    matches = [
        name
        for name, entry in c["MODELS"].items()
        if entry["provider"] == spec.provider and entry["model"] == spec.model
    ]
    if not any(role in c["MODEL_CAPABILITIES"].get(name, ()) for name in matches):
        raise ValueError("model_capability_mismatch")
    if definition.get("free_only") and not spec.model.endswith(":free"):
        raise ValueError("only_explicit_free_models_allowed")
    if spec.base_url not in definition.get("base_urls", ()):
        raise ValueError("untrusted_model_endpoint")
    if not spec.base_url and spec.provider == "mock":
        return definition
    try:
        u = urlsplit(spec.base_url)
        if u.username or u.password or u.query or u.fragment:
            raise ValueError()
        if definition.get("key_env"):
            if u.scheme != "https" or u.port not in (None, 443):
                raise ValueError()
        elif u.scheme != "http" or u.hostname != "127.0.0.1" or not u.port:
            raise ValueError()
    except ValueError:
        raise ValueError("untrusted_model_endpoint") from None
    return definition


def credential(provider):
    """Resolve a key at dispatch time; never keep its value in the catalog cache."""
    key = provider_definition(provider).get("key_env")
    if key is None:
        return ""
    cfg = settings()
    field = getattr(cfg, key.lower(), None)
    if field is not None:
        return field.get_secret_value()
    import os
    from dotenv import dotenv_values

    return os.environ.get(key) or dotenv_values(PROJECT_ROOT / ".env").get(key) or ""


def identity_allowed(spec, reported):
    if reported is None:
        return True
    definition = provider_definition(spec.provider)
    aliases = {spec.model}
    if definition.get("free_only") and spec.model.endswith(":free"):
        aliases.add(spec.model.removesuffix(":free"))
    # Declared aliases never authorize a different requested model or endpoint.
    aliases.update(definition.get("model_aliases", {}).get(spec.model, ()))
    return reported in aliases


def binding_snapshot(spec, role, config=None):
    from scholarship.models.protocols import resolve_protocol

    definition = provider_definition(spec.provider, config)
    name = definition["roles"][role]
    try:
        revision = resolve_protocol(name).revision
    except ValueError:
        revision = name + "-lifecycle-v1"
    # A chat-only parameter change must not invalidate an embedding index.
    relevant = {k: deepcopy(definition[k]) for k in ("base_urls", "key_env", "free_only") if k in definition}
    relevant["roles"] = {role: name}
    if role in {"chat", "translation"} and "chat_options" in definition:
        relevant["chat_options"] = deepcopy(definition["chat_options"])
    if role == "embedding" and "embedding_input_types" in definition:
        relevant["embedding_input_types"] = deepcopy(definition["embedding_input_types"])
    aliases = definition.get("model_aliases", {}).get(spec.model)
    if aliases:
        relevant["model_aliases"] = {spec.model: deepcopy(aliases)}
    return {"protocol": name, "revision": revision, "definition": relevant}
