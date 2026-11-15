"""Explicit protocol test doubles on real source text; never research inference."""

import hashlib
import json
import math
import httpx
from scholarship.adapters.synthetic import overlap
from scholarship.domains.retrieval_profile import lexical


def transport_factory(selection):
    def transport(role):
        def reply(request):
            b = json.loads(request.content)
            model = selection.roles[role].model
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            if role == "embedding":
                dim = selection.roles[role].dimensions or 4096
                vectors = []
                for text in b["input"]:
                    v = [0.0] * dim
                    for token in lexical(text).split():
                        v[int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % dim] += 1
                    if not any(v):
                        v[0] = 1
                    norm = math.sqrt(sum(x * x for x in v))
                    vectors.append([x / norm for x in v])
                return httpx.Response(
                    200,
                    json={
                        "model": model,
                        "usage": usage,
                        "embeddings": vectors,
                        "data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)],
                    },
                )
            if role == "rerank":
                order = sorted(range(len(b["documents"])), key=lambda i: (-overlap(b["query"], b["documents"][i]), i))[
                    : b["top_n"]
                ]
                return httpx.Response(
                    200,
                    json={
                        "model": model,
                        "usage": usage,
                        "results": [
                            {"index": i, "relevance_score": overlap(b["query"], b["documents"][i])} for i in order
                        ],
                    },
                )
            c = json.loads(b["messages"][-1]["content"])
            if c.get("task") == "source_summary":
                quote = c["text"][:100]
                text = json.dumps(
                    {"text": "[模拟摘要] " + quote, "quote_ids": ["q0"]}
                    if c.get("quotes")
                    else {"text": "[模拟摘要] " + quote, "quotes": [quote]},
                    ensure_ascii=False,
                )
                return httpx.Response(
                    200,
                    json={
                        "model": model,
                        "usage": usage,
                        "done": True,
                        "message": {"content": text},
                        "choices": [{"finish_reason": "stop", "message": {"content": text}}],
                    },
                )
            if c.get("task") in {"translate", "translate_parts"}:
                text = json.dumps(
                    {"segments": [{"id": b["id"], "text": "[模拟译文] " + b["text"]} for b in c["segments"]]},
                    ensure_ascii=False,
                )
                return httpx.Response(
                    200,
                    json={
                        "model": model,
                        "usage": usage,
                        "done": True,
                        "message": {"content": text},
                        "choices": [{"finish_reason": "stop", "message": {"content": text}}],
                    },
                )
            if c.get("task") in {"draft", "summary", "direction"}:
                text = json.dumps(
                    {
                        "sections": [
                            {
                                "title": title,
                                "text": "[模拟草稿，非科研结论] " + c.get("idea", ""),
                                "source_ids": [d["id"] for d in c["sources"]],
                            }
                            for title in (
                                ["Abstract", "Introduction", "Related Work", "Planned experiments"]
                                if c["task"] == "draft"
                                else ["Source-grounded notes"]
                            )
                        ],
                        "limitations": ["synthetic contract only"],
                        "hypotheses": [],
                    },
                    ensure_ascii=False,
                )
                return httpx.Response(
                    200,
                    json={
                        "model": model,
                        "usage": usage,
                        "done": True,
                        "message": {"content": text},
                        "choices": [{"finish_reason": "stop", "message": {"content": text}}],
                    },
                )
            evidence = [e for e in c["evidence"] if overlap(c["question"], e["text"]) > 0][:2]
            answer = {
                "answer": "\n".join(e["text"] for e in evidence) if evidence else "证据不足。",
                "insufficient_evidence": not bool(evidence),
                "claims": [
                    {
                        "text": e["text"],
                        "citations": [{"evidence_id": e["id"], "quote": e["text"], "start": 0, "end": len(e["text"])}],
                    }
                    for e in evidence
                ],
            }
            if c.get("citation_mode") == "quote_id":
                for claim in answer["claims"]:
                    for cite in claim["citations"]:
                        eid = cite["evidence_id"]
                        cite.clear()
                        cite.update(evidence_id=eid, quote_id="q0")
            text = json.dumps(answer, ensure_ascii=False)
            return httpx.Response(
                200,
                json={
                    "model": model,
                    "usage": usage,
                    "done": True,
                    "message": {"content": text},
                    "choices": [{"finish_reason": "stop", "message": {"content": text}}],
                },
            )

        return httpx.MockTransport(reply)

    return transport
