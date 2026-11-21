"""Native OCR publication and real-source business flows; external HTTP forbidden."""

from dataclasses import replace
from io import BytesIO
import json
from uuid import uuid4, UUID
import zipfile
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from pypdf import PdfWriter
from pypdf.generic import RectangleObject
from scholarship.adapters.providers import ContractError, ExternalDisabled
from scholarship.domains.mineru_native import normalize, geometry, map_box, import_native
from scholarship.domains.research import enqueue_research, execute_research
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.models.configuration import load_selection
from scholarship.models.runtime import register_embedding_profile
from scholarship.models.contracts import ModelResult
from scholarship.workers.offline import execute_offline
from scholarship.domains.retrieval_profile import digest
from test_offline_business import case as case


def source(rotation=0, crop=None):
    w = PdfWriter()
    p = w.add_blank_page(width=600, height=800)
    if crop:
        p.cropbox = RectangleObject(crop)
    p.rotate(rotation)
    buf = BytesIO()
    w.write(buf)
    return buf.getvalue()


def bundle(content=None, size=(600, 800), version="3.4.4"):
    rows = (
        content
        if content is not None
        else [
            {"type": "text", "text": "Training", "text_level": 1, "page_idx": 0, "bbox": [100, 100, 800, 150]},
            {"type": "text", "text": "We trained on 8 NVIDIA P100 GPUs.", "page_idx": 0, "bbox": [100, 200, 800, 300]},
            {"type": "ref_text", "text": "[1] Example reference.", "page_idx": 0, "bbox": [100, 400, 800, 500]},
        ]
    )
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(
            "layout.json", json.dumps({"_version_name": version, "pdf_info": [{"page_idx": 0, "page_size": size}]})
        )
        z.writestr("paper_content_list.json", json.dumps(rows))
    return out.getvalue()


def imported(case, client):
    owner, project = case
    raw = source()
    response = client.post(
        f"/api/v1/projects/{project}/uploads",
        files={"file": ("live-business.pdf", raw, "application/pdf")},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 202, response.text
    vid = UUID(response.json()["version_id"])
    pid = import_native(owner, vid, bundle(), expected_source_sha256=digest(raw))
    return vid, pid


@pytest.mark.parametrize(
    "r,expected",
    [(0, [80, 600, 200, 720]), (90, [110, 120, 200, 280]), (180, [440, 160, 560, 280]), (270, [440, 600, 530, 760])],
)
def test_rotated_crop_mapping(r, expected):
    crop = [20, 40, 620, 840]
    size = (800, 600) if r in (90, 270) else (600, 800)
    g = geometry(crop, r, size)
    _, box = map_box([100, 150, 300, 300], g)
    # Independent expected PDF positions, verified against PDF.js rotation convention.
    assert box == expected
    pages, _ = normalize(bundle(size=size), source(r, crop))
    assert pages[0]["rotation"] == r


@pytest.mark.parametrize("mutation", ["version", "bounds", "page", "traversal", "dimensions", "unknown_type"])
def test_native_fail_closed(mutation):
    rows = [{"type": "text", "text": "Example", "page_idx": 0, "bbox": [10, 20, 30, 40]}]
    if mutation == "bounds":
        rows[0]["bbox"][2] = 1001
    if mutation == "page":
        rows[0]["page_idx"] = 1
    if mutation == "unknown_type":
        rows[0]["type"] = "executable"
    raw = bundle(
        rows,
        size=(100, 100) if mutation == "dimensions" else (600, 800),
        version="new" if mutation == "version" else "3.4.4",
    )
    if mutation == "traversal":
        b = BytesIO(raw)
        with zipfile.ZipFile(b, "a") as z:
            z.writestr("../private", "x")
        raw = b.getvalue()
    with pytest.raises(ContractError):
        normalize(raw, source())


def test_native_publication_lineage_and_source_hash(case, signed_client):
    owner, project = case
    vid, pid = imported(case, signed_client)
    assert import_native(owner, vid, bundle(), expected_source_sha256=digest(source())) == pid
    with pytest.raises(ContractError):
        import_native(owner, vid, bundle(), expected_source_sha256="0" * 64)
    with tenant_transaction(owner) as conn:
        blocks = (
            conn.execute(
                sa.select(T["document_blocks"])
                .where(T["document_blocks"].c.parse_id == pid)
                .order_by(T["document_blocks"].c.ordinal)
            )
            .mappings()
            .all()
        )
        assert blocks[1]["parent_block_id"] == blocks[0]["id"]
        assert not blocks[1]["source_locator"]["fixture"]
        assert blocks[1]["source_locator"]["pdf_bbox"] == [60, 560, 480, 640]
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(T["bibliographic_refs"])
                .where(T["bibliographic_refs"].c.parse_id == pid)
            )
            == 1
        )
        assert (
            conn.scalar(sa.select(T["paper_versions"].c.active_index_id).where(T["paper_versions"].c.id == vid)) is None
        )
    with pytest.raises(DBAPIError), tenant_transaction(owner) as conn:
        conn.execute(
            T["document_blocks"].update().where(T["document_blocks"].c.id == blocks[1]["id"]).values(raw_text="changed")
        )
    with tenant_transaction(uuid4()) as conn:
        assert conn.scalar(sa.select(T["document_blocks"].c.id).where(T["document_blocks"].c.parse_id == pid)) is None
    assert signed_client.get(f"/api/v1/research/parses/{pid}/outline").json()[0]["canonical_text"] == "Training"


def test_real_source_qa_checkpoint_worker_and_evidence(case, signed_client):
    owner, project = case
    vid, pid = imported(case, signed_client)
    run = enqueue_research(owner, project, uuid4().hex, "research_build", [vid])
    execute_offline(str(owner), str(project), str(run["id"]))
    with tenant_transaction(owner) as conn:
        r = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == run["id"])).mappings().one()
        assert r["status"] == "succeeded", r["error_json"]
        assert r["result_json"]["source_synthetic"] is False and r["result_json"]["model_simulated"] is True
    key = uuid4().hex
    qa = enqueue_research(owner, project, key, "research_qa", [vid], question="How many NVIDIA P100 GPUs?")
    assert (
        enqueue_research(owner, project, key, "research_qa", [vid], question="How many NVIDIA P100 GPUs?")["id"]
        == qa["id"]
    )
    assert execute_research(owner, project, qa["id"], interrupt_after=["retrieve"]) is None
    execute_offline(str(owner), str(project), str(qa["id"]))
    with tenant_transaction(owner) as conn:
        r = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == qa["id"])).mappings().one()
        assert r["status"] == "succeeded", r["error_json"]
        result = r["result_json"]
        assert not result["source_synthetic"] and result["model_simulated"]
        assert result["external_requests"] == 0 and result["citations"]
        assert (
            conn.scalar(
                sa.text("SELECT count(*) FROM model_runtime.calls WHERE run_id=:id AND simulated=false"),
                {"id": qa["id"]},
            )
            == 0
        )
    e = signed_client.get("/api/v1/research/evidence/" + result["citations"][0]["id"])
    assert e.status_code == 200
    e = e.json()
    assert e["original_pdf_available"] and e["pdf_bbox"] and e["parse_id"] == str(pid)
    assert e["text"][e["start"] : e["end"]] == e["quote"] and not e["synthetic"]
    assert execute_research(owner, project, qa["id"]) == result
    assert signed_client.get(f"/api/v1/research/projects/{project}/runs").status_code == 200
    denied = signed_client.post(
        f"/api/v1/research/projects/{project}/questions",
        json={"version_ids": [str(vid)], "mode": "cloud", "question": "test"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert denied.status_code == 409


def test_real_profile_requires_consumed_real_receipt(case, database):
    owner, project = case
    from scholarship.domains.offline_workflow import enqueue

    run = enqueue(owner, project, "offline_build", uuid4().hex, {}, dispatch=False)["id"]
    selection = load_selection("free_gemma")
    result = ModelResult(
        [[1.0] * 4096],
        "openrouter",
        selection.roles["embedding"].model,
        None,
        {"total_tokens": 1},
        None,
        1,
        4096,
        False,
    )
    with pytest.raises(ContractError):
        register_embedding_profile(owner, selection.roles["embedding"], result)
    with database.begin() as conn:
        cid = conn.scalar(
            sa.text(
                "INSERT INTO model_runtime.calls(owner_id,project_id,run_id,intent,request_sha256,config_json,role,provider,model,simulated,state,receipt_json,network_slot_consumed) VALUES(:o,:p,:r,'proof',:h,CAST(:c AS jsonb),'embedding','openrouter',:m,false,'succeeded',CAST(:j AS jsonb),true) RETURNING id"
            ),
            {
                "o": owner,
                "p": project,
                "r": run,
                "h": "a" * 64,
                "c": json.dumps(selection.snapshot()),
                "m": result.requested_model,
                "j": json.dumps(__import__("dataclasses").asdict(result)),
            },
        )
    profile = register_embedding_profile(owner, selection.roles["embedding"], result, call_id=cid)
    assert register_embedding_profile(owner, selection.roles["embedding"], result, call_id=cid) == profile
    with pytest.raises(DBAPIError):
        register_embedding_profile(uuid4(), selection.roles["embedding"], result, call_id=cid)
    with pytest.raises(DBAPIError):
        register_embedding_profile(owner, selection.roles["embedding"], replace(result, dimensions=1024), call_id=cid)
    with tenant_transaction(owner) as conn:
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM model_runtime.real_profile_proofs WHERE profile_id=:p AND project_id=:project"
                ),
                {"p": profile, "project": project},
            )
            == 1
        )


def production_case(case, database, monkeypatch, *, limit=3, preset="free_gemma"):
    from scholarship.models.production import ProductionGateway
    from scholarship.domains.offline_workflow import enqueue
    from scholarship.models.configuration import load_selection
    from dataclasses import replace
    from types import SimpleNamespace

    owner, project = case
    selection = replace(load_selection(preset), allow_cloud=True, allow_local=True)
    run = enqueue(owner, project, "research_qa", uuid4().hex, {}, dispatch=False)["id"]
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == run)
            .values(config_json={"mode": "cloud", "models": selection.snapshot()})
        )
    with database.begin() as conn:
        conn.execute(sa.text("UPDATE billing.pools SET live_enabled=true WHERE active"))
        spec = selection.roles["embedding"]
        policy = conn.scalar(
            sa.text(
                "INSERT INTO model_runtime.production_policies(owner_id,project_id,provider,model,role,enabled,free_verified,ends_at,request_limit,token_limit,approval_reference) VALUES(:o,:p,:v,:m,'embedding',true,true,now()+interval '1 hour',:n,100000,'ISOLATED TEST ONLY') RETURNING id"
            ),
            {"o": owner, "p": project, "v": spec.provider, "m": spec.model, "n": limit},
        )
    monkeypatch.setattr("scholarship.models.routing.settings", lambda: SimpleNamespace(external_services_enabled=True))
    from scholarship.models.catalog import configuration

    test_config = {**configuration(), "ALLOW_REAL_REQUESTS": True, "ALLOW_LOCAL_INFERENCE": True}
    monkeypatch.setattr("scholarship.models.routing.configuration", lambda: test_config)
    monkeypatch.setattr(
        "scholarship.models.production.credential", lambda p: "" if p == "ollama" else "isolated-test-key"
    )
    return owner, project, run, selection, policy, ProductionGateway


def test_production_free_usage_cache_and_budget(case, database, monkeypatch):
    import httpx

    owner, project, run, selection, policy, Gateway = production_case(case, database, monkeypatch)
    sent = []

    def reply(request):
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "model": selection.roles["embedding"].model,
                "data": [{"index": 0, "embedding": [1.0, 0.0]}],
                "usage": {"total_tokens": 4},
            },
        )

    with Gateway(owner, project, run, selection, transport_factory=lambda p: httpx.MockTransport(reply)) as gateway:
        result = gateway.invoke("embedding", "embedding", "embed", {"texts": ["test"]})
        assert not result["simulated"]
        assert gateway.invoke("embedding", "embedding", "embed", {"texts": ["test"]}) == result
    assert len(sent) == 1
    with tenant_transaction(owner) as conn:
        p = (
            conn.execute(sa.text("SELECT * FROM model_runtime.production_policies WHERE id=:p"), {"p": policy})
            .mappings()
            .one()
        )
        assert p["requests_used"] == 1 and p["tokens_used"] == 4 and p["tokens_reserved"] == 0
        assert (
            conn.scalar(
                sa.text("SELECT count(*) FROM model_runtime.production_admissions WHERE settled AND policy_id=:p"),
                {"p": policy},
            )
            == 1
        )
        assert (
            conn.scalar(
                sa.text("SELECT count(*) FROM billing.allocations WHERE NOT offline AND actual=0 AND state='settled'")
            )
            >= 1
        )


@pytest.mark.parametrize("failure", ["timeout", "missing_usage", "rate_limit"])
def test_production_uncertain_never_resends_or_refunds(case, database, monkeypatch, failure):
    import httpx
    from scholarship.models.contracts import ProviderFailure

    owner, project, run, selection, policy, Gateway = production_case(case, database, monkeypatch, limit=1)
    sent = []

    def reply(request):
        sent.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("secret")
        if failure == "rate_limit":
            return httpx.Response(429)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    with Gateway(owner, project, run, selection, transport_factory=lambda p: httpx.MockTransport(reply)) as gateway:
        with pytest.raises(ProviderFailure):
            gateway.invoke("one", "embedding", "embed", {"texts": ["test"]})
        with pytest.raises((ProviderFailure, ExternalDisabled)):
            gateway.invoke("one", "embedding", "embed", {"texts": ["test"]})
        with pytest.raises(ExternalDisabled):
            gateway.invoke("two", "embedding", "embed", {"texts": ["test"]})
    assert len(sent) == 1
    with tenant_transaction(owner) as conn:
        p = (
            conn.execute(sa.text("SELECT * FROM model_runtime.production_policies WHERE id=:p"), {"p": policy})
            .mappings()
            .one()
        )
        assert p["requests_used"] == 1 and p["tokens_reserved"] > 0
        assert (
            conn.scalar(
                sa.text("SELECT count(*) FROM model_runtime.production_admissions WHERE policy_id=:p AND NOT settled"),
                {"p": policy},
            )
            == 1
        )


def test_production_admission_shared_gate_and_role_scope(case, database, monkeypatch):
    import httpx

    owner, project, run, selection, policy, Gateway = production_case(case, database, monkeypatch)
    sent = []
    with database.begin() as conn:
        conn.execute(sa.text("UPDATE billing.pools SET live_enabled=false WHERE active"))
    with Gateway(
        owner, project, run, selection, transport_factory=lambda p: httpx.MockTransport(lambda r: sent.append(r))
    ) as gateway:
        with pytest.raises(ExternalDisabled):
            gateway.invoke("one", "embedding", "embed", {"texts": ["test"]})
        with pytest.raises(ExternalDisabled):
            gateway.invoke("answer", "chat", "generate", {"messages": [{"role": "user", "content": "test"}]})
    assert not sent
    with tenant_transaction(owner) as conn:
        assert (
            conn.scalar(
                sa.text("SELECT requests_used FROM model_runtime.production_policies WHERE id=:p"), {"p": policy}
            )
            == 0
        )


def test_541_page_part_assembly_global_locators_and_storage(case, signed_client):
    from scholarship.domains.mineru_native import combine_parts

    owner, project = case
    w = PdfWriter()
    for _ in range(541):
        w.add_blank_page(width=600, height=800)
    output = BytesIO()
    w.write(output)
    pdf = output.getvalue()
    parts = []
    for start, end in [(1, 200), (201, 400), (401, 541)]:
        zbuf = BytesIO()
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                "layout.json",
                json.dumps(
                    {
                        "_version_name": "3.4.4",
                        "pdf_info": [{"page_idx": i, "page_size": [600, 800]} for i in range(end - start + 1)],
                    }
                ),
            )
            z.writestr(
                "part_content_list.json",
                json.dumps(
                    [
                        {
                            "page_idx": i,
                            "bbox": [100, 100, 900, 200],
                            "type": "text",
                            "text": f"Fixture global page {start + i}",
                        }
                        for i in range(end - start + 1)
                    ]
                ),
            )
        parts.append((start, end, zbuf.getvalue()))
    with pytest.raises(ContractError):
        combine_parts(parts[1:], pdf)
    combined = combine_parts(parts, pdf)
    pages, _ = normalize(combined, pdf)
    assert len(pages) == 541 and pages[200]["blocks"][0]["text"] == "Fixture global page 201"
    response = signed_client.post(
        f"/api/v1/projects/{project}/uploads",
        files={"file": ("541-fixture.pdf", pdf, "application/pdf")},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 202, response.text
    vid = UUID(response.json()["version_id"])
    pid = import_native(owner, vid, combined, expected_source_sha256=digest(pdf))
    page = signed_client.get(f"/api/v1/parses/{pid}/pages/541").json()
    assert page["blocks"][0]["source_locator"]["page_idx"] == 540
    assert "541" in page["blocks"][0]["canonical_text"]


def test_business_api_outbox_executes_on_project_worker(case, signed_client, background):
    import time

    owner, project = case
    vid, _ = imported(case, signed_client)

    def submit(path, body):
        response = signed_client.post(
            f"/api/v1/research/projects/{project}/{path}", json=body, headers={"Idempotency-Key": uuid4().hex}
        )
        assert response.status_code == 202, response.text
        run = response.json()["id"]
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            with tenant_transaction(owner) as conn:
                r = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == UUID(run))).mappings().one()
            if r["status"] in {"succeeded", "failed", "waiting_user"}:
                assert r["status"] == "succeeded", r["error_json"]
                return r
            time.sleep(0.2)
        raise AssertionError("research_outbox_not_executed")

    submit("indexes", {"version_ids": [str(vid)]})
    result = submit("questions", {"version_ids": [str(vid)], "question": "NVIDIA P100 GPUs?"})
    assert result["result_json"]["citations"]
    assert result["result_json"]["external_requests"] == 0


@pytest.mark.parametrize("preset", ["mock", "local", "free_api"])
def test_project_preset_is_used_and_task_snapshot_is_pinned(case, signed_client, preset):
    owner, project = case
    vid, _ = imported(case, signed_client)
    before = enqueue_research(owner, project, uuid4().hex, "research_build", [vid])
    with tenant_transaction(owner) as conn:
        original = conn.scalar(sa.select(T["runs"].c.config_json).where(T["runs"].c.id == before["id"]))
        conn.execute(
            T["projects"]
            .update()
            .where(T["projects"].c.id == project)
            .values(preferences={"model_configuration": {"preset": preset, "overrides": {}}})
        )
    build = enqueue_research(owner, project, uuid4().hex, "research_build", [vid])
    execute_offline(str(owner), str(project), str(build["id"]))
    qa = enqueue_research(owner, project, uuid4().hex, "research_qa", [vid], question="NVIDIA P100?")
    execute_offline(str(owner), str(project), str(qa["id"]))
    with tenant_transaction(owner) as conn:
        saved = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == qa["id"])).mappings().one()
        assert saved["status"] == "succeeded", saved["error_json"]
        assert saved["config_json"]["models"]["preset"] == preset
        assert conn.scalar(sa.select(T["runs"].c.config_json).where(T["runs"].c.id == before["id"])) == original
        assert saved["result_json"]["model_simulated"] and not saved["result_json"]["source_synthetic"]


def test_new_parse_keeps_old_active_index_and_pins_qa_to_it(case, signed_client):
    owner, project = case
    vid, old_parse = imported(case, signed_client)
    run = enqueue_research(owner, project, uuid4().hex, "research_build", [vid])
    execute_offline(str(owner), str(project), str(run["id"]))
    revised = bundle(
        [{"type": "text", "text": "A new immutable parse revision.", "page_idx": 0, "bbox": [100, 100, 500, 200]}]
    )
    new_parse = import_native(owner, vid, revised, expected_source_sha256=digest(source()))
    assert new_parse != old_parse
    qa = enqueue_research(owner, project, uuid4().hex, "research_qa", [vid], question="NVIDIA P100?")
    with tenant_transaction(owner) as conn:
        task = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == qa["id"])).mappings().one()
        assert task["input_json"]["parses"] == [{"version_id": str(vid), "parse_id": str(old_parse)}]
        assert (
            conn.scalar(sa.select(T["paper_versions"].c.active_parse_id).where(T["paper_versions"].c.id == vid))
            == old_parse
        )
    docs = signed_client.get(f"/api/v1/research/projects/{project}/documents").json()
    doc = next(d for d in docs if d["id"] == str(vid))
    assert doc["native_index_stale"] and doc["native_parse_id"] == str(new_parse)
    execute_offline(str(owner), str(project), str(qa["id"]))
    with tenant_transaction(owner) as conn:
        saved = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == qa["id"])).mappings().one()
        assert saved["status"] == "succeeded"
        assert all(c["parse_id"] == str(old_parse) for c in saved["result_json"]["citations"])


def test_switch_back_embedding_builds_new_generation_and_old_resume_cannot_rollback(case, signed_client):
    owner, project = case
    vid, _ = imported(case, signed_client)

    def build(preset):
        with tenant_transaction(owner) as conn:
            conn.execute(
                T["projects"]
                .update()
                .where(T["projects"].c.id == project)
                .values(preferences={"model_configuration": {"preset": preset, "overrides": {}}})
            )
        run = enqueue_research(owner, project, uuid4().hex, "research_build", [vid])
        execute_offline(str(owner), str(project), str(run["id"]))
        with tenant_transaction(owner) as conn:
            state = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == run["id"])).mappings().one()
            assert state["status"] == "succeeded", state["error_json"]
            gid = conn.scalar(sa.select(T["paper_versions"].c.active_index_id).where(T["paper_versions"].c.id == vid))
        return run["id"], gid

    original, first = build("free_gemma")
    _, second = build("mock")
    _, third = build("free_gemma")
    assert len({first, second, third}) == 3
    assert execute_research(owner, project, original)["documents"][0]["generation_id"] == str(first)
    with tenant_transaction(owner) as conn:
        assert (
            conn.scalar(sa.select(T["paper_versions"].c.active_index_id).where(T["paper_versions"].c.id == vid))
            == third
        )
        configs = (
            conn.execute(
                sa.select(T["index_generations"].c.profile_id).where(T["index_generations"].c.id.in_([first, third]))
            )
            .scalars()
            .all()
        )
        assert configs[0] == configs[1]


def test_user_journey_readiness_matches_model_switch_and_blocks_before_queue(case, signed_client):
    owner, project = case
    vid, _ = imported(case, signed_client)

    def doc():
        response = signed_client.get(f"/api/v1/research/projects/{project}/documents")
        assert response.status_code == 200
        return next(d for d in response.json() if d["id"] == str(vid))

    assert not doc()["can_query_offline"]
    run = enqueue_research(owner, project, uuid4().hex, "research_build", [vid])
    execute_offline(str(owner), str(project), str(run["id"]))
    assert doc()["can_query_offline"] and not doc()["can_query_cloud"]
    with tenant_transaction(owner) as conn:
        original = conn.scalar(sa.select(T["paper_versions"].c.active_index_id).where(T["paper_versions"].c.id == vid))
        before = conn.scalar(sa.select(sa.func.count()).select_from(T["runs"]).where(T["runs"].c.project_id == project))
        conn.execute(
            T["projects"]
            .update()
            .where(T["projects"].c.id == project)
            .values(preferences={"model_configuration": {"preset": "mock", "overrides": {}}})
        )
    assert not doc()["can_query_offline"]
    response = signed_client.post(
        f"/api/v1/research/projects/{project}/questions",
        json={"version_ids": [str(vid)], "question": "NVIDIA P100?"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 409 and response.json()["detail"] == "embedding_profile_requires_rebuild"
    with tenant_transaction(owner) as conn:
        assert (
            conn.scalar(sa.select(sa.func.count()).select_from(T["runs"]).where(T["runs"].c.project_id == project))
            == before
        )
        assert (
            conn.scalar(sa.select(T["paper_versions"].c.active_index_id).where(T["paper_versions"].c.id == vid))
            == original
        )
    run = enqueue_research(owner, project, uuid4().hex, "research_build", [vid])
    execute_offline(str(owner), str(project), str(run["id"]))
    assert doc()["can_query_offline"] and not doc()["can_query_cloud"]


def test_document_readiness_does_not_leak_other_project(signed_client, accounts):
    other = accounts["reviewer"]["project"]["id"]
    assert signed_client.get(f"/api/v1/research/projects/{other}/documents").status_code == 404
