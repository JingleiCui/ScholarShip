from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from scholarship.adapters.providers import Embeddings, UnknownOutcome
from scholarship.domains.billing import Budget, OfflineExecutor
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T


@pytest.fixture
def billing_case(database):
    users = []
    with database.begin() as c:
        c.exec_driver_sql("UPDATE billing.pools SET active=false WHERE active")
        pool = c.scalar(sa.text("INSERT INTO billing.pools(limit_amount) VALUES(1) RETURNING id"))
        for _ in range(2):
            uid = c.scalar(
                T["users"]
                .insert()
                .values(
                    username="bill_" + uuid4().hex,
                    password_hash="unusable-test-hash",
                    display_name="Synthetic billing actor",
                    role="researcher",
                )
                .returning(T["users"].c.id)
            )
            project = c.scalar(
                T["projects"]
                .insert()
                .values(owner_id=uid, name="billing test", description="", preferences={})
                .returning(T["projects"].c.id)
            )
            now = datetime.now(timezone.utc)
            c.execute(
                T["budget_accounts"]
                .insert()
                .values(
                    owner_id=uid,
                    period_start=now,
                    period_end=now + timedelta(days=1),
                    category="ai",
                    currency="CNY",
                    limit_amount=10,
                    spent_amount=0,
                    reserved_amount=0,
                )
            )
            run = c.scalar(
                T["runs"]
                .insert()
                .values(
                    owner_id=uid,
                    project_id=project,
                    kind="offline_contract",
                    status="running",
                    idempotency_key=uuid4().hex,
                    request_sha256="a" * 64,
                    input_json={},
                    config_json={},
                    input_scope_json={},
                    next_event_seq=1,
                    event_floor=1,
                )
                .returning(T["runs"].c.id)
            )
            step = c.scalar(
                T["run_steps"]
                .insert()
                .values(
                    owner_id=uid,
                    project_id=project,
                    run_id=run,
                    step_key="offline",
                    attempt_no=1,
                    tool_name="offline_contract",
                    status="running",
                    input_sha256="a" * 64,
                )
                .returning(T["run_steps"].c.id)
            )
            users.append((uid, step))
    return pool, users


def reserve(actor, amount=".7", intent=None, request=None, offline=True):
    uid, step = actor
    return Budget(uid).reserve(
        step,
        intent or uuid4().hex,
        "dashscope",
        "embedding",
        request or {"input": "synthetic"},
        Decimal(amount),
        {"currency": "CNY", "offline": True},
        offline=offline,
    )


def test_concurrent_global_cap_across_two_researchers(billing_case, database):
    pool, actors = billing_case

    def attempt(actor):
        try:
            return reserve(actor).call_id
        except DBAPIError:
            return None

    with ThreadPoolExecutor(max_workers=2) as threads:
        result = list(threads.map(attempt, actors))
    assert sum(x is not None for x in result) == 1
    with database.connect() as c:
        assert c.scalar(sa.text("SELECT reserved_amount FROM billing.pools WHERE id=:p"), {"p": pool}) == Decimal(".7")


def test_intent_conflict_and_unknown_retains_reservation(billing_case, database):
    pool, actors = billing_case
    a = actors[0]
    b = Budget(a[0])
    key = uuid4().hex
    hold = reserve(a, intent=key)
    assert reserve(a, intent=key).call_id == hold.call_id
    with pytest.raises(DBAPIError):
        reserve(a, intent=key, request={"changed": True})
    assert b.sent(hold.call_id)
    b.unknown(hold.call_id)
    b.unknown(hold.call_id)
    assert not b.sent(hold.call_id)
    assert reserve(a, intent=key).state == "unknown"
    with pytest.raises(DBAPIError):
        b.release(hold.call_id)
    with database.connect() as c:
        assert c.scalar(sa.text("SELECT reserved_amount FROM billing.pools WHERE id=:p"), {"p": pool}) == Decimal(".7")


def test_settlement_once_and_charge_overrun_blocks_new_calls(billing_case, database):
    pool, actors = billing_case
    a = actors[0]
    b = Budget(a[0])
    hold = reserve(a, amount=".2")
    assert b.sent(hold.call_id)
    receipt = {"usage": {"input_tokens": 10}, "provider_request_id": "offline"}
    b.settle(hold.call_id, "1.2", receipt)
    b.settle(hold.call_id, "1.2", receipt)
    with pytest.raises(DBAPIError):
        b.settle(hold.call_id, "1.3", receipt)
    with pytest.raises(DBAPIError):
        reserve(actors[1], amount=".01")
    with database.connect() as c:
        row = c.execute(
            sa.text("SELECT spent_amount,reserved_amount,blocked FROM billing.pools WHERE id=:p"), {"p": pool}
        ).one()
        assert row == (Decimal("1.2"), Decimal(0), True)
        assert (
            c.scalar(
                sa.select(sa.func.count())
                .select_from(T["usage_ledger"])
                .where(T["usage_ledger"].c.call_id == hold.call_id)
            )
            == 1
        )


def test_actor_boundaries_and_direct_write_rejected(billing_case, database):
    pool, actors = billing_case
    a, b = actors
    hold = reserve(a, amount=".1")
    with tenant_transaction(b[0]) as c:
        assert (
            c.scalar(sa.text("SELECT count(*) FROM billing.allocations WHERE call_id=:cid"), {"cid": hold.call_id}) == 0
        )
    with pytest.raises(DBAPIError):
        Budget(b[0]).sent(hold.call_id)
    with pytest.raises(DBAPIError):
        Budget(a[0]).reserve(b[1], uuid4().hex, "dashscope", "embedding", {}, ".1", {"currency": "CNY"})
    with pytest.raises(DBAPIError):
        reserve(a, offline=False)
    with pytest.raises(DBAPIError), tenant_transaction(a[0]) as c:
        c.exec_driver_sql("UPDATE billing.pools SET limit_amount=100000")
    with pytest.raises(DBAPIError), tenant_transaction(a[0]) as c:
        c.exec_driver_sql("UPDATE scholar.external_calls SET status='succeeded'")
    with database.begin() as c:
        c.execute(T["users"].update().where(T["users"].c.id == b[0]).values(role="reviewer"))
    with pytest.raises(DBAPIError):
        reserve(b, amount=".1")


def test_unsent_release_and_expired_dispatch(billing_case, database):
    pool, actors = billing_case
    a = actors[0]
    b = Budget(a[0])
    hold = reserve(a, amount=".1")
    b.release(hold.call_id)
    b.release(hold.call_id)
    with database.connect() as c:
        assert c.scalar(sa.text("SELECT reserved_amount FROM billing.pools WHERE id=:p"), {"p": pool}) == 0
    another = reserve(a, amount=".1")
    with database.begin() as c:
        c.execute(
            sa.text(
                "UPDATE billing.pools SET period_start=now()-interval '2 days',period_end=now()-interval '1 day' WHERE id=:p"
            ),
            {"p": pool},
        )
    with pytest.raises(DBAPIError):
        b.sent(another.call_id)


def test_offline_executor_receipt_replay_and_timeout(billing_case):
    _, actors = billing_case
    a = actors[0]
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.1] * 1024}],
                "model": "text-embedding-v4",
                "id": "offline-1",
                "usage": {"total_tokens": 10},
            },
        )

    adapter = Embeddings(httpx.MockTransport(handler))
    runner = OfflineExecutor(a[0])
    key = uuid4().hex
    try:
        first = runner.execute(a[1], key, adapter, "embedding", {"texts": ["alpha"]}, 100)
        assert runner.execute(a[1], key, adapter, "embedding", {"texts": ["alpha"]}, 100) == first
        assert len(calls) == 1 and first["offline"]
    finally:
        adapter.close()
    attempts = []

    def timeout(req):
        attempts.append(req)
        raise httpx.ReadTimeout("synthetic")

    adapter = Embeddings(httpx.MockTransport(timeout))
    key = uuid4().hex
    try:
        for _ in range(2):
            with pytest.raises(UnknownOutcome):
                runner.execute(a[1], key, adapter, "embedding", {"texts": ["beta"]}, 100)
        assert len(attempts) == 1
    finally:
        adapter.close()


def test_explicit_cny_and_micro_rounding(billing_case):
    _, actors = billing_case
    a = actors[0]
    budget = Budget(a[0])
    key = uuid4().hex
    first = reserve(a, amount=".0000011", intent=key)
    assert reserve(a, amount=".0000011", intent=key).call_id == first.call_id
    with pytest.raises(DBAPIError):
        budget.reserve(a[1], uuid4().hex, "dashscope", "embedding", {}, ".1", {"currency": "USD"})


def test_settlement_ack_loss_does_not_duplicate(billing_case, monkeypatch):
    _, actors = billing_case
    a = actors[0]
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-v4",
                "data": [{"index": 0, "embedding": [0.1] * 1024}],
                "usage": {"total_tokens": 10},
            },
        )

    adapter = Embeddings(httpx.MockTransport(handler))
    runner = OfflineExecutor(a[0])
    original = runner.budget.settle

    def lost_ack(*args):
        original(*args)
        raise RuntimeError("synthetic receipt ack loss")

    monkeypatch.setattr(runner.budget, "settle", lost_ack)
    try:
        receipt = runner.execute(a[1], uuid4().hex, adapter, "embedding", {"texts": ["alpha"]}, 100)
        assert receipt["usage"]["input_tokens"] == 10 and len(calls) == 1
    finally:
        adapter.close()


def test_readonly_status_and_no_live_route(signed_client):
    r = signed_client.get("/api/v1/system/external-services")
    assert r.status_code == 200
    data = r.json()
    assert (
        data["mode"] == "offline_contracts_only"
        and not data["live_transport_available"]
        and not data["research_qa_available"]
    )
    assert "Authorization" not in r.text and "password" not in r.text
    assert signed_client.post("/api/v1/ask", json={"question": "q"}).status_code == 404


def test_chat_reservation_includes_actual_wire_output_limit(billing_case):
    from scholarship.adapters.providers import Answerer, Evidence

    _, actors = billing_case
    a = actors[0]
    content = {
        "answer": "alpha",
        "insufficient_evidence": False,
        "claims": [{"text": "alpha", "citations": [{"evidence_id": "e", "quote": "alpha", "start": 0, "end": 5}]}],
    }

    def handler(req):
        import json

        assert json.loads(req.content)["max_tokens"] == 2048
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 20},
            },
        )

    adapter = Answerer(httpx.MockTransport(handler))
    key = uuid4().hex
    try:
        OfflineExecutor(a[0]).execute(
            a[1], key, adapter, "chat", {"question": "q", "evidence": [Evidence("e", "alpha", 1)]}, 100
        )
        with tenant_transaction(a[0]) as c:
            amount = c.scalar(
                sa.text(
                    "SELECT a.amount FROM billing.allocations a JOIN scholar.external_calls c ON c.id=a.call_id WHERE c.intent_key=:k"
                ),
                {"k": key},
            )
            assert amount == Decimal(".016584")
    finally:
        adapter.close()
