"""Protocol plugins know wire formats; business code knows roles and results."""

from dataclasses import dataclass
from copy import deepcopy

from scholarship.adapters.providers import ContractError, integer


@dataclass(frozen=True)
class WireRequest:
    path: str
    body: dict


class OpenAIChat:
    revision = "openai-chat-v1"
    roles = {"chat", "translation"}
    usage_style = "openai"

    def prepare(self, spec, definition, *, messages, structured, max_output):
        body = {"model": spec.model, "messages": messages, "stream": False, "max_tokens": max_output, "temperature": 0}
        body.update(deepcopy(definition.get("chat_options", {})))
        if structured and spec.json_mode:
            body["response_format"] = {"type": "json_object"}
        return WireRequest("/chat/completions", body)

    def read(self, data, **unused):
        try:
            choice = data["choices"][0]
            if choice["finish_reason"] != "stop" or choice["message"].get("tool_calls"):
                raise ContractError("incomplete_or_tool_response")
            return choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ContractError("invalid_chat_response") from None


class OllamaChat:
    revision = "ollama-chat-v1"
    roles = {"chat", "translation"}
    usage_style = "ollama"

    def prepare(self, spec, definition, *, messages, structured, max_output):
        body = {
            "model": spec.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"num_ctx": spec.context_tokens, "num_predict": max_output, "temperature": 0},
        }
        if structured and spec.json_mode:
            body["format"] = "json"
        return WireRequest("/api/chat", body)

    def read(self, data, **unused):
        if (
            data.get("done") is not True
            or data.get("done_reason") not in (None, "stop")
            or data.get("message", {}).get("tool_calls")
        ):
            raise ContractError("incomplete_or_tool_response")
        return data.get("message", {}).get("content")


class OpenAIEmbedding:
    revision = "openai-embedding-v1"
    roles = {"embedding"}
    usage_style = "openai"

    def prepare(self, spec, definition, *, texts, input_kind):
        body = {"model": spec.model, "input": texts}
        input_types = definition.get("embedding_input_types", {})
        if input_types:
            if set(input_types) != {"document", "query"}:
                raise ContractError("invalid_embedding_input_types")
            body["input_type"] = input_types[input_kind]
        return WireRequest("/embeddings", body)

    def read(self, data, *, count):
        try:
            rows = data["data"]
            if (
                not isinstance(rows, list)
                or len(rows) != count
                or {integer(row["index"]) for row in rows} != set(range(count))
            ):
                raise ContractError("embedding_index_mismatch")
            return [row["embedding"] for row in sorted(rows, key=lambda row: row["index"])]
        except (KeyError, TypeError):
            raise ContractError("invalid_embedding_response") from None


class OllamaEmbedding:
    revision = "ollama-embedding-v1"
    roles = {"embedding"}
    usage_style = "ollama"

    def prepare(self, spec, definition, *, texts, input_kind):
        body = {"model": spec.model, "input": texts, "truncate": False}
        if spec.dimensions:
            body["dimensions"] = spec.dimensions
        return WireRequest("/api/embed", body)

    def read(self, data, **unused):
        return data.get("embeddings")


class Rerank:
    revision = "rerank-v1"
    roles = {"rerank"}
    usage_style = "openai"

    def prepare(self, spec, definition, *, query, documents, top_n):
        return WireRequest("/rerank", {"model": spec.model, "query": query, "documents": documents, "top_n": top_n})

    def read(self, data, **unused):
        return data.get("results")


# Register a new protocol here; existing protocols require only catalog entries.
_ADAPTERS = {}
_LIFECYCLES = {
    "synthetic": {"chat", "translation", "embedding", "rerank", "parser"},
    "mineru_local": {"parser"},
    "mineru_cloud": {"parser"},
}


def register_protocol(name, adapter):
    if name in _ADAPTERS or name in _LIFECYCLES:
        raise ValueError("protocol_already_registered")
    if not getattr(adapter, "roles", None) or not getattr(adapter, "revision", None):
        raise ValueError("invalid_protocol_plugin")
    _ADAPTERS[name] = adapter


def protocol_roles(name):
    if name in _LIFECYCLES:
        return _LIFECYCLES[name]
    return resolve_protocol(name).roles


def resolve_protocol(name):
    if name not in _ADAPTERS:
        raise ValueError("protocol_requires_specialized_lifecycle" if name in _LIFECYCLES else "unknown_protocol")
    return _ADAPTERS[name]


for _name, _adapter in {
    "openai_chat": OpenAIChat(),
    "ollama_chat": OllamaChat(),
    "openai_embedding": OpenAIEmbedding(),
    "ollama_embedding": OllamaEmbedding(),
    "rerank": Rerank(),
}.items():
    register_protocol(_name, _adapter)
