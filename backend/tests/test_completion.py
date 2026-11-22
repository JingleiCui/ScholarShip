"""User-facing completion paths, isolated local state and synthetic model contracts."""

from uuid import uuid4, UUID
import sqlalchemy as sa
import pytest
from scholarship.domains.cancellation import check_cancelled, RunCancelled
from scholarship.domains.document_identity import fingerprint, similarity
from scholarship.domains.studio.workflow import submit
from scholarship.domains.retrieval_profile import digest
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.workers.offline import execute_offline
from test_offline_business import case as case
from test_live_business import imported


def test_workspace_and_reversible_library(case, signed_client):
    owner, project = case
    name = "练习空间-" + uuid4().hex
    created = signed_client.post("/api/v1/projects", json={"name": name})
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    assert signed_client.post("/api/v1/projects", json={"name": name}).status_code == 409
    with tenant_transaction(owner) as conn:
        prefs = conn.scalar(sa.select(T["projects"].c.preferences).where(T["projects"].c.id == UUID(pid)))
        from scholarship.models.configuration import load_selection

        assert prefs["model_configuration"]["preset"] == load_selection().preset
    vid, _ = imported(case, signed_client)
    papers = signed_client.get(f"/api/v1/projects/{project}/papers").json()
    paper = next(p for p in papers if p["version_id"] == str(vid))
    path = "/api/v1/papers/" + paper["id"]
    assert signed_client.patch(path, json={"title": "Updated", "current_version_id": str(vid)}).status_code == 200
    assert signed_client.patch(path, json={"archived": True}).status_code == 200
    assert signed_client.get(f"/api/v1/projects/{project}/papers").json() == []
    assert signed_client.get(f"/api/v1/projects/{project}/archived-papers").json()[0]["title"] == "Updated"
    # Retained original bytes remain available to their owner for historical evidence.
    assert signed_client.get(f"/api/v1/paper-versions/{vid}/file").status_code == 200
    assert signed_client.patch(path, json={"archived": False}).status_code == 200
    assert signed_client.patch(path, json={"current_version_id": str(uuid4())}).status_code == 409
    assert signed_client.patch(f"/api/v1/papers/{uuid4()}", json={"archived": True}).status_code == 404
    diff = signed_client.get(f"/api/v1/paper-versions/{vid}/compare/{vid}")
    assert diff.status_code == 200 and not diff.json()["changes"]


def test_history_retains_scoped_source_versions_without_exposing_task_inputs(case, signed_client):
    owner, project = case
    version, parse = imported(case, signed_client)
    run = submit(owner, project, uuid4().hex, "translate", {"parse_id": str(parse), "language": "en"})
    execute_offline(str(owner), str(project), str(run["id"]))
    rows = signed_client.get(f"/api/v1/studio/projects/{project}/runs").json()
    record = next(r for r in rows if r["id"] == str(run["id"]))
    assert record["source_version_ids"] == [str(version)]
    assert "input_json" not in record and "config_json" not in record
    assert signed_client.get(f"/api/v1/studio/projects/{uuid4()}/runs").json() == []
    # Even a retained task cannot make source IDs from another project visible.
    with tenant_transaction(owner) as conn:
        from scholarship.api.studio import history_source_versions

        row = {"id": uuid4(), "input_json": {"arguments": {"parse_ids": [str(parse)], "version_id": str(version)}}}
        assert history_source_versions(conn, [row], uuid4())[row["id"]] == []


def test_cancel_queued_never_executes_and_running_stops_at_boundary(case, signed_client):
    owner, project = case
    run = submit(owner, project, uuid4().hex, "diagram", {"nodes": ["A", "B"], "edges": [[0, 1]]})
    response = signed_client.post(f"/api/v1/runs/{run['id']}/cancel")
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    execute_offline(str(owner), str(project), str(run["id"]))
    with tenant_transaction(owner) as conn:
        assert not conn.scalar(
            sa.select(T["artifact_versions"].c.id).where(T["artifact_versions"].c.producing_run_id == run["id"])
        )
    with pytest.raises(RunCancelled):
        check_cancelled(owner, run["id"])
    run2 = submit(owner, project, uuid4().hex, "diagram", {"nodes": ["A"], "edges": []})
    with tenant_transaction(owner) as conn:
        conn.execute(T["runs"].update().where(T["runs"].c.id == run2["id"]).values(status="running"))
    response = signed_client.post(f"/api/v1/runs/{run2['id']}/cancel")
    assert response.json()["status"] == "running"
    execute_offline(str(owner), str(project), str(run2["id"]))
    assert signed_client.get(f"/api/v1/runs/{run2['id']}").json()["status"] == "cancelled"


def test_human_translation_is_new_revision_and_idempotent(case, signed_client):
    owner, project = case
    _, pid = imported(case, signed_client)
    run = submit(owner, project, uuid4().hex, "translate", {"parse_id": str(pid), "language": "en", "block_ids": []})
    execute_offline(str(owner), str(project), str(run["id"]))
    result = signed_client.get(f"/api/v1/runs/{run['id']}").json()
    assert result["status"] == "succeeded", result
    tid = result["result_json"]["translation_id"]
    rows = signed_client.get(f"/api/v1/studio/translations/{tid}/review").json()
    row = rows[0]
    original = row["translated_text"]
    edit = {
        "segment_id": row["id"],
        "expected_text_sha256": row["text_sha256"],
        "text": original + " Reviewed.",
        "reason": "核对术语",
    }
    response = signed_client.post(f"/api/v1/studio/translations/{tid}/revisions", json=edit)
    assert response.status_code == 201, response.text
    new = response.json()["translation_id"]
    assert new != tid
    assert signed_client.post(f"/api/v1/studio/translations/{tid}/revisions", json=edit).json()["translation_id"] == new
    assert signed_client.get(f"/api/v1/studio/translations/{tid}/segments").json()[0]["translated_text"] == original
    fresh = signed_client.get(f"/api/v1/studio/translations/{new}/segments").json()[0]
    assert fresh["placeholders_json"]["human_edited"] and fresh["placeholders_json"]["model_simulated"]
    assert signed_client.get(f"/api/v1/studio/translations/{new}/export").status_code == 200
    assert (
        signed_client.post(
            f"/api/v1/studio/translations/{tid}/revisions", json={**edit, "expected_text_sha256": digest("wrong")}
        ).status_code
        == 409
    )


def test_fingerprints_distinguish_content_from_filename():
    original = " ".join(f"word{i}" for i in range(600))
    assert similarity(fingerprint(original), fingerprint(original + " a small added sentence")) > 0.9
    assert similarity(fingerprint(original), fingerprint(" ".join(f"different{i}" for i in range(600)))) < 0.1
    assert fingerprint("") == []


def test_translation_warning_not_silent_rewrite():
    from scholarship.api.reading_tools import quality_flags

    assert quality_flags("In all but a few cases, 8 GPUs were used.", "在少数情况下使用 8 个 GPU。")
    assert not quality_flags("In all but a few cases, 8 GPUs were used.", "除少数情况外均使用 8 个 GPU。")


def test_selected_text_translation_has_exact_source_offsets(case, signed_client):
    owner, project = case
    _, pid = imported(case, signed_client)
    with tenant_transaction(owner) as conn:
        block = (
            conn.execute(
                sa.select(T["document_blocks"])
                .where(
                    T["document_blocks"].c.parse_id == pid, sa.func.length(T["document_blocks"].c.canonical_text) > 30
                )
                .limit(1)
            )
            .mappings()
            .one()
        )
    text = block["canonical_text"]
    span = {"block_id": str(block["id"]), "start": 2, "end": 25, "text": text[2:25]}
    run = submit(
        owner, project, uuid4().hex, "translate", {"parse_id": str(pid), "language": "en", "source_selection": span}
    )
    execute_offline(str(owner), str(project), str(run["id"]))
    result = signed_client.get(f"/api/v1/runs/{run['id']}").json()
    assert result["status"] == "succeeded", result
    rows = signed_client.get(
        "/api/v1/studio/translations/" + result["result_json"]["translation_id"] + "/segments"
    ).json()
    assert len(rows) == 1 and rows[0]["canonical_text"] == text[2:25]
    assert rows[0]["placeholders_json"]["source_start"] == 2
    bad = submit(
        owner,
        project,
        uuid4().hex,
        "translate",
        {"parse_id": str(pid), "source_selection": {**span, "text": "not source"}},
    )
    execute_offline(str(owner), str(project), str(bad["id"]))
    assert signed_client.get(f"/api/v1/runs/{bad['id']}").json()["status"] == "failed"


def test_reference_resolution_scope_and_audit(case, signed_client):
    owner, project = case
    vid, pid = imported(case, signed_client)
    with tenant_transaction(owner) as conn:
        paper = conn.scalar(sa.select(T["paper_versions"].c.paper_id).where(T["paper_versions"].c.id == vid))
        ref = conn.scalar(
            sa.select(T["bibliographic_refs"].c.id).where(T["bibliographic_refs"].c.parse_id == pid).limit(1)
        )
    url = f"/api/v1/studio/references/{ref}/resolution"
    assert signed_client.put(url, json={"paper_id": str(uuid4()), "reason": "核对作者年份"}).status_code == 409
    assert (
        signed_client.put(url, json={"paper_id": str(paper), "reason": "核对作者年份"}).json()["resolution_state"]
        == "verified"
    )
    assert (
        signed_client.put(url, json={"paper_id": None, "reason": "撤销错误匹配"}).json()["resolution_state"]
        == "unresolved"
    )
    with tenant_transaction(owner) as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(T["audit_events"])
                .where(T["audit_events"].c.action == "reference_identity_review")
            )
            == 2
        )
    assert signed_client.get(f"/api/v1/studio/parses/{pid}/reference-context").json()["can_write"]


def test_fulltext_direction_review_pins_two_sources(case, signed_client):
    from test_live_business import source, bundle
    from scholarship.domains.mineru_native import import_native

    owner, project = case
    _, first = imported(case, signed_client)
    raw = source(rotation=90)
    upload = signed_client.post(
        f"/api/v1/projects/{project}/uploads",
        files={"file": ("followup.pdf", raw, "application/pdf")},
        headers={"Idempotency-Key": uuid4().hex},
    )
    second = import_native(
        owner, UUID(upload.json()["version_id"]), bundle(size=(800, 600)), expected_source_sha256=digest(raw)
    )
    spans = []
    with tenant_transaction(owner) as conn:
        for pid in [first, second]:
            b = (
                conn.execute(sa.select(T["document_blocks"]).where(T["document_blocks"].c.parse_id == pid).limit(1))
                .mappings()
                .one()
            )
            spans.append(
                {"block_id": str(b["id"]), "start": 0, "end": len(b["canonical_text"]), "text": b["canonical_text"]}
            )
    response = signed_client.post(
        f"/api/v1/studio/projects/{project}/tasks",
        json={
            "operation": "direction_review",
            "question": "是否支持原主张",
            "reason": "比较两篇原文，但证据不足",
            "assessment": "insufficient",
            "original_evidence": spans[0],
            "followup_evidence": spans[1],
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 202, response.text
    execute_offline(str(owner), str(project), response.json()["id"])
    result = signed_client.get("/api/v1/runs/" + response.json()["id"]).json()
    assert result["status"] == "succeeded", result
    assert result["result_json"]["model_requests"] == 0
    assert len({e["parse_id"] for e in result["result_json"]["evidence"]}) == 2
    assert (
        signed_client.get("/api/v1/studio/artifacts/" + result["result_json"]["artifact_version_id"]).status_code == 200
    )


def test_simulation_quota_is_explicit_pause_not_dependency_retry(case):
    from dataclasses import replace
    from scholarship.models.configuration import load_selection
    from scholarship.models.gateway import SimulatedGateway
    from scholarship.models.fixtures import transport_factory
    from scholarship.models.contracts import ProviderFailure

    owner, project = case
    run = submit(owner, project, uuid4().hex, "diagram", {"nodes": ["A"], "edges": []})
    selection = replace(load_selection("mock"), max_requests_per_run=1)
    with SimulatedGateway(owner, run["id"], selection, transport_factory(selection)) as gateway:
        gateway.invoke("first", "embedding", "embed", {"texts": ["hello"]})
        with pytest.raises(ProviderFailure) as error:
            gateway.invoke("second", "embedding", "embed", {"texts": ["world"]})
        assert error.value.code == "model_request_quota_exhausted"


def test_index_scope_limit_rejected_before_model_calls(case, signed_client, monkeypatch):
    from dataclasses import replace
    from scholarship.models.configuration import load_selection
    from scholarship.domains import research

    owner, project = case
    vid, _ = imported(case, signed_client)
    monkeypatch.setattr(
        research, "project_selection", lambda *args: replace(load_selection("mock"), max_requests_per_run=1)
    )
    from scholarship.adapters.providers import ContractError

    with pytest.raises(ContractError) as error:
        research.enqueue_research(owner, project, uuid4().hex, "research_build", [vid])
    assert str(error.value) == "index_scope_exceeds_run_limit_split_documents"
    with tenant_transaction(owner) as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM model_runtime.calls WHERE project_id=:p"), {"p": project}) == 0
