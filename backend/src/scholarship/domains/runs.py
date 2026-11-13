import hashlib
import json
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from fastapi import HTTPException

from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables

TERMINAL = {"succeeded", "partially_succeeded", "failed", "cancelled"}


def emit(conn, run_id, event_type, payload):
    run = conn.execute(
        sa.update(tables["runs"])
        .where(tables["runs"].c.id == run_id)
        .values(next_event_seq=tables["runs"].c.next_event_seq + 1, updated_at=sa.func.now())
        .returning(tables["runs"].c.owner_id, tables["runs"].c.project_id, tables["runs"].c.next_event_seq)
    ).one()
    conn.execute(
        tables["run_events"]
        .insert()
        .values(
            owner_id=run.owner_id,
            project_id=run.project_id,
            run_id=run_id,
            seq=run.next_event_seq - 1,
            event_type=event_type,
            public_payload=payload,
        )
    )


def public_run(row):
    return {
        k: row[k]
        for k in ("id", "project_id", "kind", "status", "result_json", "error_json", "created_at", "finished_at")
    }


def create_probe(owner_id: UUID, project_id: UUID, key: str):
    request_hash = hashlib.sha256(
        json.dumps({"kind": "system_probe", "project_id": str(project_id)}, sort_keys=True).encode()
    ).hexdigest()
    with tenant_transaction(owner_id) as conn:
        if not conn.scalar(sa.select(tables["projects"].c.id).where(tables["projects"].c.id == project_id)):
            raise HTTPException(404, "project_not_found")
        runs = tables["runs"]
        stmt = (
            insert(runs)
            .values(
                owner_id=owner_id,
                project_id=project_id,
                kind="system_probe",
                status="queued",
                idempotency_key=key,
                request_sha256=request_hash,
                input_json={},
                config_json={"revision": "probe-v1"},
                input_scope_json={},
                next_event_seq=1,
                event_floor=1,
            )
            .on_conflict_do_nothing(index_elements=[runs.c.owner_id, runs.c.project_id, runs.c.idempotency_key])
            .returning(runs)
        )
        row = conn.execute(stmt).mappings().first()
        if row is None:
            row = (
                conn.execute(sa.select(runs).where(runs.c.project_id == project_id, runs.c.idempotency_key == key))
                .mappings()
                .one()
            )
            if row["request_sha256"] != request_hash:
                raise HTTPException(409, "idempotency_conflict")
            return public_run(row)
        conn.execute(
            tables["task_outbox"]
            .insert()
            .values(
                owner_id=owner_id,
                project_id=project_id,
                run_id=row["id"],
                event_key=f"probe:{row['id']}",
                command="system_probe",
                payload_json={},
                available_at=sa.func.now(),
                delivery_attempts=0,
            )
        )
        emit(conn, row["id"], "queued", {"message": "任务已保存，等待后台投递。"})
        return public_run(row)


def get_run(owner_id, run_id):
    with tenant_transaction(owner_id) as conn:
        row = conn.execute(sa.select(tables["runs"]).where(tables["runs"].c.id == run_id)).mappings().first()
        if row is None:
            raise HTTPException(404, "run_not_found")
        return dict(row)


def read_events(owner_id, run_id, after):
    with tenant_transaction(owner_id) as conn:
        run = conn.execute(sa.select(tables["runs"]).where(tables["runs"].c.id == run_id)).mappings().first()
        if run is None:
            raise HTTPException(404, "run_not_found")
        if after < run["event_floor"] - 1:
            raise HTTPException(410, "event_cursor_expired")
        if after >= run["next_event_seq"]:
            raise HTTPException(400, "event_cursor_ahead")
        events = (
            conn.execute(
                sa.select(
                    tables["run_events"].c.seq, tables["run_events"].c.event_type, tables["run_events"].c.public_payload
                )
                .where(tables["run_events"].c.run_id == run_id, tables["run_events"].c.seq > after)
                .order_by(tables["run_events"].c.seq)
                .limit(100)
            )
            .mappings()
            .all()
        )
        return [dict(e) for e in events], run["status"] in TERMINAL
