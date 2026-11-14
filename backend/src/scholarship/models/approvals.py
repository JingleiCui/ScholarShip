"""Explicit maintenance only: immutable approval manifests and cumulative lineage."""

from datetime import datetime, timedelta, timezone
from uuid import uuid5, NAMESPACE_URL

from psycopg.types.json import Jsonb
import sqlalchemy as sa

from scholarship.adapters.providers import ExternalDisabled
from scholarship.models.configuration import fingerprint
from scholarship.models.live import make_manifest
from scholarship.infrastructure.schema import tables as T


def issue_approval(
    db,
    *,
    key,
    selection,
    requests,
    total_limit,
    prior_keys=(),
    owner_id=None,
    project_id=None,
    approved=False,
    data_scope=None,
):
    if approved is not True:
        raise ExternalDisabled("explicit_approval_required")
    manifest = make_manifest(selection, requests, data_scope=data_scope)
    if type(total_limit) is not int or not 1 <= total_limit <= 200 or len(set(prior_keys)) != len(prior_keys):
        raise ValueError("invalid_cumulative_limit")
    with db.begin() as conn:
        parents = [
            conn.execute(sa.text("SELECT * FROM model_runtime.grants WHERE approval_key=:key FOR UPDATE"), {"key": k})
            .mappings()
            .one()
            for k in prior_keys
        ]
        previous = sum(p["used_requests"] for p in parents)
        parent_id = str(parents[-1]["id"]) if parents else None
        if parents:
            owner_id, project_id = parents[-1]["owner_id"], parents[-1]["project_id"]
            for index, p in enumerate(parents):
                expected_parent = str(parents[index - 1]["id"]) if index else None
                if (
                    p["state"] == "active"
                    or p["preflight_json"].get("parent_grant_id") != expected_parent
                    or p["owner_id"] != owner_id
                    or p["project_id"] != project_id
                ):
                    raise ValueError("invalid_approval_lineage")
        existing = (
            conn.execute(sa.text("SELECT * FROM model_runtime.grants WHERE approval_key=:key"), {"key": key})
            .mappings()
            .first()
        )
        if existing:
            if (
                existing["manifest_sha256"] != fingerprint(manifest)
                or existing["max_requests"] + previous != total_limit
                or existing["preflight_json"].get("parent_grant_id") != parent_id
            ):
                raise ValueError("approval_conflict")
            return dict(existing)  # never reset, refill, reopen, or rewrite prior calls
        if len(requests) + previous != total_limit:
            raise ValueError("remaining_approval_slots_mismatch")
        if parent_id and conn.scalar(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM model_runtime.grants WHERE preflight_json->>'parent_grant_id'=:parent)"
            ),
            {"parent": parent_id},
        ):
            raise ValueError("parent_already_has_continuation")
        if not conn.scalar(
            sa.select(T["projects"].c.id).where(T["projects"].c.id == project_id, T["projects"].c.owner_id == owner_id)
        ):
            raise ValueError("approval_project_not_owned")
        run = uuid5(NAMESPACE_URL, "scholarship:" + key)
        conn.execute(
            T["runs"]
            .insert()
            .values(
                id=run,
                owner_id=owner_id,
                project_id=project_id,
                kind="provider_validation",
                status="running",
                idempotency_key=key,
                request_sha256=fingerprint(manifest),
                input_json={"synthetic": data_scope is None, "total_limit": total_limit, "data_scope": data_scope},
                config_json={"models": selection.snapshot()},
                input_scope_json={},
                next_event_seq=1,
                event_floor=1,
            )
        )
        gid = conn.connection.driver_connection.execute(
            """INSERT INTO model_runtime.grants
          (approval_key,owner_id,project_id,run_id,manifest_json,manifest_sha256,max_requests,max_output_tokens,state,expires_at,preflight_json)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s) RETURNING id""",
            (
                key,
                owner_id,
                project_id,
                run,
                Jsonb(manifest),
                fingerprint(manifest),
                len(requests),
                selection.max_output_tokens,
                datetime.now(timezone.utc) + timedelta(days=1),
                Jsonb({"parent_grant_id": parent_id, "previous_consumed": previous, "total_limit": total_limit}),
            ),
        ).fetchone()[0]
        conn.execute(
            T["audit_events"]
            .insert()
            .values(
                owner_id=owner_id,
                project_id=project_id,
                action="explicit_synthetic_approval_recorded",
                subject_json={"grant_id": str(gid)},
                safe_payload={
                    "parent_grant_id": parent_id,
                    "total_limit": total_limit,
                    "previous_consumed": previous,
                    "remaining_slots": len(requests),
                },
            )
        )
        return dict(
            conn.execute(sa.text("SELECT * FROM model_runtime.grants WHERE id=:id"), {"id": gid}).mappings().one()
        )


def history(conn, grant_id):
    """Runtime/RLS read of the exact ancestry, with cycle and depth guards."""
    rows = (
        conn.execute(
            sa.text("""WITH RECURSIVE lineage AS (
      SELECT *, ARRAY[id::text] AS path FROM model_runtime.grants WHERE id=:id
      UNION ALL SELECT g.*, l.path || g.id::text FROM model_runtime.grants g JOIN lineage l
      ON g.id::text=l.preflight_json->>'parent_grant_id'
      WHERE NOT g.id::text=ANY(l.path) AND cardinality(l.path)<20)
      SELECT id,state,used_requests,max_requests,stop_code,approval_key,preflight_json FROM lineage"""),
            {"id": grant_id},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
