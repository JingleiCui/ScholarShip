"""Real document workflows with pinned sources and explicit execution modes."""

from uuid import UUID
import sqlalchemy as sa
from scholarship.adapters.providers import ContractError, ExternalDisabled
from scholarship.domains.deep_parse import version_scope
from scholarship.domains.indexing import build_index, snapshot
from scholarship.domains.offline_workflow import enqueue, qa_graph
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.models.configuration import load_selection, restore_snapshot
from scholarship.models.fixtures import transport_factory
from scholarship.models.gateway import SimulatedGateway
from scholarship.models.production import ProductionGateway, require_cloud


def project_selection(owner, project):
    from fastapi import HTTPException

    with tenant_transaction(owner) as conn:
        prefs = conn.scalar(sa.select(T["projects"].c.preferences).where(T["projects"].c.id == project))
        if prefs is None:
            raise HTTPException(404, "project_not_found")
    choice = prefs.get("model_configuration", {})
    return load_selection(choice.get("preset"), choice.get("overrides"))


def enqueue_research(owner, project, key, kind, versions, *, question=None, mode="offline_acceptance"):
    if mode not in {"offline_acceptance", "cloud"} or kind not in {"research_build", "research_qa"}:
        raise ContractError("invalid_research_mode_or_kind")
    selection = project_selection(owner, project)
    if mode == "cloud":
        require_cloud(selection)
        roles = ("embedding",) if kind == "research_build" else ("embedding", "rerank", "chat")
        with tenant_transaction(owner) as conn:
            for role in roles:
                spec = selection.roles[role]
                if not conn.scalar(
                    sa.text(
                        "SELECT EXISTS(SELECT 1 FROM model_runtime.production_policies WHERE project_id=:p AND provider=:v AND model=:m AND role=:r AND enabled AND free_verified AND starts_at<=now() AND ends_at>now() AND requests_used<request_limit)"
                    ),
                    {"p": project, "v": spec.provider, "m": spec.model, "r": role},
                ):
                    raise ExternalDisabled("production_policy_closed")
    ids = sorted(set(versions), key=str)
    if not ids or len(ids) > 8:
        raise ContractError("one_to_eight_versions_required")
    parses = []
    with tenant_transaction(owner) as conn:
        for vid in ids:
            v = version_scope(conn, vid)
            if v["project_id"] != project or v["metadata_json"].get("synthetic"):
                raise ContractError("real_document_in_project_required")
            p = conn.scalar(
                sa.select(T["document_parses"].c.id)
                .where(
                    T["document_parses"].c.version_id == vid,
                    T["document_parses"].c.parser_name == "mineru-native",
                    T["document_parses"].c.status == "ready",
                )
                .order_by(T["document_parses"].c.revision_no.desc())
                .limit(1)
            )
            if not p:
                raise ContractError("ready_native_parse_required")
            parses.append({"version_id": str(vid), "parse_id": str(p)})
    scope = (
        snapshot(owner, project, ids, model_selection=selection, simulated=mode != "cloud")
        if kind == "research_qa"
        else []
    )
    if kind == "research_qa":
        parses = [{"version_id": g["version_id"], "parse_id": g["parse_id"]} for g in scope]
    config = {"mode": mode, "revision": "research-business-v1", "models": selection.snapshot()}
    inputs = {
        "version_ids": [str(v) for v in ids],
        "parses": parses,
        "question": question,
        "configuration": config,
        "scope": scope,
    }
    run = enqueue(owner, project, kind, key, inputs, dispatch=False)
    with tenant_transaction(owner) as conn:
        row = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == run["id"]).with_for_update()).mappings().one()
        if row["config_json"].get("revision") == "research-business-v1":
            return run
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == run["id"])
            .values(config_json=config, input_scope_json={"generations": scope})
        )
        conn.execute(
            T["task_outbox"]
            .insert()
            .values(
                owner_id=owner,
                project_id=project,
                run_id=run["id"],
                event_key=f"{kind}:{run['id']}",
                command=kind,
                payload_json={},
                available_at=sa.func.now(),
                delivery_attempts=0,
            )
        )
    return run


def execute_research(owner, project, run_id, *, interrupt_after=None):
    with tenant_transaction(owner) as conn:
        row = (
            conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == run_id, T["runs"].c.project_id == project))
            .mappings()
            .one()
        )
    config = row["config_json"]
    selection = restore_snapshot(config["models"])
    if config["mode"] == "offline_acceptance":
        gateway = SimulatedGateway(owner, run_id, selection, transport_factory(selection))
    elif config["mode"] == "cloud":
        gateway = ProductionGateway(owner, project, run_id, selection)
    else:
        raise ContractError("invalid_pinned_research_mode")
    with gateway:
        if row["kind"] == "research_qa":
            return qa_graph(owner, project, run_id, interrupt_after=interrupt_after, model_gateway=gateway)
        if row["kind"] != "research_build":
            raise ContractError("research_task_required")
        docs = []
        for source in row["input_json"]["parses"]:
            vid, pid = UUID(source["version_id"]), UUID(source["parse_id"])
            gid = build_index(owner, vid, pid, run_id, model_gateway=gateway, task_generation=True)
            docs.append({**source, "generation_id": str(gid)})
        return {
            "documents": docs,
            "model_simulated": gateway.simulated,
            "source_synthetic": False,
            "external_requests": 0 if gateway.simulated else None,
        }
