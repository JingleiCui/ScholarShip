"""Cooperative cancellation at safe boundaries; preserve remote-call receipts."""

import sqlalchemy as sa
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T


class RunCancelled(Exception):
    pass


def check_cancelled(owner, run):
    with tenant_transaction(owner) as conn:
        row = conn.execute(
            sa.select(T["runs"].c.cancel_requested_at, T["runs"].c.status).where(T["runs"].c.id == run)
        ).first()
        if row and (row.cancel_requested_at or row.status == "cancelled"):
            raise RunCancelled()
