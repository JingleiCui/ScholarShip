"""Deterministic local test doubles, never scientific inference."""

import hashlib
import json
import math
import httpx

from scholarship.adapters.providers import Embeddings, Reranker, Answerer
from scholarship.domains.retrieval_profile import lexical


def vector(text):
    values = [0.0] * 1024
    for word in lexical(text).split():
        n = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big")
        values[n % 1024] += 1.0
    if not any(values):
        values[0] = 1.0
    size = math.sqrt(sum(x * x for x in values))
    return [x / size for x in values]


def overlap(query, text):
    return len(set(lexical(query).split()) & set(lexical(text).split()))


def response(request):
    body = json.loads(request.content)
    usage = {"total_tokens": 0}
    if request.url.path.endswith("/embeddings"):
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-v4",
                "usage": usage,
                "data": [{"index": i, "embedding": vector(t)} for i, t in enumerate(body["input"])],
            },
        )
    if request.url.path.endswith("/reranks"):
        ranks = sorted(enumerate(body["documents"]), key=lambda x: (-overlap(body["query"], x[1]), x[0]))[
            : body["top_n"]
        ]
        return httpx.Response(
            200,
            json={
                "model": "qwen3-rerank",
                "usage": usage,
                "results": [{"index": i, "relevance_score": overlap(body["query"], t)} for i, t in ranks],
            },
        )
    if request.url.path.endswith("/chat/completions"):
        context = json.loads(body["messages"][-1]["content"])
        evidence = [e for e in context["evidence"] if overlap(context["question"], e["text"]) > 0]
        claims = [
            {
                "text": e["text"],
                "citations": [{"evidence_id": e["id"], "quote": e["text"], "start": 0, "end": len(e["text"])}],
            }
            for e in evidence
        ]
        answer = {
            "answer": "\n".join(e["text"] for e in evidence) if evidence else "证据不足，无法据此回答。",
            "claims": claims,
            "insufficient_evidence": not bool(evidence),
        }
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer, ensure_ascii=False)}}],
            },
        )
    raise AssertionError("unrecognized_synthetic_request")


def adapters():
    return (
        Embeddings(httpx.MockTransport(response)),
        Reranker(httpx.MockTransport(response)),
        Answerer(httpx.MockTransport(response)),
    )
