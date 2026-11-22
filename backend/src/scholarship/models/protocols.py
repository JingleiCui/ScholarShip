"""Protocol plugins know wire formats; business code knows roles and results."""

from dataclasses import dataclass
from copy import deepcopy

from scholarship.adapters.providers import ContractError, integer


@dataclass(frozen=True)
class WireRequest:
    path: str
    body: dict


class OpenAIChat:
    revision = "openai-chat-v2"
    roles = {"chat", "translation"}
    usage_style = "openai"

    def prepare(self, spec, definition, *, messages, structured, max_output, schema=None):
        body = {"model": spec.model, "messages": messages, "stream": False, "max_tokens": max_output, "temperature": 0}
        body.update(deepcopy(definition.get("chat_options", {})))
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "figure_description", "strict": True, "schema": schema},
            }
        elif structured and spec.json_mode:
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
    revision = "ollama-chat-v5"
    roles = {"chat", "translation"}
    usage_style = "ollama"

    def prepare(self, spec, definition, *, messages, structured, max_output, schema=None):
        converted = []
        for message in messages:
            row = deepcopy(message)
            if isinstance(row.get("content"), list):
                parts = row["content"]
                row["content"] = "\n".join(p["text"] for p in parts if p.get("type") == "text")
                row["images"] = [p["image_url"]["url"].split(",", 1)[1] for p in parts if p.get("type") == "image_url"]
            converted.append(row)
        body = {
            "model": spec.model,
            "messages": converted,
            "stream": False,
            "think": False,
            "options": {"num_ctx": spec.context_tokens, "num_predict": max_output, "temperature": 0},
        }
        if schema is not None:
            body["format"] = deepcopy(schema)
            # Ollama's JSON grammar does not enforce numeric min/max. Constrain
            # the decoder to a declared 0.01 grid rather than clamping replies.
            coordinate = (
                body["format"].get("$defs", {}).get("Node", {}).get("properties", {}).get("bbox", {}).get("items")
            )
            if coordinate is not None and definition.get("figure_policy") == "nodes_then_edges":
                body["format"]["properties"]["edges"] = {"type": "array", "const": []}
                for row in body["messages"]:
                    if row.get("role") == "system":
                        row["content"] += (
                            " This is the node-location stage only. Return edges as an empty list; a separate stage traces connections using your fixed node IDs."
                        )
            if coordinate is not None:
                coordinate["enum"] = [round(i / 100, 2) for i in range(101)]
                for row in body["messages"]:
                    if row.get("role") == "system":
                        row["content"] += (
                            " Read boxes in observed top-to-bottom order. The origin is the top-left of the image; y increases downward. "
                            "Use bbox coordinates on the supplied 0.01 grid between 0 and 1. Each box MUST have x1<x2 and y1<y2. "
                            "Never extrapolate positions by subtracting fixed layer offsets. Omit any node whose positive-area box cannot be located, "
                            "and omit edges referencing an omitted node; describe that uncertainty. Do not infer arrows from known architecture conventions."
                        )
        elif structured and spec.json_mode:
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
    revision = "ollama-embedding-v2"
    roles = {"embedding"}
    usage_style = "ollama"

    def prepare(self, spec, definition, *, texts, input_kind):
        body = {"model": spec.model, "input": texts, "truncate": False, "options": {"num_ctx": spec.context_tokens}}
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


class SyntheticProtocol:
    """Mock-only wire contract. Keeps the existing pinned lifecycle revision."""

    revision = "synthetic-lifecycle-v1"
    roles = {"chat", "translation", "embedding", "rerank", "parser"}
    usage_style = "openai"

    def prepare(self, spec, definition, **arguments):
        if "texts" in arguments:
            body = {"model": spec.model, "input": arguments["texts"]}
            path = "/embeddings"
        elif "documents" in arguments:
            body = {"model": spec.model, **arguments}
            path = "/rerank"
        elif "messages" in arguments:
            body = {"model": spec.model, "messages": arguments["messages"]}
            path = "/chat/completions"
        else:
            raise ContractError("synthetic_parser_requires_layout_lifecycle")
        return WireRequest("https://scholarship.invalid" + path, body)

    def read(self, data, **unused):
        if "choices" in data:
            return OpenAIChat().read(data)
        if "data" in data:
            return OpenAIEmbedding().read(data, count=unused["count"])
        return data.get("results")


# Register a new protocol here; existing protocols require only catalog entries.
_ADAPTERS = {}
_LIFECYCLES = {
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
    "synthetic": SyntheticProtocol(),
    "openai_chat": OpenAIChat(),
    "ollama_chat": OllamaChat(),
    "openai_embedding": OpenAIEmbedding(),
    "ollama_embedding": OllamaEmbedding(),
    "rerank": Rerank(),
}.items():
    register_protocol(_name, _adapter)
