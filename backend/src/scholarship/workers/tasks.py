from datetime import datetime, timedelta, timezone
from typing import TypedDict
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import StateGraph, START, END
import sqlalchemy as sa

from scholarship.config import settings
from scholarship.domains.cancellation import RunCancelled, check_cancelled
from scholarship.domains.runs import emit
from scholarship.infrastructure.database import engine, tenant_transaction
from scholarship.infrastructure.health import dependency_health
from scholarship.infrastructure.schema import tables
from scholarship.workers.celery_app import app


class ProbeState(TypedDict):
    result: dict


def graph_probe(run_id):
    """Deterministic local graph; this path has no external provider adapters."""
    with psycopg.connect(
        settings().dsn("checkpoint"),
        autocommit=True,
        row_factory=dict_row,
        prepare_threshold=0,
        options="-c search_path=agent",
    ) as conn:
        saver = PostgresSaver(conn)
        builder = StateGraph(ProbeState)
        builder.add_node("inspect_dependencies", lambda state: {"result": dependency_health()})
        builder.add_edge(START, "inspect_dependencies")
        builder.add_edge("inspect_dependencies", END)
        graph = builder.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": str(run_id)}}
        previous = graph.get_state(config)
        if previous.values.get("result") and not previous.next:
            return previous.values["result"]
        return graph.invoke(None if previous.next else {}, config)["result"]


@app.task(name="scholarship.dispatch")
def dispatch():
    with engine().begin() as conn:
        rows = conn.execute(sa.text("SELECT * FROM scholar.claim_outbox_v2(10)")).mappings().all()
    for row in rows:
        # A failed send leaves the claim to expire; a successful but unrecorded send
        # may be delivered again. The run lease fences duplicate completion.
        app.send_task(
            {
                "preview_parse": "scholarship.preview_parse",
                "offline_build": "scholarship.execute_offline",
                "offline_qa": "scholarship.execute_offline",
                "research_build": "scholarship.execute_offline",
                "research_qa": "scholarship.execute_offline",
                "research_studio": "scholarship.execute_offline",
                "system_probe": "scholarship.execute_probe",
            }[row["command"]],
            args=[str(row["owner_id"]), str(row["project_id"]), str(row["run_id"])],
        )
        with tenant_transaction(row["owner_id"]) as conn:
            conn.execute(
                sa.update(tables["task_outbox"])
                .where(
                    tables["task_outbox"].c.id == row["id"], tables["task_outbox"].c.lease_token == row["lease_token"]
                )
                .values(
                    dispatched_at=sa.func.now(),
                    available_at=sa.func.now() + sa.text("interval '10 seconds'"),
                    lease_until=None,
                    lease_token=None,
                )
            )


@app.task(name="scholarship.execute_probe")
def execute_probe(owner, project, run_id):
    owner, project, run_id = UUID(owner), UUID(project), UUID(run_id)
    token = uuid4()
    now = datetime.now(timezone.utc)
    runs, steps = tables["runs"], tables["run_steps"]
    with tenant_transaction(owner) as conn:
        row = (
            conn.execute(
                sa.select(runs)
                .where(runs.c.id == run_id, runs.c.project_id == project, runs.c.kind == "system_probe")
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if row is None or row["status"] not in {"queued", "running"}:
            return
        if row["status"] == "running" and row["lease_until"] and row["lease_until"] > now:
            return
        attempt = 1 + conn.scalar(
            sa.select(sa.func.coalesce(sa.func.max(steps.c.attempt_no), 0)).where(steps.c.run_id == run_id)
        )
        if attempt > 3:
            conn.execute(
                runs.update()
                .where(runs.c.id == run_id)
                .values(status="failed", finished_at=now, lease_until=None, error_json={"code": "attempt_limit"})
            )
            emit(conn, run_id, "failed", {"message": "恢复次数已达上限。"})
            return
        # Mark a previous expired attempt before creating the fenced replacement.
        conn.execute(
            steps.update()
            .where(steps.c.run_id == run_id, steps.c.status == "running")
            .values(status="failed", error_json={"code": "lease_expired"})
        )
        until = now + timedelta(seconds=settings().lease_seconds)
        conn.execute(
            runs.update()
            .where(runs.c.id == run_id)
            .values(status="running", lease_token=token, lease_until=until, heartbeat_at=now, updated_at=now)
        )
        step_id = conn.scalar(
            steps.insert()
            .values(
                owner_id=owner,
                project_id=project,
                run_id=run_id,
                step_key="local_probe",
                attempt_no=attempt,
                tool_name="langgraph_system_probe",
                status="running",
                lease_token=token,
                lease_until=until,
                input_sha256=row["request_sha256"],
            )
            .returning(steps.c.id)
        )
        emit(conn, run_id, "running", {"message": "后台已领取任务，正在执行本地 LangGraph 探针。", "attempt": attempt})
    try:
        check_cancelled(owner, run_id)
        result = graph_probe(run_id)
        check_cancelled(owner, run_id)
        status = "succeeded" if result["ready"] else "failed"
        error = None if status == "succeeded" else {"code": "dependency_unavailable"}
    except RunCancelled:
        result, status, error = None, "cancelled", {"code": "cancelled_at_safe_boundary"}
    except Exception as exc:
        result, status, error = None, "failed", {"code": "probe_failed", "type": type(exc).__name__}
    with tenant_transaction(owner) as conn:
        # Lock and compare the token before changing any authoritative result.
        active = conn.execute(sa.select(runs.c.lease_token).where(runs.c.id == run_id).with_for_update()).scalar()
        if active != token:
            return
        conn.execute(
            steps.update()
            .where(steps.c.id == step_id, steps.c.lease_token == token)
            .values(
                status="failed" if status == "cancelled" else status,
                output_json=result,
                error_json=error,
                lease_until=None,
                updated_at=sa.func.now(),
            )
        )
        conn.execute(
            runs.update()
            .where(runs.c.id == run_id, runs.c.lease_token == token)
            .values(
                status=status,
                result_json=result,
                error_json=error,
                finished_at=sa.func.now(),
                lease_until=None,
                lease_token=None,
                updated_at=sa.func.now(),
            )
        )
        emit(
            conn,
            run_id,
            status,
            {
                "message": "基础设施探针完成。" if status == "succeeded" else "基础设施探针失败。",
                "result": result,
                "error": error,
            },
        )


@app.task(name="scholarship.metadata_dispatch")
def metadata_dispatch():
    with engine().begin() as conn:
        rows = conn.execute(sa.text("SELECT * FROM scholar.due_metadata_subscriptions(5)")).mappings().all()
    for row in rows:
        metadata_subscription.delay(str(row["owner_id"]), str(row["project_id"]), str(row["id"]))


@app.task(name="scholarship.metadata_subscription")
def metadata_subscription(owner, project, subscription):
    from scholarship.domains.studio.discovery import run_subscription

    return run_subscription(UUID(owner), UUID(project), UUID(subscription))
