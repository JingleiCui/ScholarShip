"""Cooperative cancellation at safe boundaries; never erase remote-call receipts."""

from uuid import UUID
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from scholarship.api.auth import Identity
from scholarship.api.offline import demo_actor
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.domains.runs import emit, public_run, TERMINAL

router = APIRouter()


@router.post("/runs/{run_id}/cancel")
def request_cancel(run_id: UUID, user: Identity = Depends(demo_actor)):
    with tenant_transaction(user.user_id) as conn:
        row = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == run_id).with_for_update()).mappings().first()
        if not row:
            raise HTTPException(404, "run_not_found")
        if row["status"] in TERMINAL or row["cancel_requested_at"]:
            return public_run(row)
        values = {"cancel_requested_at": sa.func.now()}
        # waiting_user may carry unknown provider results: retain that state for reconciliation.
        if row["status"] == "queued":
            values.update(status="cancelled", finished_at=sa.func.now(), lease_token=None, lease_until=None)
        result = (
            conn.execute(T["runs"].update().where(T["runs"].c.id == run_id).values(**values).returning(T["runs"]))
            .mappings()
            .one()
        )
        emit(
            conn,
            run_id,
            "cancelled" if result["status"] == "cancelled" else "cancel_requested",
            {
                "message": "已取消排队任务。"
                if result["status"] == "cancelled"
                else "取消已请求；正在执行的请求先保留回执，在安全边界停止。"
            },
        )
        return public_run(result)
