"""Bounded dispatch tested with fake credentials and injected MockTransport only."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from psycopg.types.json import Jsonb
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from scholarship.models.live import LiveExecutor, GrantTransport, make_manifest
from scholarship.models.configuration import load_selection, fingerprint
from scholarship.models.adapters import ProviderFailure
from scholarship.adapters.providers import ContractError
from scholarship.infrastructure.database import tenant_transaction
from scholarship.config import settings
from test_offline_business import case as case, enqueue
from validate_live_models import REQUESTS


@pytest.fixture(autouse=True)
def fake_keys_and_block_external(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "chatanywhere_api_key", SecretStr("test-chatanywhere-only"))
    monkeypatch.setattr(cfg, "openrouter_api_key", SecretStr("test-openrouter-only"))
    original = httpx.HTTPTransport.handle_request

    def guard(self, request):
        if request.url.host not in {"127.0.0.1", "localhost"}:
            raise AssertionError("external transport blocked in test")
        return original(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guard)


def granted(case, database, *, quota=5, state="active", expired=False):
    owner, project = case
    sel = replace(load_selection("free_api"), max_output_tokens=256, max_requests_per_run=5, timeout_seconds=30)
    manifest = make_manifest(sel, REQUESTS)
    run = enqueue(owner, project, "provider_validation", uuid4().hex, {})["id"]
    with database.begin() as conn:
        gid = conn.connection.driver_connection.execute(
            """INSERT INTO model_runtime.grants
          (approval_key,owner_id,project_id,run_id,manifest_json,manifest_sha256,max_requests,max_output_tokens,state,expires_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,256,%s,%s) RETURNING id""",
            (
                uuid4().hex,
                owner,
                project,
                run,
                Jsonb(manifest),
                fingerprint(manifest),
                quota,
                state,
                datetime.now(timezone.utc) + timedelta(hours=-1 if expired else 1),
            ),
        ).fetchone()[0]
    return sel, gid, LiveExecutor(owner, gid, sel)


def fake_response(request):
    b = json.loads(request.content)
    if request.url.host == "api.chatanywhere.tech":
        assert request.headers["Authorization"] == "Bearer test-chatanywhere-only"
        assert b["max_tokens"] == 256 and "thinking" not in b
        context = b["messages"][-1]["content"]
        if b.get("response_format"):
            e = json.loads(context)["evidence"][0]
            text = json.dumps(
                {
                    "answer": "91 percent",
                    "insufficient_evidence": False,
                    "claims": [
                        {
                            "text": "91 percent",
                            "citations": [
                                {"evidence_id": e["id"], "quote": e["text"], "start": 0, "end": len(e["text"])}
                            ],
                        }
                    ],
                }
            )
        else:
            text = "SCHOLARSHIP_OK"
        return httpx.Response(
            200,
            json={
                "id": "test-chat",
                "model": "gpt-4o-mini-2024-07-18",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                "choices": [{"finish_reason": "stop", "message": {"content": text}}],
            },
        )
    assert request.headers["Authorization"] == "Bearer test-openrouter-only"
    if request.url.path.endswith("/embeddings"):
        return httpx.Response(
            200,
            json={
                "model": b["model"].removesuffix(":free"),
                "data": [{"index": i, "embedding": [1.0, 0.0, 0.0]} for i, _ in enumerate(b["input"])],
                "usage": {"prompt_tokens": 3, "total_tokens": 3},
            },
        )
    return httpx.Response(
        200,
        json={
            "model": b["model"].removesuffix(":free"),
            "results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.1}],
        },
    )


def test_five_slots_only_and_receipt_replay(case, database):
    sel, gid, executor = granted(case, database)
    sends = []

    def inner(r):
        sends.append(r)
        return fake_response(r)

    receipts = []
    for intent, role, operation, args in REQUESTS:
        receipt = executor.execute(intent, role, operation, args, inner=httpx.MockTransport(inner))
        receipts.append(receipt)
        assert receipt["simulated"] is False
    assert len(sends) == 5 and receipts[1]["dimensions"] == receipts[2]["dimensions"] == 3
    assert receipts[-1]["value"]["verification"] == "exact_citation_spans_only"
    intent, role, operation, args = REQUESTS[0]
    assert executor.execute(intent, role, operation, args, inner=httpx.MockTransport(inner)) == receipts[0]
    assert len(sends) == 5
    with tenant_transaction(case[0]) as conn:
        assert conn.scalar(sa.text("SELECT used_requests FROM model_runtime.grants WHERE id=:id"), {"id": gid}) == 5
        conn.execute(sa.text("SELECT model_runtime.complete_grant(:id)"), {"id": gid})
        assert conn.scalar(sa.text("SELECT state FROM model_runtime.grants WHERE id=:id"), {"id": gid}) == "completed"


@pytest.mark.parametrize("state,expired", [("stopped", False), ("completed", False), ("active", True)])
def test_closed_or_expired_grant_never_sends(case, database, state, expired):
    _, _, e = granted(case, database, state=state, expired=expired)
    intent, role, op, args = REQUESTS[0]
    sent = []
    with pytest.raises(DBAPIError):
        e.execute(intent, role, op, args, inner=httpx.MockTransport(lambda r: sent.append(r)))
    assert not sent


def test_content_model_or_snapshot_changes_are_outside_grant(case, database):
    sel, gid, e = granted(case, database)
    with pytest.raises(DBAPIError):
        e.execute(
            "chat_plain",
            "chat",
            "generate",
            {"messages": [{"role": "user", "content": "unapproved"}]},
            inner=httpx.MockTransport(fake_response),
        )
    altered = replace(sel, max_output_tokens=255)
    other = LiveExecutor(case[0], gid, altered)
    with pytest.raises(DBAPIError):
        other.execute(*REQUESTS[0], inner=httpx.MockTransport(fake_response))
    with tenant_transaction(case[0]) as conn:
        assert conn.scalar(sa.text("SELECT used_requests FROM model_runtime.grants WHERE id=:id"), {"id": gid}) == 0


def test_wire_mutation_denied_after_preparation(case, database):
    sel, gid, e = granted(case, database)
    from scholarship.models.live import request_hash
    from sqlalchemy.dialects.postgresql import JSONB

    intent, role, op, args = REQUESTS[0]
    with tenant_transaction(case[0]) as conn:
        stmt = sa.text("SELECT model_runtime.prepare_live_call(:g,:i,:h,:s)").bindparams(sa.bindparam("s", type_=JSONB))
        cid = conn.scalar(stmt, {"g": gid, "i": intent, "h": request_hash(sel, role, op, args), "s": sel.snapshot()})
    transport = GrantTransport(case[0], cid, "chatanywhere", inner=httpx.MockTransport(fake_response))
    with pytest.raises(DBAPIError):
        transport.handle_request(
            httpx.Request("POST", "https://api.chatanywhere.tech/v1/chat/completions", json={"model": "paid-other"})
        )
    with tenant_transaction(case[0]) as conn:
        assert conn.scalar(sa.text("SELECT used_requests FROM model_runtime.grants WHERE id=:id"), {"id": gid}) == 0


@pytest.mark.parametrize("code", [401, 403, 429, 500, 302])
def test_first_error_stops_all_remaining_calls(case, database, code):
    _, gid, e = granted(case, database)
    calls = []

    def fail(r):
        calls.append(r)
        return httpx.Response(code, json={"error": "private provider details"}, headers={"Retry-After": "60"})

    with pytest.raises(ProviderFailure):
        e.execute(*REQUESTS[0], inner=httpx.MockTransport(fail))
    with pytest.raises(DBAPIError):
        e.execute(*REQUESTS[1], inner=httpx.MockTransport(fail))
    assert len(calls) == 1
    with tenant_transaction(case[0]) as conn:
        row = (
            conn.execute(sa.text("SELECT state,used_requests FROM model_runtime.grants WHERE id=:id"), {"id": gid})
            .mappings()
            .one()
        )
        assert row["state"] == "stopped" and row["used_requests"] == 1


def test_timeout_not_retried_and_unknown_remains(case, database):
    _, gid, e = granted(case, database)
    calls = []

    def fail(r):
        calls.append(r)
        raise httpx.ReadTimeout("secret request details")

    with pytest.raises(ProviderFailure):
        e.execute(*REQUESTS[0], inner=httpx.MockTransport(fail))
    with pytest.raises(ProviderFailure):
        e.execute(*REQUESTS[0], inner=httpx.MockTransport(fail))
    assert len(calls) == 1
    with tenant_transaction(case[0]) as conn:
        assert (
            conn.scalar(sa.text("SELECT state FROM model_runtime.calls WHERE grant_id=:id"), {"id": gid}) == "unknown"
        )


def test_unexpected_reported_model_stops(case, database):
    _, gid, e = granted(case, database)

    def bad(r):
        d = fake_response(r).json()
        d["model"] = "different-model"
        return httpx.Response(200, json=d)

    with pytest.raises(ContractError):
        e.execute(*REQUESTS[0], inner=httpx.MockTransport(bad))
    with tenant_transaction(case[0]) as conn:
        assert conn.scalar(sa.text("SELECT state FROM model_runtime.grants WHERE id=:id"), {"id": gid}) == "stopped"


def test_quota_cannot_be_extended_by_new_intent(case, database):
    _, gid, e = granted(case, database, quota=1)
    e.execute(*REQUESTS[0], inner=httpx.MockTransport(fake_response))
    with pytest.raises(DBAPIError):
        e.execute(*REQUESTS[1], inner=httpx.MockTransport(fake_response))
    with tenant_transaction(case[0]) as conn:
        assert conn.scalar(sa.text("SELECT used_requests FROM model_runtime.grants WHERE id=:id"), {"id": gid}) == 1


def test_grant_owner_rls_and_no_runtime_creation(case, database, accounts):
    _, gid, _ = granted(case, database)
    other = accounts["reviewer"]["user"]["id"]
    with tenant_transaction(other) as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM model_runtime.grants WHERE id=:id"), {"id": gid}) == 0
    with pytest.raises(DBAPIError), tenant_transaction(case[0]) as conn:
        conn.execute(
            sa.text("UPDATE model_runtime.grants SET state='active',used_requests=0 WHERE id=:id"), {"id": gid}
        )


def test_no_approval_cannot_even_open_maintenance_database():
    from scholarship.models.approvals import issue_approval
    from scholarship.adapters.providers import ExternalDisabled

    with pytest.raises(ExternalDisabled):
        issue_approval(None, key="unapproved", selection=None, requests=[], total_limit=6)
    with pytest.raises(ValueError):
        make_manifest(
            replace(load_selection("free_gemma"), max_output_tokens=256),
            [("bypass", "chat", "request", {"path": "/chat/completions", "body": {"model": "paid"}})],
        )


@pytest.mark.parametrize("last_status", [200, 429])
def test_cumulative_lineage_preserves_unknown_and_never_refills(case, database, last_status):
    from scholarship.models.approvals import issue_approval

    sel, gid, executor = granted(case, database)
    executor.execute(*REQUESTS[0], inner=httpx.MockTransport(fake_response))

    def fail(request):
        raise httpx.ReadTimeout("private content")

    with pytest.raises(ProviderFailure):
        executor.execute(*REQUESTS[1], inner=httpx.MockTransport(fail))
    with database.connect() as conn:
        key0 = conn.scalar(sa.text("SELECT approval_key FROM model_runtime.grants WHERE id=:id"), {"id": gid})
    keys = [key0]

    def issue(selection, requests):
        key = uuid4().hex
        grant = issue_approval(
            database, key=key, selection=selection, requests=requests, total_limit=6, prior_keys=keys, approved=True
        )
        keys.append(key)
        return grant

    g1 = issue(sel, REQUESTS[1:])
    with pytest.raises(ProviderFailure):
        LiveExecutor(case[0], g1["id"], sel).execute(*REQUESTS[1], inner=httpx.MockTransport(fail))
    g2 = issue(sel, REQUESTS[1:4])
    e2 = LiveExecutor(case[0], g2["id"], sel)
    e2.execute(*REQUESTS[1], inner=httpx.MockTransport(fake_response))
    with pytest.raises(ProviderFailure):
        e2.execute(*REQUESTS[2], inner=httpx.MockTransport(fail))
    gemma = replace(load_selection("free_gemma"), max_output_tokens=256, max_requests_per_run=1)
    g3 = issue(gemma, [REQUESTS[-1]])

    def answer(request):
        if last_status != 200:
            return httpx.Response(last_status, headers={"Retry-After": "60"})
        data = fake_response(
            httpx.Request(
                "POST",
                "https://api.chatanywhere.tech/v1/chat/completions",
                headers={"Authorization": "Bearer test-chatanywhere-only"},
                content=request.content,
            )
        ).json()
        data["model"] = "google/gemma-4-31b-it"
        return httpx.Response(200, json=data)

    e3 = LiveExecutor(case[0], g3["id"], gemma)
    if last_status == 200:
        e3.execute(*REQUESTS[-1], inner=httpx.MockTransport(answer))
        with tenant_transaction(case[0]) as conn:
            conn.execute(sa.text("SELECT model_runtime.complete_grant(:id)"), {"id": g3["id"]})
    else:
        with pytest.raises(ProviderFailure):
            e3.execute(*REQUESTS[-1], inner=httpx.MockTransport(answer))
    again = issue_approval(
        database,
        key=keys[-1],
        selection=gemma,
        requests=[REQUESTS[-1]],
        total_limit=6,
        prior_keys=keys[:-1],
        approved=True,
    )
    assert again["used_requests"] == 1 and again["state"] == ("completed" if last_status == 200 else "stopped")
    with database.connect() as conn:
        assert (
            conn.scalar(
                sa.text("SELECT sum(used_requests) FROM model_runtime.grants WHERE id IN (:a,:b,:c,:d)"),
                {"a": gid, "b": g1["id"], "c": g2["id"], "d": g3["id"]},
            )
            == 6
        )
        receipts = conn.scalars(
            sa.text("SELECT receipt_json FROM model_runtime.calls WHERE grant_id IN (:a,:b,:c,:d)"),
            {"a": gid, "b": g1["id"], "c": g2["id"], "d": g3["id"]},
        ).all()
        assert "private content" not in json.dumps(receipts)
        if last_status == 429:
            assert any(r.get("retry_after") == 60 for r in receipts)
    with pytest.raises(ValueError):
        issue_approval(
            database,
            key=uuid4().hex,
            selection=gemma,
            requests=[REQUESTS[-1]],
            total_limit=6,
            prior_keys=keys[:-1],
            approved=True,
        )
    with pytest.raises(ValueError):
        issue_approval(
            database,
            key=uuid4().hex,
            selection=gemma,
            requests=[REQUESTS[-1]],
            total_limit=5,
            prior_keys=keys[1:3],
            approved=True,
        )


def test_explicit_proxy_route_disables_environment_and_retries(monkeypatch):
    import scholarship.models.transports as transport

    captured = []
    monkeypatch.setattr(
        transport, "configuration", lambda: {"LIVE_PROXY_URLS": {"openrouter": "http://127.0.0.1:7897"}}
    )
    monkeypatch.setattr(transport.httpx, "HTTPTransport", lambda **kw: captured.append(kw) or object())
    transport.network_transport("openrouter")
    transport.network_transport("chatanywhere")
    assert captured == [
        {"retries": 0, "trust_env": False, "proxy": "http://127.0.0.1:7897"},
        {"retries": 0, "trust_env": False, "proxy": None},
    ]
    for proxy in ("http://secret@127.0.0.1:7897", "https://remote.example:7897", "http://127.0.0.1:7897/?secret=x"):
        monkeypatch.setattr(transport, "configuration", lambda: {"LIVE_PROXY_URLS": {"openrouter": proxy}})
        with pytest.raises(ValueError, match="invalid_local_proxy"):
            transport.network_transport("openrouter")


def test_stopped_cli_run_is_read_only(case, database, tmp_path, monkeypatch):
    import validate_live_models as cli
    import scholarship.infrastructure.initialize as initialization

    _, gid, executor = granted(case, database, state="stopped")
    ref = tmp_path / "reference.json"
    ref.write_text(json.dumps({"owner_id": str(case[0]), "grant_id": str(gid)}))
    monkeypatch.setattr(cli, "REFERENCE", ref)
    monkeypatch.setattr(cli, "REPORT", tmp_path / "report.json")
    monkeypatch.setattr("sys.argv", ["validate_live_models.py", "--action", "run"])
    monkeypatch.setattr(initialization, "admin_engine", lambda: pytest.fail("runtime must not open admin connection"))
    cli.main()
    cli.main()
    saved = json.loads(cli.REPORT.read_text())
    assert saved["state"] == "stopped" and saved["used_request_slots"] == 0
