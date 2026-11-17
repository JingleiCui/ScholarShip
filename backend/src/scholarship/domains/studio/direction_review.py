"""Human scientific assessment grounded in two immutable full-text spans."""

from uuid import UUID
import sqlalchemy as sa
from scholarship.adapters.providers import ContractError
from scholarship.domains.deep_parse import version_scope
from scholarship.domains.retrieval_profile import canonical
from scholarship.domains.studio.artifacts import publish
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T


def review(owner, project, run, inputs):
    anchors = []
    with tenant_transaction(owner) as conn:
        for key in ("original_evidence", "followup_evidence"):
            span = inputs[key]
            row = (
                conn.execute(
                    sa.select(T["document_blocks"], T["document_pages"].c.page_no, T["document_parses"].c.version_id)
                    .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
                    .join(T["document_parses"], T["document_parses"].c.id == T["document_blocks"].c.parse_id)
                    .where(
                        T["document_blocks"].c.id == UUID(span["block_id"]),
                        T["document_blocks"].c.project_id == project,
                        T["document_parses"].c.status == "ready",
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                raise ContractError("direction_evidence_scope_mismatch")
            version_scope(conn, row["version_id"])
            start, end = span["start"], span["end"]
            if not 0 <= start < end <= len(row["canonical_text"]) or row["canonical_text"][start:end] != span["text"]:
                raise ContractError("direction_evidence_text_mismatch")
            anchors.append(
                {
                    "role": key,
                    "parse_id": str(row["parse_id"]),
                    "version_id": str(row["version_id"]),
                    "block_id": str(row["id"]),
                    "page": row["page_no"],
                    "start": start,
                    "end": end,
                    "quote": span["text"],
                }
            )
        if anchors[0]["version_id"] == anchors[1]["version_id"]:
            raise ContractError("distinct_followup_document_required")
        result = {
            "question": inputs["question"],
            "assessment": inputs["assessment"],
            "reason": inputs["reason"],
            "evidence": anchors,
            "reviewer_id": str(owner),
            "method": "human_fulltext_comparison",
            "scientific_quality_verified": False,
            "model_requests": 0,
            "notice": "人工核对结论，仅覆盖所选全文片段；需核对后续论文时间、假设、实验条件和检索覆盖范围。",
        }
        aid = publish(
            conn,
            owner,
            project,
            run,
            "direction_review",
            "direction-review.json",
            canonical(result).encode(),
            mime="application/json",
            suffix="json",
            metadata=result,
            inputs=[{"role": a["role"], "source_parse_id": UUID(a["parse_id"])} for a in anchors],
        )
        return {**result, "artifact_version_id": str(aid), "model_simulated": False}
