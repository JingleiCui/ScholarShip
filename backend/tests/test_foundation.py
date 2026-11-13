from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import time
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from scholarship.domains.runs import create_probe
from scholarship.infrastructure.database import engine, tenant_transaction
from scholarship.infrastructure.schema import tables

pytestmark = pytest.mark.integration


def test_deployed_catalog_and_roles(database):
    with database.connect() as conn:
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='scholar' AND table_name<>'alembic_version'"
                )
            )
            == 41
        )
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema='scholar' AND table_name<>'alembic_version'"
                )
            )
            == 494
        )
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM information_schema.table_constraints WHERE constraint_schema='scholar' AND constraint_type='FOREIGN KEY'"
                )
            )
            == 147
        )
        assert conn.scalar(sa.text("SELECT count(*) FROM agent.checkpoint_migrations")) > 0
        roles = conn.execute(
            sa.text(
                "SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname IN ('scholar_runtime','scholar_auth','scholar_checkpoint')"
            )
        ).all()
        assert len(roles) == 3 and not any(any(r) for r in roles)


def test_rls_owner_and_pool_reset(accounts):
    a, b = accounts["researcher"], accounts["reviewer"]
    for account in [a, b, a, b]:
        with tenant_transaction(account["user"]["id"]) as conn:
            ids = conn.scalars(sa.select(tables["projects"].c.owner_id)).all()
            assert ids and set(ids) == {account["user"]["id"]}
    with engine().connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(tables["projects"])) == 0
    with tenant_transaction(a["user"]["id"]) as conn:
        with pytest.raises(DBAPIError):
            conn.execute(
                tables["projects"]
                .insert()
                .values(owner_id=b["user"]["id"], name="forbidden", description="", preferences={})
            )


def test_composite_fk_rejects_cross_owner(accounts):
    a, b = accounts["researcher"], accounts["reviewer"]
    with pytest.raises(DBAPIError), tenant_transaction(a["user"]["id"]) as conn:
        conn.execute(
            tables["papers"]
            .insert()
            .values(
                owner_id=a["user"]["id"], project_id=b["project"]["id"], title="foreign", normalized_title="foreign"
            )
        )


def carrier(conn, account):
    owner, project = account["user"]["id"], account["project"]["id"]
    file_id = conn.scalar(
        tables["file_objects"]
        .insert()
        .values(
            owner_id=owner,
            sha256=uuid4().hex * 2,
            byte_size=10,
            mime_type="application/pdf",
            storage_key="tests/" + uuid4().hex,
            state="ready",
        )
        .returning(tables["file_objects"].c.id)
    )
    paper = conn.scalar(
        tables["papers"]
        .insert()
        .values(owner_id=owner, project_id=project, title="Synthetic probe", normalized_title="synthetic probe")
        .returning(tables["papers"].c.id)
    )
    version = conn.scalar(
        tables["paper_versions"]
        .insert()
        .values(owner_id=owner, project_id=project, paper_id=paper, file_id=file_id, metadata_json={})
        .returning(tables["paper_versions"].c.id)
    )
    parse = conn.scalar(
        tables["document_parses"]
        .insert()
        .values(
            owner_id=owner,
            project_id=project,
            version_id=version,
            parser_name="synthetic",
            parser_version="1",
            config_sha256="a" * 64,
            revision_no=1,
            mode="preview",
            status="processing",
            expected_pages=1,
            completed_pages=0,
        )
        .returning(tables["document_parses"].c.id)
    )
    return version, parse


def test_version_pointer_and_publication_guards(accounts):
    a = accounts["researcher"]
    with tenant_transaction(a["user"]["id"]) as conn:
        version, parse = carrier(conn, a)
        other_version, other_parse = carrier(conn, a)
        profile = conn.scalar(sa.select(tables["index_profiles"].c.id).limit(1))
        other_index = conn.scalar(
            tables["index_generations"]
            .insert()
            .values(
                owner_id=a["user"]["id"],
                project_id=a["project"]["id"],
                version_id=other_version,
                parse_id=other_parse,
                profile_id=profile,
                generation_no=1,
                collection_name="ss_test_unused_" + uuid4().hex,
                status="building",
                expected_points=0,
                verified_points=0,
            )
            .returning(tables["index_generations"].c.id)
        )
        page = conn.scalar(
            tables["document_pages"]
            .insert()
            .values(
                owner_id=a["user"]["id"],
                project_id=a["project"]["id"],
                parse_id=parse,
                page_no=1,
                width=600,
                height=800,
                rotation=0,
                status="ready",
                text_layer_chars=10,
            )
            .returning(tables["document_pages"].c.id)
        )
        conn.execute(
            tables["document_parses"]
            .update()
            .where(tables["document_parses"].c.id == parse)
            .values(status="ready", completed_pages=1)
        )
    bad_statements = [
        tables["paper_versions"]
        .update()
        .where(tables["paper_versions"].c.id == version)
        .values(active_parse_id=other_parse),
        tables["paper_versions"]
        .update()
        .where(tables["paper_versions"].c.id == version)
        .values(metadata_json={"changed": True}),
        tables["document_pages"].update().where(tables["document_pages"].c.id == page).values(width=999),
        tables["document_pages"].update().where(tables["document_pages"].c.id == page).values(parse_id=other_parse),
        tables["document_parses"].update().where(tables["document_parses"].c.id == parse).values(status="processing"),
    ]
    for stmt in bad_statements:
        with pytest.raises(DBAPIError), tenant_transaction(a["user"]["id"]) as conn:
            conn.execute(stmt)
    with pytest.raises(DBAPIError) as caught, tenant_transaction(a["user"]["id"]) as conn:
        conn.execute(
            tables["paper_versions"]
            .update()
            .where(tables["paper_versions"].c.id == version)
            .values(active_parse_id=other_parse, active_index_id=other_index)
        )
    assert caught.value.orig.sqlstate == "23503"


def test_append_only_audit(accounts):
    a = accounts["researcher"]
    with tenant_transaction(a["user"]["id"]) as conn:
        item = conn.scalar(
            tables["audit_events"]
            .insert()
            .values(
                owner_id=a["user"]["id"],
                project_id=a["project"]["id"],
                action="synthetic_test",
                subject_json={},
                safe_payload={},
            )
            .returning(tables["audit_events"].c.id)
        )
    for stmt in [
        tables["audit_events"].update().where(tables["audit_events"].c.id == item).values(action="changed"),
        tables["audit_events"].delete().where(tables["audit_events"].c.id == item),
    ]:
        with pytest.raises(DBAPIError), tenant_transaction(a["user"]["id"]) as conn:
            conn.execute(stmt)


def test_cost_reference_cannot_use_another_accounts_budget(accounts, database):
    a, b = accounts["researcher"], accounts["reviewer"]
    with database.connect() as conn:
        other_budget = conn.scalar(
            sa.select(tables["budget_accounts"].c.id).where(tables["budget_accounts"].c.owner_id == b["user"]["id"])
        )
    with tenant_transaction(a["user"]["id"]) as conn:
        scope = {"owner_id": a["user"]["id"], "project_id": a["project"]["id"]}
        rid = conn.scalar(
            tables["runs"]
            .insert()
            .values(
                **scope,
                kind="synthetic_cost_constraint",
                status="queued",
                idempotency_key=uuid4().hex,
                request_sha256="a" * 64,
                input_json={},
                config_json={},
                input_scope_json={},
                next_event_seq=1,
                event_floor=1,
            )
            .returning(tables["runs"].c.id)
        )
        step = conn.scalar(
            tables["run_steps"]
            .insert()
            .values(
                **scope,
                run_id=rid,
                step_key="cost",
                attempt_no=1,
                tool_name="synthetic",
                status="pending",
                input_sha256="a" * 64,
            )
            .returning(tables["run_steps"].c.id)
        )
        call = conn.scalar(
            tables["external_calls"]
            .insert()
            .values(
                **scope,
                step_id=step,
                provider="synthetic_no_request",
                operation="test",
                intent_key=uuid4().hex,
                request_sha256="a" * 64,
                status="prepared",
            )
            .returning(tables["external_calls"].c.id)
        )
    with pytest.raises(DBAPIError) as caught, tenant_transaction(a["user"]["id"]) as conn:
        conn.execute(
            tables["budget_reservations"]
            .insert()
            .values(**scope, account_id=other_budget, call_id=call, reserved_amount=1, state="held")
        )
    assert caught.value.orig.sqlstate == "23503"


def test_artifact_requires_exactly_one_provenance_input(accounts):
    a = accounts["researcher"]
    with pytest.raises(DBAPIError) as caught, tenant_transaction(a["user"]["id"]) as conn:
        conn.execute(
            tables["artifact_inputs"]
            .insert()
            .values(
                owner_id=a["user"]["id"],
                project_id=a["project"]["id"],
                output_version_id=uuid4(),
                input_role="synthetic",
                ordinal=0,
            )
        )
    assert caught.value.orig.sqlstate == "23514"


def test_concurrent_budget_period_rejects_overlap(accounts):
    uid = accounts["researcher"]["user"]["id"]
    start = datetime.now(timezone.utc)

    def insert_period(offset):
        try:
            with tenant_transaction(uid) as conn:
                conn.execute(
                    tables["budget_accounts"]
                    .insert()
                    .values(
                        owner_id=uid,
                        period_start=start + timedelta(hours=offset),
                        period_end=start + timedelta(days=1, hours=offset),
                        category="concurrency_probe",
                        currency="CNY",
                        limit_amount=1,
                        spent_amount=0,
                        reserved_amount=0,
                    )
                )
            return True
        except DBAPIError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(insert_period, range(2))) == [False, True]


def test_auth_and_csrf(client, signed_client, accounts):
    assert signed_client.get("/api/v1/auth/me").json()["username"] == "researcher"
    assert signed_client.get("/api/v1/projects").json()[0]["id"] == str(accounts["researcher"]["project"]["id"])
    body = {"project_id": str(accounts["researcher"]["project"]["id"])}
    csrf = signed_client.headers.pop("X-CSRF-Token")
    assert signed_client.post("/api/v1/runs", json=body, headers={"Idempotency-Key": "no-csrf"}).status_code == 403
    signed_client.headers["X-CSRF-Token"] = csrf
    assert (
        signed_client.post(
            "/api/v1/runs",
            json={"project_id": str(accounts["reviewer"]["project"]["id"])},
            headers={"Idempotency-Key": "cross-owner"},
        ).status_code
        == 404
    )
    assert signed_client.post("/api/v1/auth/logout").status_code == 200
    assert signed_client.get("/api/v1/auth/me").status_code == 401


def test_public_health_and_configuration(client):
    assert client.get("/api/v1/health/live").status_code == 200
    assert client.get("/api/v1/health/ready").json()["ready"]
    meta = client.get("/api/v1/meta").json()
    assert not meta["external_services"]["enabled"]
    assert not any(v for k, v in meta["external_services"].items() if k.endswith("_configured"))
    assert meta["budget"]["limit_cny"] == 100
    assert client.get("/api/v1/projects").status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "bad", "password": "bad"}).status_code == 403


def wait_run(client, run_id, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = client.get(f"/api/v1/runs/{run_id}").json()
        if row["status"] in {"succeeded", "failed"}:
            assert row["status"] == "succeeded", row.get("error_json")
            return row
        time.sleep(0.25)
    pytest.fail("Real queue did not finish within timeout")


def test_real_queue_checkpoint_idempotency_and_sse(signed_client, accounts, background, database):
    client = signed_client
    body = {"project_id": str(accounts["researcher"]["project"]["id"])}
    headers = {"Idempotency-Key": uuid4().hex}
    first = client.post("/api/v1/runs", json=body, headers=headers)
    assert first.status_code == 202
    rid = first.json()["id"]
    assert client.post("/api/v1/runs", json=body, headers=headers).json()["id"] == rid
    wait_run(client, rid)
    text = client.get(f"/api/v1/runs/{rid}/events").text
    assert "event: queued" in text and "event: running" in text and "event: succeeded" in text
    assert [line for line in text.splitlines() if line.startswith("id:")] == ["id: 1", "id: 2", "id: 3"]
    resumed = client.get(f"/api/v1/runs/{rid}/events", headers={"Last-Event-ID": "2"}).text
    assert "id: 3" in resumed and "id: 1" not in resumed
    assert client.get(f"/api/v1/runs/{rid}/events?after=999").status_code == 400
    with database.connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM agent.checkpoints WHERE thread_id=:rid"), {"rid": rid}) > 0
        assert (
            conn.scalar(
                sa.select(sa.func.count()).select_from(tables["run_steps"]).where(tables["run_steps"].c.run_id == rid)
            )
            == 1
        )
    from scholarship.workers.tasks import execute_probe, graph_probe

    execute_probe(str(accounts["researcher"]["user"]["id"]), body["project_id"], rid)  # duplicate delivery is inert
    assert graph_probe(rid)["ready"]  # reuse persisted final state
    assert client.get(f"/api/v1/runs/{rid}/events").text == text
    with database.begin() as conn:
        conn.execute(
            tables["runs"].update().where(tables["runs"].c.id == rid).values(request_sha256="0" * 64, event_floor=2)
        )
    assert client.post("/api/v1/runs", json=body, headers=headers).status_code == 409
    assert client.get(f"/api/v1/runs/{rid}/events").status_code == 410


def test_expired_run_lease_recovers_via_outbox(signed_client, accounts, background, database):
    a = accounts["researcher"]
    row = create_probe(a["user"]["id"], a["project"]["id"], uuid4().hex)
    with database.begin() as conn:
        conn.execute(
            tables["runs"]
            .update()
            .where(tables["runs"].c.id == row["id"])
            .values(status="running", lease_token=uuid4(), lease_until=sa.func.now() - sa.text("interval '1 second'"))
        )
    wait_run(signed_client, row["id"])


def test_bm25_dense_rrf_and_scope():
    from qdrant_client import models as m
    from scholarship.infrastructure.health import qdrant_client

    client = qdrant_client()
    name = "ss_probe_" + uuid4().hex
    a, b = str(uuid4()), str(uuid4())
    try:
        client.create_collection(
            name,
            vectors_config={"dense": m.VectorParams(size=1024, distance=m.Distance.COSINE, on_disk=True)},
            on_disk_payload=True,
        )
        client.create_payload_index(
            name,
            "lexical_text",
            m.TextIndexParams(
                type=m.TextIndexType.TEXT,
                tokenizer=m.TokenizerType.WORD,
                min_token_len=1,
                max_token_len=64,
                lowercase=True,
                scoring=True,
            ),
            wait=True,
        )
        client.create_payload_index(name, "owner_id", m.PayloadSchemaType.KEYWORD, wait=True)
        client.upsert(
            name,
            points=[
                m.PointStruct(
                    id=i, vector={"dense": [1.0] + [0.0] * 1023}, payload={"owner_id": owner, "lexical_text": text}
                )
                for i, owner, text in [
                    (1, a, "scholarship evidence provenance"),
                    (2, a, "unrelated chemistry"),
                    (3, b, "scholarship evidence provenance"),
                ]
            ],
            wait=True,
        )
        scope = m.Filter(must=[m.FieldCondition(key="owner_id", match=m.MatchValue(value=a))])
        lexical = client.query_points(
            name, query=m.TextQuery(text="provenance"), using="lexical_text", query_filter=scope, limit=5
        ).points
        assert [p.id for p in lexical] == [1]
        dense = client.query_points(name, query=[1.0] + [0.0] * 1023, using="dense", query_filter=scope, limit=5).points
        assert {p.id for p in dense} == {1, 2}
        result = client.query_points(
            name,
            prefetch=[
                m.Prefetch(query=[1.0] + [0.0] * 1023, using="dense", filter=scope, limit=5),
                m.Prefetch(query=m.TextQuery(text="provenance"), using="lexical_text", filter=scope, limit=5),
            ],
            query=m.RrfQuery(rrf=m.Rrf(k=60, weights=[1.0, 1.0])),
            query_filter=scope,
            limit=5,
        ).points
        assert result[0].id == 1 and all(p.id != 3 for p in result)
    finally:
        if client.collection_exists(name):
            client.delete_collection(name)
        client.close()
