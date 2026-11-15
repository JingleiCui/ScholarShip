"""Immutable tenant-scoped deliverables with input provenance."""

from pathlib import Path
from uuid import UUID
import sqlalchemy as sa
from fastapi import HTTPException
from scholarship.config import settings
from scholarship.domains.deep_parse import persist_file
from scholarship.domains.retrieval_profile import canonical, digest
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T


def publish(
    conn, owner, project, run, kind, name, raw, *, mime="application/zip", suffix="zip", metadata=None, inputs=()
):
    job = (
        conn.execute(
            sa.select(T["runs"]).where(T["runs"].c.id == run, T["runs"].c.project_id == project).with_for_update()
        )
        .mappings()
        .first()
    )
    if not job:
        raise HTTPException(404, "run_not_found")
    old = (
        conn.execute(
            sa.select(T["artifact_versions"])
            .join(T["artifacts"], T["artifacts"].c.id == T["artifact_versions"].c.artifact_id)
            .where(
                T["artifact_versions"].c.producing_run_id == run,
                T["artifacts"].c.kind == kind,
                T["artifacts"].c.name == name,
            )
        )
        .mappings()
        .first()
    )
    if old:
        f = conn.execute(sa.select(T["file_objects"]).where(T["file_objects"].c.id == old["file_id"])).mappings().one()
        if f["sha256"] != digest(raw):
            raise ValueError("immutable_artifact_conflict")
        return old["id"]
    scope = {"owner_id": owner, "project_id": project}
    fid = persist_file(conn, owner, raw, suffix, mime)
    aid = conn.scalar(T["artifacts"].insert().values(**scope, kind=kind, name=name).returning(T["artifacts"].c.id))
    vid = conn.scalar(
        T["artifact_versions"]
        .insert()
        .values(
            **scope,
            artifact_id=aid,
            file_id=fid,
            producing_run_id=run,
            revision_no=1,
            metadata_json=metadata or {},
            config_sha256=digest(canonical(job["config_json"])),
            published_at=sa.func.now(),
        )
        .returning(T["artifact_versions"].c.id)
    )
    for i, item in enumerate(inputs):
        conn.execute(
            T["artifact_inputs"]
            .insert()
            .values(
                **scope,
                output_version_id=vid,
                input_role=item["role"],
                ordinal=i,
                **{k: UUID(str(v)) for k, v in item.items() if k != "role"},
            )
        )
    conn.execute(T["artifacts"].update().where(T["artifacts"].c.id == aid).values(current_version_id=vid))
    return vid


def downloadable(owner, version_id):
    with tenant_transaction(owner) as conn:
        row = (
            conn.execute(
                sa.select(T["file_objects"], T["artifacts"].c.name)
                .join(T["artifact_versions"], T["artifact_versions"].c.file_id == T["file_objects"].c.id)
                .join(T["artifacts"], T["artifacts"].c.id == T["artifact_versions"].c.artifact_id)
                .where(
                    T["artifact_versions"].c.id == version_id,
                    T["artifacts"].c.deleted_at.is_(None),
                    T["file_objects"].c.state == "ready",
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "artifact_not_found")
        root = (Path(settings().scholar_data_dir) / "files").resolve()
        path = (root / row["storage_key"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or digest(path.read_bytes()) != row["sha256"]:
            raise HTTPException(409, "artifact_integrity_error")
        return path, row["mime_type"], row["name"]


def write_member(archive, name, raw):
    import zipfile

    item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    item.compress_type = zipfile.ZIP_DEFLATED
    item.external_attr = 0o600 << 16
    archive.writestr(item, raw)
