"""Leased offline task execution with heartbeat and bounded outbox recovery."""

from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from uuid import UUID, uuid4
import sqlalchemy as sa
from scholarship.domains.cancellation import RunCancelled, check_cancelled
from scholarship.adapters.providers import ContractError, UnknownOutcome, ExternalDisabled
from scholarship.models.contracts import ProviderFailure
from scholarship.domains.research import execute_research
from scholarship.config import settings
from scholarship.domains.offline_workflow import build_demo, qa_graph
from scholarship.domains.runs import emit
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.workers.celery_app import app


@app.task(name="scholarship.execute_offline")
def execute_offline(owner, project, run_id):
    owner, project, run_id = UUID(owner), UUID(project), UUID(run_id)
    token = uuid4()
    now = datetime.now(timezone.utc)
    with tenant_transaction(owner) as conn:
        r = (
            conn.execute(
                sa.select(T["runs"])
                .where(
                    T["runs"].c.id == run_id,
                    T["runs"].c.project_id == project,
                    T["runs"].c.kind.in_(
                        ["offline_build", "offline_qa", "research_build", "research_qa", "research_studio"]
                    ),
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if (
            not r
            or r["status"] not in {"queued", "running"}
            or (r["status"] == "running" and r["lease_until"] and r["lease_until"] > now)
        ):
            return
        attempts = conn.scalar(
            sa.select(sa.func.count())
            .select_from(T["run_steps"])
            .where(T["run_steps"].c.run_id == run_id, T["run_steps"].c.step_key == "offline_execution")
        )
        if attempts >= 3:
            conn.execute(
                T["runs"]
                .update()
                .where(T["runs"].c.id == run_id)
                .values(
                    status="failed",
                    lease_token=None,
                    lease_until=None,
                    finished_at=now,
                    error_json={"code": "attempt_limit"},
                )
            )
            emit(conn, run_id, "failed", {"message": "离线恢复已达 3 次上限。"})
            return
        conn.execute(
            T["run_steps"]
            .update()
            .where(
                T["run_steps"].c.run_id == run_id,
                T["run_steps"].c.step_key == "offline_execution",
                T["run_steps"].c.status == "running",
            )
            .values(status="failed", error_json={"code": "lease_expired"})
        )
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == run_id)
            .values(
                status="running",
                lease_token=token,
                lease_until=now + timedelta(seconds=settings().lease_seconds),
                heartbeat_at=now,
            )
        )
        step = conn.scalar(
            T["run_steps"]
            .insert()
            .values(
                owner_id=owner,
                project_id=project,
                run_id=run_id,
                step_key="offline_execution",
                attempt_no=attempts + 1,
                tool_name="offline_business_graph",
                status="running",
                lease_token=token,
                input_sha256=r["request_sha256"],
            )
            .returning(T["run_steps"].c.id)
        )
        emit(conn, run_id, "running", {"message": "执行已固定文献版本与模型配置的任务。", "attempt": attempts + 1})
    stop = Event()

    def heartbeat():
        while not stop.wait(max(1, settings().lease_seconds // 3)):
            try:
                with tenant_transaction(owner) as conn:
                    conn.execute(
                        T["runs"]
                        .update()
                        .where(T["runs"].c.id == run_id, T["runs"].c.lease_token == token)
                        .values(
                            heartbeat_at=sa.func.now(),
                            lease_until=sa.func.now() + sa.text(f"interval '{settings().lease_seconds} seconds'"),
                        )
                    )
            except Exception:
                return  # Lease expiry permits a bounded, idempotent recovery.

    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    result = None
    error = None
    status = "succeeded"
    try:
        check_cancelled(owner, run_id)
        if r["kind"] == "research_studio":
            from scholarship.domains.studio.workflow import execute

            result = execute(owner, project, run_id)
        elif r["kind"].startswith("research_"):
            result = execute_research(owner, project, run_id)
        else:
            result = (
                build_demo(owner, project, run_id) if r["kind"] == "offline_build" else qa_graph(owner, project, run_id)
            )
        if result is None:
            raise RuntimeError("graph_incomplete")
    except RunCancelled:
        status = "cancelled"
        result = None
        error = {"code": "cancelled_at_safe_boundary"}
    except (ProviderFailure, ExternalDisabled) as exc:
        status = "waiting_user"
        error = {"code": exc.code if isinstance(exc, ProviderFailure) else "production_admission_closed"}
    except UnknownOutcome:
        status = "waiting_user"
        error = {"code": "offline_call_unknown_reconcile_required"}
    except ContractError as exc:
        if r["config_json"].get("mode") == "cloud":
            with tenant_transaction(owner) as conn:
                calls = (
                    conn.execute(
                        sa.text(
                            "SELECT id FROM model_runtime.calls WHERE run_id=:r AND NOT simulated ORDER BY created_at DESC LIMIT 1"
                        ),
                        {"r": run_id},
                    )
                    .scalars()
                    .all()
                )
                for cid in calls:
                    conn.execute(sa.text("SELECT model_runtime.suspend_production(:id)"), {"id": cid})
        status = "failed"
        import re

        error = {
            "code": "offline_contract_rejected",
            "contract_code": str(exc) if re.fullmatch(r"[a-z_]{1,100}", str(exc)) else None,
        }
    except Exception as exc:
        status = "queued" if attempts + 1 < 3 else "failed"
        error = {"code": "offline_dependency_failure", "type": type(exc).__name__}
    finally:
        stop.set()
        thread.join(timeout=2)
    with tenant_transaction(owner) as conn:
        current = conn.scalar(sa.select(T["runs"].c.lease_token).where(T["runs"].c.id == run_id).with_for_update())
        if current != token:
            return
        conn.execute(
            T["run_steps"]
            .update()
            .where(T["run_steps"].c.id == step)
            .values(
                status="succeeded" if status == "succeeded" else "failed",
                output_json=result,
                error_json=error,
                lease_until=None,
            )
        )
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == run_id, T["runs"].c.lease_token == token)
            .values(
                status=status,
                result_json=result,
                error_json=error,
                lease_token=None,
                lease_until=None,
                finished_at=sa.func.now() if status in {"succeeded", "failed", "cancelled"} else None,
            )
        )
        emit(
            conn,
            run_id,
            status,
            {
                "message": "任务完成；请按结果中的来源与模拟标记评估。"
                if status == "succeeded"
                else "离线任务暂停或等待有界恢复。",
                "error": error,
            },
        )
