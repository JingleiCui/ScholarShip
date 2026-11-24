"""Validated secret-free configuration snapshots; no provider calls."""

from dataclasses import asdict, dataclass
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from scholarship.config import settings
from scholarship.models.catalog import (
    configuration,
    validate_binding,
    provider_definition,
    credential,
    binding_snapshot,
    deployment,
)

ROLES = ("chat", "translation", "embedding", "rerank", "parser")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    base_url: str = ""
    dimensions: int | None = None
    json_mode: bool = False
    context_tokens: int = 8192
    tier: str | None = None

    def validate(self, role, *, config=None):
        validate_binding(self, role, config)
        if not isinstance(self.model, str) or not 1 <= len(self.model) <= 160:
            raise ValueError("invalid_model")
        if self.dimensions is not None and (type(self.dimensions) is not int or not 1 <= self.dimensions <= 16384):
            raise ValueError("invalid_dimensions")
        if (
            type(self.json_mode) is not bool
            or type(self.context_tokens) is not int
            or not 512 <= self.context_tokens <= 32768
        ):
            raise ValueError("invalid_model_limits")
        if self.provider == "mineru_local" and self.tier != "basic":
            raise ValueError("unreviewed_local_parser_tier")
        return self


@dataclass(frozen=True)
class ModelSelection:
    preset: str
    roles: dict
    timeout_seconds: int
    concurrency: int
    max_output_tokens: int
    max_requests_per_run: int
    allow_local: bool
    allow_cloud: bool
    bindings: dict | None = None

    def snapshot(self):
        body = {
            "revision": "models-v2" if self.bindings is not None else "models-v1",
            "preset": self.preset,
            "roles": {k: asdict(v) for k, v in self.roles.items()},
            "timeout_seconds": self.timeout_seconds,
            "concurrency": self.concurrency,
            "max_output_tokens": self.max_output_tokens,
            "max_requests_per_run": self.max_requests_per_run,
        }
        if self.bindings is not None:
            body["bindings"] = deepcopy(self.bindings)
        return {**body, "fingerprint": fingerprint(body)}


def load_selection(preset=None, overrides=None, path: Path | None = None):
    c = configuration(path)
    name = preset or c["ACTIVE_PRESET"]
    if name not in c["PRESETS"]:
        raise ValueError("unknown_preset")
    if c.get("INFERENCE_MODE", "auto") not in {"auto", "local", "cloud"}:
        raise ValueError("invalid_inference_mode")
    choices = {**c["PRESETS"][name], **c["ROLE_OVERRIDES"], **(overrides or {})}
    if set(choices) != set(ROLES):
        raise ValueError("invalid_role_set")
    roles = {k: ModelSpec(**c["MODELS"][v]).validate(k, config=c) for k, v in choices.items()}
    bounds = {
        "TIMEOUT_SECONDS": (1, 600),
        "CONCURRENCY": (1, 4),
        "MAX_OUTPUT_TOKENS": (1, 8192),
        "MAX_REQUESTS_PER_RUN": (1, 200),
    }
    for key, (low, high) in bounds.items():
        if type(c[key]) is not int or not low <= c[key] <= high:
            raise ValueError("invalid_execution_limits")
    if any(type(c[k]) is not bool for k in ["ALLOW_LOCAL_INFERENCE", "ALLOW_REAL_REQUESTS"]):
        raise ValueError("invalid_permission_flags")
    return ModelSelection(
        name,
        roles,
        c["TIMEOUT_SECONDS"],
        c["CONCURRENCY"],
        c["MAX_OUTPUT_TOKENS"],
        c["MAX_REQUESTS_PER_RUN"],
        c["ALLOW_LOCAL_INFERENCE"],
        c["ALLOW_REAL_REQUESTS"],
        {role: binding_snapshot(spec, role, c) for role, spec in roles.items()},
    )


def restore_snapshot(body):
    safe = {k: v for k, v in body.items() if k != "fingerprint"}
    if (
        body.get("fingerprint") != fingerprint(safe)
        or safe.get("revision") not in {"models-v1", "models-v2"}
        or set(safe["roles"]) != set(ROLES)
    ):
        raise ValueError("invalid_model_snapshot")
    roles = {k: ModelSpec(**v).validate(k) for k, v in safe["roles"].items()}
    bindings = safe.get("bindings")
    if safe["revision"] == "models-v2" and (not isinstance(bindings, dict) or set(bindings) != set(ROLES)):
        raise ValueError("invalid_model_bindings")
    # Historic snapshots cannot grant execution permission; current gates always apply.
    current = load_selection()
    return ModelSelection(
        safe["preset"],
        roles,
        safe["timeout_seconds"],
        safe["concurrency"],
        safe["max_output_tokens"],
        safe["max_requests_per_run"],
        current.allow_local,
        current.allow_cloud,
        bindings,
    )


def configuration_status(preset=None, overrides=None):
    selected = load_selection(preset, overrides)
    catalog = configuration()
    cfg = settings()
    roles = {}
    for role, spec in selected.roles.items():
        definition = provider_definition(spec.provider, catalog)
        key = definition.get("key_env")
        configured = bool(credential(spec.provider)) if key else True
        roles[role] = {
            **asdict(spec),
            "protocol": definition["roles"][role],
            "deployment": deployment(spec.provider, catalog),
            "credential_required": bool(key),
            "credential_present": configured,
            "verification_state": "synthetic_only" if spec.provider == "mock" else "not_verified",
            "account_verified": False,
            "installed_verified": spec.provider == "mock",
            "inference_enabled": False,
            "dimensions_state": "pending_first_valid_receipt"
            if role == "embedding" and spec.dimensions is None
            else "configured_not_verified",
        }
    return {
        "selected_preset": selected.preset,
        # Project-level role overrides win over the preset; expose them so the UI
        # can tell a clean preset from a custom per-role selection.
        "active_overrides": dict(overrides or {}),
        "credentials_present": {
            "chatanywhere": bool(cfg.chatanywhere_api_key.get_secret_value()),
            "openrouter": bool(cfg.openrouter_api_key.get_secret_value()),
            "mineru": bool(cfg.mineru_api_key.get_secret_value()),
        },
        "available_presets": list(catalog["PRESETS"]),
        "inference_mode": catalog.get("INFERENCE_MODE", "auto"),
        "available_models": {
            name: {
                **entry,
                "roles": catalog["MODEL_CAPABILITIES"].get(name, []),
                "deployment": deployment(entry["provider"], catalog),
            }
            for name, entry in catalog["MODELS"].items()
        },
        "reload_policy": {
            "model_config": "new_tasks_without_restart",
            "running_and_queued_tasks": "pinned_snapshot_drift_rejected",
            "permission_flags": "checked_before_each_real_request",
            "environment_and_builtin_credentials": "restart_api_and_workers",
            "container_file": "requires_visible_file_update_or_redeploy",
            "production_mode": "cloud_is_legacy_name_for_real_local_cloud_or_mixed_dispatch",
            "parser": "cloud_existing_artifacts_or_separately_authorized_upload",
        },
        "roles": roles,
        "snapshot_fingerprint": selected.snapshot()["fingerprint"],
        "cloud_enabled": False,
        "local_enabled": selected.allow_local,
        "switch_effects": {
            "chat": "new_tasks_only",
            "translation": "new_tasks_only",
            "rerank": "new_tasks_only",
            "embedding": "new_profile_and_verified_rebuild",
            "parser": "new_parse_revision",
        },
        "configured_flags": {
            "allow_cloud": selected.allow_cloud,
            "allow_local": selected.allow_local,
            "external_services_enabled": cfg.external_services_enabled,
        },
        "live_validation_authorized": False,
    }
