"""Append-only identity review overlay; published reference content stays immutable."""

from uuid import UUID
import sqlalchemy as sa
from scholarship.infrastructure.schema import tables as T


def apply_reviews(conn, rows):
    rows = [dict(r) for r in rows]
    if not rows:
        return rows
    events = conn.execute(
        sa.select(T["audit_events"])
        .where(
            T["audit_events"].c.action == "reference_identity_review",
            T["audit_events"].c.subject_json["reference_id"].astext.in_([str(r["id"]) for r in rows]),
        )
        .order_by(T["audit_events"].c.created_at.desc(), T["audit_events"].c.id.desc())
    ).mappings()
    latest = {}
    for e in events:
        latest.setdefault(e["subject_json"]["reference_id"], e)
    for row in rows:
        event = latest.get(str(row["id"]))
        if event:
            pid = event["safe_payload"].get("paper_id")
            row.update(
                resolved_paper_id=UUID(pid) if pid else None,
                resolution_state="verified" if pid else "unresolved",
                human_review={
                    "reason": event["safe_payload"]["reason"],
                    "review_id": str(event["id"]),
                    "reviewed_at": event["created_at"].isoformat(),
                },
            )
    return rows
