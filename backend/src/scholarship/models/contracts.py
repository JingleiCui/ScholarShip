"""Provider-neutral results and sanitized failure contracts."""

from dataclasses import dataclass
from scholarship.adapters.providers import integer, ContractError

ROLE_OPERATIONS = {
    "chat": {"answer", "generate", "describe_figure"},
    "translation": {"generate"},
    "embedding": {"embed"},
    "rerank": {"rank"},
}


class ProviderFailure(RuntimeError):
    def __init__(self, code, status=None, *, unknown=False, retry_after=None, transport_kind=None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.unknown = unknown
        self.retry_after = retry_after
        self.transport_kind = transport_kind


@dataclass(frozen=True)
class ModelResult:
    value: object
    provider: str
    requested_model: str
    reported_model: str | None
    usage: dict
    request_id: str | None
    elapsed_ms: int
    dimensions: int | None = None
    simulated: bool = True


def optional_count(value):
    return None if value is None else integer(value)


def usage(data, ollama=False):
    if ollama:
        return {
            "input_tokens": optional_count(data.get("prompt_eval_count")),
            "output_tokens": optional_count(data.get("eval_count")),
        }
    u = data.get("usage")
    if u is None:
        return {"input_tokens": None, "output_tokens": None}
    if not isinstance(u, dict):
        raise ContractError("invalid_usage")
    result = {
        "input_tokens": optional_count(u.get("prompt_tokens")),
        "output_tokens": optional_count(u.get("completion_tokens")),
    }
    if "total_tokens" in u:
        result["total_tokens"] = optional_count(u["total_tokens"])
    return result
