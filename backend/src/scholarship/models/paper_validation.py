"""A bounded real-paper RAG validation, isolated from published business indexes.

Only approved short excerpts are sent to models. No PDF upload or parse claims.
"""

from contextlib import closing
from dataclasses import dataclass
import hashlib
import re
from pathlib import Path
from uuid import UUID, uuid5

from pypdf import PdfReader
from qdrant_client import models as m

from scholarship.adapters.providers import Evidence, ContractError
from scholarship.domains.retrieval_profile import lexical
from scholarship.infrastructure.health import qdrant_client
from scholarship.models.live import serialize


@dataclass(frozen=True)
class PaperValidation:
    source: dict
    evidence: tuple
    question: str

    def requests(self):
        documents = [item.text for item in self.evidence]
        return [
            ("paper_documents", "embedding", "embed", {"texts": documents, "input_kind": "document"}),
            ("paper_query", "embedding", "embed", {"texts": [self.question], "input_kind": "query"}),
            ("paper_rerank", "rerank", "rank", {"query": self.question, "documents": documents, "top_n": 2}),
            ("paper_answer", "chat", "answer", {"question": self.question, "evidence": [self.evidence[0]]}),
        ]


def load_transformer_sample(path):
    path = Path(path)
    raw = path.read_bytes()
    reader = PdfReader(path)
    blocks = []
    locators = []
    definitions = [
        ("transformer-hardware", "We trained our models on one machine", "5.3 Optimizer"),
        ("transformer-optimizer", "We used the Adam optimizer", "We varied the learning"),
    ]
    for key, start_marker, end_marker in definitions:
        matches = []
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            start = text.find(start_marker)
            end = text.find(end_marker, start + len(start_marker)) if start >= 0 else -1
            if start >= 0 and end > start:
                end = start + len(text[start:end].rstrip())
                matches.append((index + 1, start, end, text[start:end]))
        if len(matches) != 1:
            raise ContractError("official_sample_anchor_ambiguous")
        page, start, end, text = matches[0]
        if len(text.encode()) > 1600:
            raise ContractError("official_sample_excerpt_too_large")
        blocks.append(Evidence(key, text, page))
        locators.append(
            {
                "evidence_id": key,
                "pdf_page": page,
                "page_text_start": start,
                "page_text_end": end,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
    return PaperValidation(
        {
            "kind": "public_pdf_excerpt",
            "filename": path.name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "pages": len(reader.pages),
            "parser": "pypdf-text-preview",
            "locators": locators,
            "pdf_uploads": 0,
            "scientific_acceptance_question": "official-single-paper-3",
        },
        tuple(blocks),
        "How many NVIDIA P100 GPUs were used to train the Transformer base model?",
    )


def run_paper_validation(gateway, plan, *, owner_id, project_id, grant_id, progress=None):
    """Four model calls once each; PG receipts make successful steps replayable."""
    report = {
        "source": plan.source,
        "question": plan.question,
        "steps": [],
        "validation_only": True,
        "pdf_uploads": 0,
        "business_index_published": False,
    }

    def emit(name, **detail):
        report["steps"].append({"step": name, **detail})
        if progress:
            progress(report)

    requests = plan.requests()
    docs = gateway.invoke(*requests[0])
    dimensions = docs["dimensions"]
    emit("document_embeddings", dimensions=dimensions, provider=docs["provider"], model=docs["requested_model"])
    query = gateway.invoke(*requests[1])
    if query["dimensions"] != dimensions:
        raise ContractError("paper_query_dimension_changed")
    emit("query_embedding", dimensions=query["dimensions"])
    collection = "ss_live_probe_" + UUID(str(grant_id)).hex
    namespace = UUID(str(grant_id))
    point_ids = {e.id: str(uuid5(namespace, e.id)) for e in plan.evidence}
    scope = {"owner_id": str(owner_id), "project_id": str(project_id), "grant_id": str(grant_id)}
    with closing(qdrant_client()) as client:
        if not client.collection_exists(collection):
            client.create_collection(
                collection, vectors_config={"dense": m.VectorParams(size=dimensions, distance=m.Distance.COSINE)}
            )
        for key in scope:
            client.create_payload_index(collection, key, m.PayloadSchemaType.KEYWORD, wait=True)
        client.create_payload_index(
            collection,
            "lexical_text",
            m.TextIndexParams(type=m.TextIndexType.TEXT, tokenizer=m.TokenizerType.WORD, lowercase=True, scoring=True),
            wait=True,
        )
        points = [
            m.PointStruct(
                id=point_ids[e.id],
                vector={"dense": vector},
                payload={
                    **scope,
                    "evidence_id": e.id,
                    "source_sha256": plan.source["sha256"],
                    "pdf_page": e.page,
                    "text_sha256": hashlib.sha256(e.text.encode()).hexdigest(),
                    "lexical_text": lexical(e.text),
                    "validation_only": True,
                },
            )
            for e, vector in zip(plan.evidence, docs["value"], strict=True)
        ]
        client.upsert(collection, points=points, wait=True)
        got = client.retrieve(collection, ids=[p.id for p in points], with_payload=True, with_vectors=True)
        by_id = {str(p.id): p for p in got}
        if set(by_id) != {str(p.id) for p in points} or client.count(collection, exact=True).count != len(points):
            raise ContractError("paper_probe_manifest_mismatch")
        for p in points:
            actual = by_id[str(p.id)]
            vector = p.vector["dense"]
            length = sum(x * x for x in vector) ** 0.5
            if (
                actual.payload != p.payload
                or len(actual.vector["dense"]) != dimensions
                or any(abs(a - b / length) > 1e-5 for a, b in zip(actual.vector["dense"], vector, strict=True))
            ):
                raise ContractError("paper_probe_vector_mismatch")
        filtered = m.Filter(must=[m.FieldCondition(key=k, match=m.MatchValue(value=v)) for k, v in scope.items()])
        dense = client.query_points(
            collection, query=query["value"][0], using="dense", query_filter=filtered, limit=2
        ).points
        sparse = client.query_points(
            collection,
            query=m.TextQuery(text=lexical(plan.question)),
            using="lexical_text",
            query_filter=filtered,
            limit=2,
        ).points
        scores = {}
        for group in (dense, sparse):
            for index, item in enumerate(group):
                scores[str(item.id)] = scores.get(str(item.id), 0) + 1 / (60 + index + 1)
        expected = point_ids[plan.evidence[0].id]
        if not scores or max(scores, key=scores.get) != expected:
            raise ContractError("paper_retrieval_did_not_find_hardware")
        emit(
            "hybrid_retrieval",
            collection=collection,
            dense=[str(p.id) for p in dense],
            bm25=[str(p.id) for p in sparse],
            rrf=scores,
            first_evidence_id=plan.evidence[0].id,
        )
    # Freeze the candidate set/order independently of search ranking for the manifest.
    ranking = gateway.invoke(*requests[2])
    if ranking["value"][0]["index"] != 0:
        raise ContractError("paper_reranker_did_not_select_hardware")
    emit("rerank", ranking=ranking["value"])
    answer = gateway.invoke(*requests[3])
    if answer["value"]["insufficient_evidence"]:
        raise ContractError("official_question_unanswered")
    # Citation validation already checks exact offsets; also check the official fact.
    text = answer["value"]["answer"]
    if not re.search(r"\b8\b|\beight\b|八", text, re.I) or "P100" not in text:
        raise ContractError("official_hardware_answer_incorrect")
    locations = {item["evidence_id"]: item for item in plan.source["locators"]}
    citations = []
    for claim in answer["value"]["claims"]:
        for citation in claim["citations"]:
            location = locations[citation["evidence_id"]]
            citations.append(
                {
                    **citation,
                    "pdf_page": location["pdf_page"],
                    "source_sha256": plan.source["sha256"],
                    "page_text_start": location["page_text_start"] + citation["start"],
                    "page_text_end": location["page_text_start"] + citation["end"],
                }
            )
    if not citations:
        raise ContractError("official_answer_has_no_citation")
    report.update(
        answer=answer["value"],
        citations=citations,
        dimensions=dimensions,
        model_snapshot=gateway.selection.snapshot(),
        passed=True,
    )
    emit("grounded_answer", citation_count=len(citations))
    return serialize(report)
