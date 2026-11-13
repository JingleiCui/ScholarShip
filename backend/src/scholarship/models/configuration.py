"""Validated secret-free configuration snapshots; no provider calls."""

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import runpy
from urllib.parse import urlsplit

from scholarship.config import PROJECT_ROOT, settings

ROLES = ("chat", "translation", "embedding", "rerank", "parser")
PROVIDERS = {
    "mock": set(ROLES),
    "chatanywhere": {"chat", "translation"},
    "openrouter": {"embedding", "rerank"},
    "ollama": {"chat", "translation", "embedding"},
    "local_reranker": {"rerank"},
    "mineru_local": {"parser"},
    "mineru_cloud": {"parser"},
}
CREDENTIALS = {
    "chatanywhere": "chatanywhere_api_key",
    "openrouter": "openrouter_api_key",
    "mineru_cloud": "mineru_api_key",
}
FREE_MODELS = {"embedding": "nvidia/nemotron-3.5-embed-8b:free", "rerank": "nvidia/nemotron-3.5-rerank-8b:free"}
CLOUD_HOSTS = {
    "chatanywhere": {"api.chatanywhere.tech", "api.chatanywhere.org"},
    "openrouter": {"openrouter.ai"},
    "mineru_cloud": {"mineru.net"},
}


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

    def validate(self, role):
        if self.provider not in PROVIDERS or role not in PROVIDERS[self.provider]:
            raise ValueError("provider_role_mismatch")
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
        if self.provider == "openrouter" and self.model != FREE_MODELS[role]:
            raise ValueError("only_reviewed_free_models_allowed")
        if self.provider == "mock":
            if self.base_url:
                raise ValueError("mock_has_no_endpoint")
            return self
        try:
            u = urlsplit(self.base_url)
            if u.username or u.password or u.query or u.fragment:
                raise ValueError()
            if self.provider in CLOUD_HOSTS:
                if u.scheme != "https" or u.hostname not in CLOUD_HOSTS[self.provider] or u.port not in (None, 443):
                    raise ValueError()
                expected = (
                    "/api/v1" if self.provider == "openrouter" else "/v1" if self.provider == "chatanywhere" else ""
                )
                if u.path.rstrip("/") != expected:
                    raise ValueError()
            elif u.scheme != "http" or u.hostname != "127.0.0.1" or not u.port or u.path.rstrip("/"):
                raise ValueError()
        except ValueError:
            raise ValueError("untrusted_model_endpoint") from None
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

    def snapshot(self):
        body = {
            "revision": "models-v1",
            "preset": self.preset,
            "roles": {k: asdict(v) for k, v in self.roles.items()},
            "timeout_seconds": self.timeout_seconds,
            "concurrency": self.concurrency,
            "max_output_tokens": self.max_output_tokens,
            "max_requests_per_run": self.max_requests_per_run,
        }
        return {**body, "fingerprint": fingerprint(body)}


def load_selection(preset=None, overrides=None, path: Path | None = None):
    c = runpy.run_path(str(path or PROJECT_ROOT / "model_config.py"))
    name = preset or c["ACTIVE_PRESET"]
    if name not in c["PRESETS"]:
        raise ValueError("unknown_preset")
    choices = {**c["PRESETS"][name], **c["ROLE_OVERRIDES"], **(overrides or {})}
    if set(choices) != set(ROLES):
        raise ValueError("invalid_role_set")
    roles = {k: ModelSpec(**c["MODELS"][v]).validate(k) for k, v in choices.items()}
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
    )


def restore_snapshot(body):
    safe = {k: v for k, v in body.items() if k != "fingerprint"}
    if (
        body.get("fingerprint") != fingerprint(safe)
        or safe.get("revision") != "models-v1"
        or set(safe["roles"]) != set(ROLES)
    ):
        raise ValueError("invalid_model_snapshot")
    roles = {k: ModelSpec(**v).validate(k) for k, v in safe["roles"].items()}
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
    )


def configuration_status(preset=None, overrides=None):
    selected = load_selection(preset, overrides)
    cfg = settings()
    roles = {}
    for role, spec in selected.roles.items():
        key = CREDENTIALS.get(spec.provider)
        configured = bool(getattr(cfg, key).get_secret_value()) if key else True
        roles[role] = {
            **asdict(spec),
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
        "credentials_present": {
            "chatanywhere": bool(cfg.chatanywhere_api_key.get_secret_value()),
            "openrouter": bool(cfg.openrouter_api_key.get_secret_value()),
            "mineru": bool(cfg.mineru_api_key.get_secret_value()),
        },
        "available_presets": ["mock", "local", "free_api"],
        "roles": roles,
        "snapshot_fingerprint": selected.snapshot()["fingerprint"],
        "cloud_enabled": False,
        "local_enabled": False,
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
