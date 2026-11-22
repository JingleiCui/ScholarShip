"""Save named model selections; changing selection never enables inference."""

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict
import sqlalchemy as sa
from scholarship.api.auth import Identity, identity, mutation_identity
from scholarship.models.configuration import load_selection, configuration_status
from scholarship.models.catalog import binding_snapshot
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T

router = APIRouter()


def project_settings(conn, project):
    row = (
        conn.execute(sa.select(T["projects"]).where(T["projects"].c.id == project).with_for_update()).mappings().first()
    )
    if not row:
        raise HTTPException(404, "project_not_found")
    return row


def selected(preferences):
    choice = preferences.get("model_configuration", {})
    return load_selection(choice.get("preset"), choice.get("overrides"))


def existing_indexes_need_rebuild(conn, project, spec):
    rows = conn.execute(
        sa.select(T["index_profiles"])
        .join(T["index_generations"], T["index_generations"].c.profile_id == T["index_profiles"].c.id)
        .join(T["paper_versions"], T["paper_versions"].c.active_index_id == T["index_generations"].c.id)
        .where(T["paper_versions"].c.project_id == project, T["paper_versions"].c.deleted_at.is_(None))
    ).mappings()
    for profile in rows:
        provider = "mock" if profile["provider"] == "offline" else profile["provider"]
        if provider != spec.provider or profile["model_name"] != spec.model:
            return True
        if spec.dimensions is not None and profile["dimensions"] != spec.dimensions:
            return True
        config = profile["config_json"]
        if spec.provider != "mock" and (
            config.get("base_url") != spec.base_url
            or config.get("adapter_binding") != binding_snapshot(spec, "embedding")
        ):
            return True
        if spec.provider != "mock" and config.get("simulated"):
            return True
    return False


@router.get("/system/models")
def models(user: Identity = Depends(identity)):
    status = configuration_status()
    with tenant_transaction(user.user_id) as conn:
        status["own_simulated_call_counts"] = [
            dict(r)
            for r in conn.execute(
                sa.text(
                    "SELECT provider,state,count(*) AS count FROM model_runtime.calls WHERE simulated GROUP BY provider,state"
                )
            ).mappings()
        ]
    return status


@router.get("/projects/{project}/model-configuration")
def project_models(project: UUID, user: Identity = Depends(identity)):
    with tenant_transaction(user.user_id) as conn:
        row = project_settings(conn, project)
        selection = selected(row["preferences"])
        status = configuration_status(
            selection.preset, overrides=row["preferences"].get("model_configuration", {}).get("overrides")
        )
        validations = [
            dict(r)
            for r in conn.execute(
                sa.text(
                    "SELECT state,max_requests,used_requests,stop_code FROM model_runtime.grants WHERE project_id=:p ORDER BY created_at DESC LIMIT 3"
                ),
                {"p": project},
            ).mappings()
        ]
        for role, spec in selection.roles.items():
            receipt = (
                conn.execute(
                    sa.text(
                        "SELECT state,receipt_json,created_at FROM model_runtime.calls WHERE project_id=:p AND role=:r AND provider=:v AND model=:m AND NOT simulated ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"p": project, "r": role, "v": spec.provider, "m": spec.model},
                )
                .mappings()
                .first()
            )
            if receipt:
                status["roles"][role]["verification_state"] = (
                    "received_valid_response" if receipt["state"] == "succeeded" else "last_attempt_not_successful"
                )
                status["roles"][role]["last_checked_at"] = receipt["created_at"]
                dimensions = receipt["receipt_json"].get("dimensions")
                if role == "embedding" and receipt["state"] == "succeeded" and type(dimensions) is int:
                    status["roles"][role]["dimensions"] = dimensions
                    status["roles"][role]["dimensions_state"] = "native_receipt_proved"
        from scholarship.models.availability import local_readiness

        return {
            **status,
            "local_ready": local_readiness(selection),
            "production_policies": [
                dict(r)
                for r in conn.execute(
                    sa.text(
                        "SELECT role,provider,model,enabled,request_limit,requests_used,ends_at FROM model_runtime.production_policies WHERE project_id=:p ORDER BY starts_at DESC LIMIT 20"
                    ),
                    {"p": project},
                ).mappings()
            ],
            "bounded_validations": validations,
            "fingerprint": selection.snapshot()["fingerprint"],
            "requires_reindex": row["preferences"].get("model_configuration", {}).get("requires_reindex", False)
            or existing_indexes_need_rebuild(conn, project, selection.roles["embedding"]),
            "execution_mode": "configuration_only",
            "active_indexes_unchanged": True,
        }


class Switch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: str = Field(max_length=40)
    overrides: dict[str, str] = Field(default_factory=dict, max_length=5)
    expected_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.put("/projects/{project}/model-configuration")
def switch(project: UUID, body: Switch, user: Identity = Depends(mutation_identity)):
    if user.role != "researcher":
        raise HTTPException(403, "researcher_required")
    try:
        new = load_selection(body.preset, body.overrides)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "invalid_named_model_selection") from None
    with tenant_transaction(user.user_id) as conn:
        row = project_settings(conn, project)
        old = selected(row["preferences"])
        if old.snapshot()["fingerprint"] != body.expected_fingerprint:
            raise HTTPException(409, "model_configuration_changed_refresh_required")
        embedding_changed = (
            old.roles["embedding"] != new.roles["embedding"] or old.bindings["embedding"] != new.bindings["embedding"]
        )
        parser_changed = old.roles["parser"] != new.roles["parser"]
        preferences = {
            **row["preferences"],
            "model_configuration": {
                "preset": body.preset,
                "overrides": body.overrides,
                "snapshot": new.snapshot(),
                "requires_reindex": embedding_changed
                or existing_indexes_need_rebuild(conn, project, new.roles["embedding"])
                or row["preferences"].get("model_configuration", {}).get("requires_reindex", False),
            },
        }
        conn.execute(
            T["projects"]
            .update()
            .where(T["projects"].c.id == project)
            .values(preferences=preferences, updated_at=sa.func.now())
        )
        conn.execute(
            T["audit_events"]
            .insert()
            .values(
                owner_id=user.user_id,
                project_id=project,
                action="model_configuration_selected",
                subject_json={"fingerprint": new.snapshot()["fingerprint"]},
                safe_payload={
                    "preset": new.preset,
                    "embedding_changed": embedding_changed,
                    "parser_changed": parser_changed,
                },
            )
        )
        return {
            "fingerprint": new.snapshot()["fingerprint"],
            "requires_reindex": preferences["model_configuration"]["requires_reindex"],
            "requires_new_parse": parser_changed,
            "active_indexes_unchanged": True,
            "execution_enabled": False,
        }
