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


def test_recorded_preflight_stop_is_permanent_on_rerun(case, database, tmp_path, monkeypatch):
    import validate_live_models as cli

    f = tmp_path / "preflight.json"
    f.write_text(json.dumps({"provider": "chatanywhere", "http_status": 403, "code": 40300, "inference_requests": 0}))
    monkeypatch.setattr(cli, "PREFLIGHT", f)
    sel = cli.selection()
    manifest = make_manifest(sel, REQUESTS)
    cli.record_stopped_approval(database, sel, manifest)
    first = cli.find_approval(database)
    with database.begin() as conn:
        conn.execute(sa.text("UPDATE model_runtime.grants SET state='active' WHERE id=:id"), {"id": first["id"]})
    cli.record_stopped_approval(database, sel, manifest)
    second = cli.find_approval(database)
    assert first["id"] == second["id"] and second["state"] == "stopped" and second["used_requests"] == 0
    e = LiveExecutor(second["owner_id"], second["id"], sel)
    with pytest.raises(DBAPIError):
        e.execute(*REQUESTS[0], inner=httpx.MockTransport(fake_response))
