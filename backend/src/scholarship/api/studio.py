"""Product endpoints for translation, literature, graphs, drafts and safe charts."""

from io import BytesIO
import json
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Header, UploadFile, File, Form, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict, model_validator
import sqlalchemy as sa
from scholarship.models.figure_contract import FigureDescription
from scholarship.api.auth import Identity, identity
from scholarship.api.offline import demo_actor
from scholarship.adapters.providers import ContractError, ExternalDisabled
from scholarship.domains.deep_parse import persist_file
from scholarship.domains.research import project_selection
from scholarship.domains.studio.workflow import submit
from scholarship.domains.studio.artifacts import downloadable
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.domains.runs import public_run

router = APIRouter(prefix="/studio")


@router.get("/versions/{version_id}/alignment")
def document_alignment(version_id: UUID, page: int = Query(1, ge=1, le=2000), user: Identity = Depends(identity)):
    from scholarship.domains.studio.document_pipeline import alignment
    return alignment(user.user_id, version_id, page)


def history_source_versions(conn, rows, project):
    """Expose only version IDs visible in the same project; never return full task inputs."""
    parse_ids = set()
    version_ids = set()
    by_run = {}
    direct_versions = {}
    for row in rows:
        inputs = row["input_json"].get("arguments", {})
        values = list(inputs.get("parse_ids") or [])
        if inputs.get("parse_id"):
            values.append(inputs["parse_id"])
        source_parse = (row.get("result_json") or {}).get("source", {}).get("parse_id")
        if source_parse:
            values.append(source_parse)
        chosen = set()
        for value in values:
            try:
                chosen.add(UUID(str(value)))
            except ValueError:
                continue
        by_run[row["id"]] = chosen
        parse_ids.update(chosen)
        try:
            direct = {UUID(str(inputs["version_id"]))} if inputs.get("version_id") else set()
        except ValueError:
            direct = set()
        direct_versions[row["id"]] = direct
        version_ids.update(direct)
    mapping = (
        dict(
            conn.execute(
                sa.select(T["document_parses"].c.id, T["document_parses"].c.version_id).where(
                    T["document_parses"].c.id.in_(parse_ids), T["document_parses"].c.project_id == project
                )
            ).all()
        )
        if parse_ids
        else {}
    )
    visible_versions = (
        set(
            conn.scalars(
                sa.select(T["paper_versions"].c.id).where(
                    T["paper_versions"].c.id.in_(version_ids), T["paper_versions"].c.project_id == project
                )
            )
        )
        if version_ids
        else set()
    )
    return {
        rid: sorted(
            {str(mapping[pid]) for pid in ids if pid in mapping}
            | {str(vid) for vid in direct_versions[rid] if vid in visible_versions}
        )
        for rid, ids in by_run.items()
    }


class TextSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: UUID
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=4000)


class EvidenceSpan(TextSelection):
    pass


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal[
        "figure",
        "direction_review",
        "translate",
        "draft",
        "summary",
        "direction",
        "direction_check",
        "review_artifact",
        "diagram",
        "local_parse",
        "prepare_document",
    ]
    mode: Literal["offline_acceptance", "cloud", "local_inference"] = "offline_acceptance"
    version_id: UUID | None = None
    recognize_figures: bool = True
    translate_document: bool = True
    parse_id: UUID | None = None
    parse_ids: list[UUID] = Field(default_factory=list, max_length=30)
    block_ids: list[UUID] = Field(default_factory=list, max_length=200)
    original_evidence: EvidenceSpan | None = None
    followup_evidence: EvidenceSpan | None = None
    question: str = Field(default="", max_length=2000)
    assessment: Literal["supported", "partly_supported", "contradicted", "insufficient"] = "insufficient"
    reason: str = Field(default="", max_length=4000)
    source_selection: TextSelection | None = None
    language: Literal["zh-CN", "en"] = "zh-CN"
    idea: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=2000)
    search_query: str = Field(default="", max_length=500)
    artifact_version_id: UUID | None = None
    nodes: list[str] = Field(default_factory=list, max_length=60)
    edges: list[list[int]] = Field(default_factory=list, max_length=120)

    @model_validator(mode="after")
    def source_required(self):
        if self.operation == "figure" and (len(self.block_ids) != 1 or self.mode not in {"cloud", "local_inference"}):
            raise ValueError("one_figure_and_cloud_mode_required")
        if self.operation == "direction_review" and (
            not self.original_evidence
            or not self.followup_evidence
            or len(self.question.strip()) < 3
            or len(self.reason.strip()) < 3
        ):
            raise ValueError("two_fulltext_spans_and_review_required")
        if self.operation == "review_artifact" and self.artifact_version_id is None:
            raise ValueError("source_artifact_version_required")
        if self.operation in {"local_parse", "prepare_document"} and self.version_id is None:
            raise ValueError("version_id_required")
        if self.operation == "translate" and self.parse_id is None:
            raise ValueError("parse_id_required")
        if self.operation in {"draft", "summary", "direction", "direction_check"} and not self.parse_ids:
            raise ValueError("source_parses_required")
        if self.operation == "direction_check" and len(self.search_query.strip()) < 3:
            raise ValueError("explicit_followup_query_required")
        if self.operation == "diagram" and not self.nodes and not self.description.strip():
            raise ValueError("topology_required")
        return self


@router.post("/projects/{project}/tasks", status_code=202)
def tool(
    project: UUID,
    body: ToolRequest,
    user: Identity = Depends(demo_actor),
    idempotency_key: str = Header(min_length=1, max_length=128),
):
    try:
        return submit(
            user.user_id,
            project,
            idempotency_key,
            body.operation,
            body.model_dump(mode="json", exclude={"operation", "mode"}),
            body.mode,
        )
    except ExternalDisabled:
        raise HTTPException(409, "云端业务尚未启用；请使用离线验收或先配置正式额度。") from None
    except ContractError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/projects/{project}/charts", status_code=202)
async def chart(
    project: UUID,
    file: UploadFile = File(),
    config: str = Form(),
    user: Identity = Depends(demo_actor),
    idempotency_key: str = Header(min_length=1, max_length=128),
):
    project_selection(user.user_id, project)
    raw = await file.read(2 * 1024 * 1024 + 1)
    await file.close()
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, "CSV 最大 2 MB")
    try:
        cfg = json.loads(config)
        if not isinstance(cfg, dict):
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "invalid_chart_config") from None
    with tenant_transaction(user.user_id) as conn:
        fid = persist_file(conn, user.user_id, raw, "csv", "text/csv")
    return submit(user.user_id, project, idempotency_key, "chart", {"file_id": str(fid), "config": cfg}, "local")


@router.get("/projects/{project}/runs")
def runs(
    project: UUID, user: Identity = Depends(identity), operations: str | None = Query(default=None, max_length=200)
):
    from scholarship.domains.studio.workflow import OPERATIONS

    chosen = set(operations.split(",")) if operations else None
    if chosen and not chosen <= OPERATIONS:
        raise HTTPException(422, "invalid_operation_filter")
    with tenant_transaction(user.user_id) as conn:
        rows = (
            conn.execute(
                sa.select(T["runs"])
                .where(
                    T["runs"].c.project_id == project,
                    T["runs"].c.kind == "research_studio",
                    T["runs"].c.config_json["operation"].astext.in_(chosen) if chosen else sa.true(),
                )
                .order_by(T["runs"].c.created_at.desc())
                .limit(30)
            )
            .mappings()
            .all()
        )
        source_versions = history_source_versions(conn, rows, project)
        reviews = {}
        if rows:
            for event in conn.execute(
                sa.select(T["audit_events"])
                .where(
                    T["audit_events"].c.project_id == project,
                    T["audit_events"].c.action == "research_artifact_source_review",
                    T["audit_events"].c.subject_json["run_id"].astext.in_([str(r["id"]) for r in rows]),
                )
                .order_by(T["audit_events"].c.created_at.desc())
            ).mappings():
                reviews.setdefault(event["subject_json"]["run_id"], event["safe_payload"])
        figure_reviews = {}
        artifacts = [
            str(r["result_json"]["artifact_version_id"])
            for r in rows
            if r["result_json"] and r["result_json"].get("artifact_version_id")
        ]
        if artifacts:
            for event in conn.execute(
                sa.select(T["audit_events"])
                .where(
                    T["audit_events"].c.project_id == project,
                    T["audit_events"].c.action == "figure_content_review",
                    T["audit_events"].c.subject_json["artifact_version_id"].astext.in_(artifacts),
                )
                .order_by(T["audit_events"].c.created_at.desc(), T["audit_events"].c.id.desc())
            ).mappings():
                figure_reviews.setdefault(event["subject_json"]["artifact_version_id"], event["safe_payload"])
        return [
            {
                **public_run(r),
                "editorial_review": reviews.get(str(r["id"])),
                "operation": r["config_json"].get("operation"),
                "mode": r["config_json"].get("mode"),
                "source_version_ids": source_versions[r["id"]],
                "content_review": figure_reviews.get(str((r["result_json"] or {}).get("artifact_version_id"))),
            }
            for r in rows
        ]


@router.get("/artifacts/{version_id}")
def download(version_id: UUID, user: Identity = Depends(identity)):
    path, mime, name = downloadable(user.user_id, version_id)
    return FileResponse(path, media_type=mime, filename=name, headers={"Cache-Control": "private, no-store"})


@router.get("/parses/{parse_id}/translations")
def translations(parse_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        rows = conn.execute(
            sa.select(T["translation_revisions"])
            .where(T["translation_revisions"].c.parse_id == parse_id)
            .order_by(T["translation_revisions"].c.revision_no.desc())
        ).mappings()
        return [dict(r) for r in rows]


@router.get("/translations/{translation_id}/segments")
def segments(translation_id: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        rows = (
            conn.execute(
                sa.select(
                    T["translation_segments"], T["document_blocks"].c.canonical_text, T["document_pages"].c.page_no
                )
                .join(T["document_blocks"], T["document_blocks"].c.id == T["translation_segments"].c.block_id)
                .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
                .where(T["translation_segments"].c.translation_id == translation_id)
                .order_by(
                    T["document_pages"].c.page_no,
                    T["document_blocks"].c.ordinal,
                    T["translation_segments"].c.segment_no,
                )
            )
            .mappings()
            .all()
        )
        result = []
        for row in rows:
            r = dict(row)
            span = r["placeholders_json"]
            r["canonical_text"] = r["canonical_text"][
                span.get("source_start", 0) : span.get("source_end", len(r["canonical_text"]))
            ]
            result.append(r)
        return result


class Search(BaseModel):
    provider: Literal["arxiv", "crossref"] = "arxiv"
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=20)


@router.post("/projects/{project}/search")
def search(project: UUID, body: Search, user: Identity = Depends(demo_actor)):
    from scholarship.domains.studio.discovery import search

    project_selection(user.user_id, project)
    try:
        return search(user.user_id, project, body.provider, body.query, body.limit)
    except ContractError as exc:
        raise HTTPException(502, str(exc)) from None


@router.get("/projects/{project}/recommendations")
def recommendations(project: UUID, user: Identity = Depends(identity)):
    from scholarship.domains.studio.discovery import personalize

    with tenant_transaction(user.user_id) as conn:
        rows = conn.execute(
            sa.select(T["recommendations"], T["source_records"].c.record_json, T["recommendation_feedback"].c.vote)
            .join(T["source_records"], T["source_records"].c.id == T["recommendations"].c.source_record_id)
            .outerjoin(
                T["recommendation_feedback"],
                T["recommendation_feedback"].c.recommendation_id == T["recommendations"].c.id,
            )
            .where(T["recommendations"].c.project_id == project)
            .order_by(T["recommendations"].c.created_at.desc())
            .limit(100)
        ).mappings()
        return personalize([dict(r) for r in rows])


class Vote(BaseModel):
    vote: Literal[-1, 0, 1]


@router.post("/recommendations/{recommendation}/verify-code")
def verify_code(recommendation: UUID, user: Identity = Depends(demo_actor)):
    from scholarship.domains.studio.discovery import verify_code

    return verify_code(user.user_id, recommendation)


@router.put("/recommendations/{recommendation}/feedback")
def feedback(recommendation: UUID, body: Vote, user: Identity = Depends(demo_actor)):
    from sqlalchemy.dialects.postgresql import insert

    with tenant_transaction(user.user_id) as conn:
        r = (
            conn.execute(sa.select(T["recommendations"]).where(T["recommendations"].c.id == recommendation))
            .mappings()
            .first()
        )
        if not r:
            raise HTTPException(404, "recommendation_not_found")
        conn.execute(
            insert(T["recommendation_feedback"])
            .values(owner_id=user.user_id, project_id=r["project_id"], recommendation_id=recommendation, vote=body.vote)
            .on_conflict_do_update(
                index_elements=["owner_id", "project_id", "recommendation_id"],
                set_={"vote": body.vote, "updated_at": sa.func.now()},
            )
        )
        return {"vote": body.vote}


@router.post("/sources/{source_id}/import", status_code=202)
async def import_source(
    source_id: UUID, user: Identity = Depends(demo_actor), idempotency_key: str = Header(min_length=1, max_length=128)
):
    from starlette.datastructures import Headers
    from scholarship.api.library import store_upload
    from scholarship.domains.studio.discovery import DiscoveryClient

    with tenant_transaction(user.user_id) as conn:
        r = conn.execute(sa.select(T["source_records"]).where(T["source_records"].c.id == source_id)).mappings().first()
        if not r:
            raise HTTPException(404, "source_not_found")
    client = DiscoveryClient()
    try:
        raw = client.pdf(r["record_json"])
    except ContractError as exc:
        raise HTTPException(409, str(exc)) from None
    finally:
        client.close()
    f = UploadFile(
        BytesIO(raw),
        filename=r["external_id"].replace("/", "_") + ".pdf",
        headers=Headers({"content-type": "application/pdf"}),
    )
    return await store_upload(
        r["project_id"], f, paper_id=None, user=user, idempotency_key=idempotency_key, source_record=r
    )


class Subscription(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    name: str = Field(default="arXiv tracking", min_length=1, max_length=100)
    hours: int = Field(default=24, ge=6, le=168)


@router.post("/projects/{project}/subscriptions")
def subscribe(project: UUID, body: Subscription, user: Identity = Depends(demo_actor)):
    project_selection(user.user_id, project)
    with tenant_transaction(user.user_id) as conn:
        sid = conn.scalar(
            T["subscriptions"]
            .insert()
            .values(
                owner_id=user.user_id,
                project_id=project,
                name=body.name,
                sources_json=["arxiv"],
                query_json={"query": body.query},
                schedule_json={"hours": body.hours},
                timezone="Asia/Shanghai",
                cursor_state={},
                next_due_at=sa.func.now(),
                enabled=True,
            )
            .returning(T["subscriptions"].c.id)
        )
        return {"id": sid}


@router.get("/projects/{project}/subscriptions")
def subscriptions(project: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        return [
            dict(r)
            for r in conn.execute(
                sa.select(T["subscriptions"]).where(T["subscriptions"].c.project_id == project)
            ).mappings()
        ]


@router.delete("/subscriptions/{subscription_id}", status_code=200)
def unsubscribe(subscription_id: UUID, user: Identity = Depends(demo_actor)):
    with tenant_transaction(user.user_id) as conn:
        result = conn.execute(
            T["subscriptions"].update().where(T["subscriptions"].c.id == subscription_id).values(enabled=False)
        )
        if not result.rowcount:
            raise HTTPException(404, "subscription_not_found")
        return {"enabled": False}


class GraphRequest(BaseModel):
    paper_ids: list[UUID] = Field(min_length=1, max_length=100)


@router.post("/projects/{project}/graph")
def graph(project: UUID, body: GraphRequest, user: Identity = Depends(demo_actor)):
    from scholarship.domains.studio.graph import graph

    try:
        return graph(user.user_id, project, body.paper_ids)
    except ContractError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/parses/{parse_id}/references")
def references(parse_id: UUID, user: Identity = Depends(identity)):
    from scholarship.domains.deep_parse import version_scope

    with tenant_transaction(user.user_id) as conn:
        version = conn.scalar(sa.select(T["document_parses"].c.version_id).where(T["document_parses"].c.id == parse_id))
        if not version:
            raise HTTPException(404, "parse_not_found")
        version_scope(conn, version)
        rows = conn.execute(
            sa.select(
                T["bibliographic_refs"],
                T["document_blocks"].c.source_locator,
                T["document_blocks"].c.bbox,
                T["document_pages"].c.page_no,
            )
            .join(T["document_blocks"], T["document_blocks"].c.id == T["bibliographic_refs"].c.entry_block_id)
            .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
            .where(T["bibliographic_refs"].c.parse_id == parse_id)
        ).mappings()
        from scholarship.domains.studio.reference_reviews import apply_reviews

        return [
            {**r, "version_id": str(version), "pdf_bbox": r["source_locator"].get("pdf_bbox")}
            for r in apply_reviews(conn, rows)
        ]


@router.get("/blocks/{block_id}/locate")
def locate_block(block_id: UUID, user: Identity = Depends(identity)):
    from scholarship.domains.deep_parse import version_scope

    with tenant_transaction(user.user_id) as conn:
        row = (
            conn.execute(
                sa.select(T["document_blocks"], T["document_pages"].c.page_no, T["document_parses"].c.version_id)
                .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
                .join(T["document_parses"], T["document_parses"].c.id == T["document_blocks"].c.parse_id)
                .where(T["document_blocks"].c.id == block_id)
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "block_not_found")
        version_scope(conn, row["version_id"])
        return {
            "parse_id": row["parse_id"],
            "version_id": row["version_id"],
            "page": row["page_no"],
            "pdf_bbox": row["source_locator"].get("pdf_bbox"),
            "text": row["canonical_text"],
        }


@router.post("/figures/{block_id}/tasks", status_code=202)
def figure_task(
    block_id: UUID, user: Identity = Depends(demo_actor), idempotency_key: str = Header(min_length=1, max_length=128)
):
    from scholarship.domains.studio.figures import source

    try:
        with tenant_transaction(user.user_id) as conn:
            row = source(conn, block_id)
        return submit(
            user.user_id, row["project_id"], idempotency_key, "figure", {"block_ids": [str(block_id)]}, "cloud"
        )
    except ExternalDisabled:
        raise HTTPException(409, "cloud_vision_admission_required") from None
    except ContractError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/figures/{block_id}/results")
def figure_results(block_id: UUID, user: Identity = Depends(identity)):
    from scholarship.domains.studio.figures import source

    try:
        with tenant_transaction(user.user_id) as conn:
            row = source(conn, block_id)
            runs = (
                conn.execute(
                    sa.select(T["runs"])
                    .where(
                        T["runs"].c.project_id == row["project_id"],
                        T["runs"].c.config_json["operation"].astext.in_(["figure", "figure_correct"]),
                        T["runs"].c.input_json["arguments"]["block_ids"].contains([str(block_id)]),
                    )
                    .order_by(T["runs"].c.created_at.desc())
                    .limit(10)
                )
                .mappings()
                .all()
            )
            output = []
            for r in runs:
                result = r["result_json"]
                review = None
                if result and result.get("artifact_version_id"):
                    review = conn.scalar(
                        sa.select(T["audit_events"].c.safe_payload)
                        .where(
                            T["audit_events"].c.action == "figure_content_review",
                            T["audit_events"].c.subject_json["artifact_version_id"].astext
                            == result["artifact_version_id"],
                        )
                        .order_by(T["audit_events"].c.created_at.desc(), T["audit_events"].c.id.desc())
                        .limit(1)
                    )
                output.append({"id": str(r["id"]), "status": r["status"], "result": result, "review": review})
            return output
    except ContractError as exc:
        raise HTTPException(404, str(exc)) from None


class FigureReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment: Literal["accepted", "rejected"]
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/figure-artifacts/{artifact_id}/reviews")
def review_figure(artifact_id: UUID, body: FigureReview, user: Identity = Depends(demo_actor)):
    if len(body.reason.strip()) < 3:
        raise HTTPException(422, "review_reason_required")
    with tenant_transaction(user.user_id) as conn:
        row = (
            conn.execute(
                sa.select(T["artifact_versions"], T["artifacts"].c.project_id)
                .join(T["artifacts"], T["artifacts"].c.id == T["artifact_versions"].c.artifact_id)
                .where(T["artifact_versions"].c.id == artifact_id, T["artifacts"].c.kind == "figure")
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "figure_artifact_not_found")
        version_scope_id = UUID(row["metadata_json"]["source"]["version_id"])
        from scholarship.domains.deep_parse import version_scope

        version_scope(conn, version_scope_id)
        conn.execute(
            T["audit_events"]
            .insert()
            .values(
                owner_id=user.user_id,
                project_id=row["project_id"],
                action="figure_content_review",
                subject_json={"artifact_version_id": str(artifact_id)},
                safe_payload={"assessment": body.assessment, "reason": body.reason},
            )
        )
    return body.model_dump()


class FigureCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: FigureDescription
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/figure-artifacts/{artifact_id}/corrections", status_code=202)
def correct_figure(
    artifact_id: UUID,
    body: FigureCorrection,
    user: Identity = Depends(demo_actor),
    idempotency_key: str = Header(min_length=1, max_length=128),
):
    if len(body.reason.strip()) < 3:
        raise HTTPException(422, "review_reason_required")
    with tenant_transaction(user.user_id) as conn:
        row = (
            conn.execute(
                sa.select(T["artifact_versions"], T["artifacts"].c.project_id)
                .join(T["artifacts"], T["artifacts"].c.id == T["artifact_versions"].c.artifact_id)
                .where(T["artifact_versions"].c.id == artifact_id, T["artifacts"].c.kind == "figure")
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "figure_artifact_not_found")
        from scholarship.domains.studio.figures import source

        origin = source(conn, UUID(row["metadata_json"]["source"]["block_id"]))
    return submit(
        user.user_id,
        row["project_id"],
        idempotency_key,
        "figure_correct",
        {
            "artifact_version_id": str(artifact_id),
            "block_ids": [str(origin["id"])],
            "description": body.description.model_dump(mode="json"),
            "reason": body.reason,
        },
        "local",
    )
