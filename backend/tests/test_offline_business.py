"""Real PG/Qdrant/checkpoints with synthetic provider responses, no live models."""

from contextlib import closing

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from uuid import uuid4, UUID
import zipfile
import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError

from scholarship.adapters.providers import ContractError
from scholarship.adapters.synthetic import vector
from scholarship.domains.deep_parse import import_synthetic, fixture_bundle
from scholarship.domains.indexing import build_index, snapshot, hybrid
from scholarship.domains.offline_workflow import demo_versions, enqueue as schedule, qa_graph, DEMO_DOCS
from scholarship.domains.retrieval_profile import chunks, PROFILE, lexical
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.health import qdrant_client
from scholarship.infrastructure.schema import tables as T
from scholarship.workers.offline import execute_offline


def enqueue(*args, **kwargs):
    # Direct fault-injection tests own execution; API tests use the real outbox.
    return schedule(*args, **kwargs, dispatch=False)


@pytest.fixture
def case(database, accounts):
    owner = accounts["researcher"]["user"]["id"]
    with database.begin() as conn:
        project = conn.scalar(
            T["projects"]
            .insert()
            .values(owner_id=owner, name="offline-test-" + uuid4().hex, description="", preferences={})
            .returning(T["projects"].c.id)
        )
        conn.execute(sa.text("UPDATE billing.pools SET active=false WHERE active"))
        conn.execute(sa.text("INSERT INTO billing.pools(limit_amount) VALUES(100)"))
    try:
        yield owner, project
    finally:
        with database.begin() as conn:
            conn.execute(
                T["runs"]
                .update()
                .where(T["runs"].c.project_id == project, T["runs"].c.status.in_(["queued", "running"]))
                .values(status="cancelled", lease_token=None, lease_until=None)
            )
        with closing(qdrant_client()) as client:
            for c in client.get_collections().collections:
                if c.name.startswith("ss_p_" + project.hex + "_"):
                    client.delete_collection(c.name)


def prepared(case):
    owner, project = case
    versions = demo_versions(owner, project)
    run = enqueue(owner, project, "offline_build", uuid4().hex, {})
    execute_offline(str(owner), str(project), str(run["id"]))
    with tenant_transaction(owner) as conn:
        row = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == run["id"])).mappings().one()
        assert row["status"] == "succeeded", row["error_json"]
    return versions


def test_unicode_chunks_and_lexical_profile():
    text = "α β₁ 研究🙂\n" * 220
    result = list(chunks(text))
    assert len(result) > 1
    assert result[0][0] == 0 and result[-1][1] == len(text)
    for a, b, t, n in result:
        assert t == text[a:b] and n == len(t.encode()) <= PROFILE["max_tokens"]
    assert "研究" in lexical(text) and "beta" in lexical(text)
    assert list(chunks("x" * 901, "formula")) == []


def test_atomic_parse_publication_lineage_and_immutability(case):
    owner, project = case
    vid = demo_versions(owner, project)[0]
    raw = fixture_bundle(DEMO_DOCS[0][1])
    pid = import_synthetic(owner, vid, raw, {1, 2})
    assert import_synthetic(owner, vid, raw, {1, 2}) == pid
    with tenant_transaction(owner) as conn:
        p = conn.execute(sa.select(T["document_parses"]).where(T["document_parses"].c.id == pid)).mappings().one()
        assert p["status"] == "ready" and p["completed_pages"] == 2 and p["manifest_sha256"]
        b = (
            conn.execute(sa.select(T["document_blocks"]).where(T["document_blocks"].c.parse_id == pid))
            .mappings()
            .first()
        )
        assert b["source_locator"]["raw_file_id"] and b["locator_precision"] == "bbox"
        assert (
            conn.scalar(sa.select(T["paper_versions"].c.active_parse_id).where(T["paper_versions"].c.id == vid)) is None
        )
    with pytest.raises(DBAPIError), tenant_transaction(owner) as conn:
        conn.execute(
            T["document_blocks"].update().where(T["document_blocks"].c.id == b["id"]).values(raw_text="tamper")
        )


@pytest.mark.parametrize("change", ["bbox", "schema", "page"])
def test_reject_bad_mapping_without_partial_publication(case, change):
    owner, project = case
    vid = demo_versions(owner, project)[0]
    raw = fixture_bundle(DEMO_DOCS[0][1])
    with zipfile.ZipFile(BytesIO(raw)) as z:
        doc = json.loads(z.read("middle_json.json"))
    if change == "bbox":
        doc["pages"][0]["blocks"][0]["bbox"] = [0, 0, 9999, 1]
    elif change == "schema":
        doc["metadata"]["fixture_schema"] = "unknown"
    else:
        doc["pages"][0]["page_idx"] = 7
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        z.writestr("middle_json.json", json.dumps(doc))
    with pytest.raises(ContractError):
        import_synthetic(owner, vid, output.getvalue(), {1, 2})
    with tenant_transaction(owner) as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(T["document_parses"])
                .where(T["document_parses"].c.version_id == vid)
            )
            == 0
        )


def test_upsert_crash_resume_and_old_generation_survives(case):
    owner, project = case
    versions = prepared(case)
    vid = versions[0]
    old = snapshot(owner, project, [vid])[0]
    new_pages = [["Alpha revised accuracy is 92 percent."], ["Alpha revised sample count is 121."]]
    pid = import_synthetic(owner, vid, fixture_bundle(new_pages), {1, 2})
    run = enqueue(owner, project, "offline_build", uuid4().hex, {})["id"]

    def crash(g):
        raise RuntimeError("simulated_process_loss_before_pg_publication")

    with pytest.raises(RuntimeError):
        build_index(owner, vid, pid, run, after_upsert=crash)
    assert snapshot(owner, project, [vid])[0] == old
    pinned = hybrid(owner, project, [old], "accuracy", vector("accuracy"))
    assert any("91 percent" in c["text"] for c in pinned["candidates"])
    gid = build_index(owner, vid, pid, run)
    assert build_index(owner, vid, pid, run) == gid
    assert snapshot(owner, project, [vid])[0]["id"] == str(gid)
    # Explicit pinned old runs keep their original generation; late old replay cannot roll back.
    build_index(owner, vid, UUID(old["parse_id"]), run)
    assert snapshot(owner, project, [vid])[0]["id"] == str(gid)
    assert any(
        "91 percent" in c["text"] for c in hybrid(owner, project, [old], "accuracy", vector("accuracy"))["candidates"]
    )


def test_manifest_tampering_never_activates(case):
    owner, project = case
    vid = demo_versions(owner, project)[0]
    pid = import_synthetic(owner, vid, fixture_bundle(DEMO_DOCS[0][1]), {1, 2})
    run = enqueue(owner, project, "offline_build", uuid4().hex, {})["id"]

    def tamper(g):
        with closing(qdrant_client()) as q:
            from qdrant_client import models as m

            q.set_payload(
                g["collection_name"],
                payload={"content_sha256": "0" * 64},
                points=m.Filter(
                    must=[m.FieldCondition(key="index_generation_id", match=m.MatchValue(value=str(g["id"])))]
                ),
                wait=True,
            )

    with pytest.raises(ContractError):
        build_index(owner, vid, pid, run, after_upsert=tamper)
    with tenant_transaction(owner) as conn:
        assert (
            conn.scalar(sa.select(T["paper_versions"].c.active_index_id).where(T["paper_versions"].c.id == vid)) is None
        )
    assert build_index(owner, vid, pid, run)


def test_multidoc_qa_checkpoint_resume_and_exact_evidence(case, database):
    owner, project = case
    versions = prepared(case)
    run = enqueue(
        owner,
        project,
        "offline_qa",
        uuid4().hex,
        {"question": "Compare Alpha Beta accuracy", "version_ids": [str(v) for v in versions]},
    )["id"]
    assert qa_graph(owner, project, run, interrupt_after=["retrieve"]) is None
    import subprocess
    import sys

    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from uuid import UUID; from scholarship.domains.offline_workflow import qa_graph; "
            f'print(json.dumps(qa_graph(UUID("{owner}"),UUID("{project}"),UUID("{run}"))))',
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    result = json.loads(process.stdout)
    assert result["synthetic"] and not result["insufficient_evidence"]
    assert "91 percent" in result["answer"] and "87 percent" in result["answer"]
    assert {c["version_id"] for c in result["citations"]} == {str(v) for v in versions}
    assert all(t["dense"] and t["bm25"] for t in result["trace"])
    assert qa_graph(owner, project, run) == result
    with tenant_transaction(owner) as conn:
        evidence = (
            conn.execute(
                sa.select(T["evidence_items"], T["document_blocks"].c.canonical_text)
                .join(T["document_blocks"], T["document_blocks"].c.id == T["evidence_items"].c.block_id)
                .where(T["evidence_items"].c.run_id == run)
            )
            .mappings()
            .all()
        )
        assert len(evidence) == len(result["citations"])
        assert set(conn.scalars(sa.select(T["run_steps"].c.status).where(T["run_steps"].c.run_id == run))) == {
            "succeeded"
        }
        for e in evidence:
            assert e["quote_text"] == e["canonical_text"][e["char_start"] : e["char_end"]]
        assert conn.scalar(sa.text("SELECT sum(actual) FROM billing.allocations")) == 0


def test_no_matching_evidence_refuses(case):
    owner, project = case
    versions = prepared(case)
    run = enqueue(
        owner,
        project,
        "offline_qa",
        uuid4().hex,
        {"question": "quasar oscillation astronomy", "version_ids": [str(versions[0])]},
    )["id"]
    r = qa_graph(owner, project, run)
    assert r["insufficient_evidence"] and r["citations"] == []


def test_cross_owner_scope_and_pg_recheck(case, accounts):
    owner, project = case
    versions = prepared(case)
    scope = snapshot(owner, project, versions)
    foreign = accounts["reviewer"]["user"]["id"]
    with pytest.raises(HTTPException):
        snapshot(foreign, project, versions)
    # A forged Qdrant payload is never authority for PG chunk membership.
    with closing(qdrant_client()) as q:
        from qdrant_client import models as m

        q.set_payload(
            scope[0]["collection_name"],
            payload={"owner_id": str(foreign)},
            points=m.Filter(
                must=[m.FieldCondition(key="index_generation_id", match=m.MatchValue(value=scope[0]["id"]))]
            ),
            wait=True,
        )
    assert not hybrid(owner, project, [scope[0]], "accuracy", vector("accuracy"))["candidates"]


def test_deleted_version_rejected_after_checkpoint(case):
    owner, project = case
    versions = prepared(case)
    run = enqueue(
        owner, project, "offline_qa", uuid4().hex, {"question": "Alpha accuracy", "version_ids": [str(versions[0])]}
    )["id"]
    assert qa_graph(owner, project, run, interrupt_after=["rerank"]) is None
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["paper_versions"].update().where(T["paper_versions"].c.id == versions[0]).values(deleted_at=sa.func.now())
        )
    with pytest.raises(HTTPException):
        qa_graph(owner, project, run)


def test_concurrent_identical_builds_and_question_idempotency(case):
    owner, project = case
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda _: enqueue(owner, project, "offline_build", "same-key", {}), range(2)))
    assert runs[0]["id"] == runs[1]["id"]
    versions = prepared(case)
    args = {"question": "Alpha accuracy", "version_ids": [str(versions[0])]}
    a = enqueue(owner, project, "offline_qa", "question-idem", args)
    assert enqueue(owner, project, "offline_qa", "question-idem", args)["id"] == a["id"]
    with pytest.raises(HTTPException) as exc:
        enqueue(owner, project, "offline_qa", "question-idem", {**args, "question": "Beta accuracy"})
    assert exc.value.status_code == 409


def test_api_async_build_qa_and_evidence(case, signed_client, background, accounts):
    import time

    owner, project = case
    r = signed_client.post(f"/api/v1/offline/projects/{project}/build", headers={"Idempotency-Key": uuid4().hex})
    assert r.status_code == 202, r.text

    def wait(run):
        for _ in range(100):
            row = signed_client.get(f"/api/v1/runs/{run}").json()
            if row["status"] in ["succeeded", "failed", "waiting_user"]:
                return row
            time.sleep(0.2)
        raise AssertionError("timeout")

    build = wait(r.json()["id"])
    assert build["status"] == "succeeded", build
    docs = signed_client.get(f"/api/v1/offline/projects/{project}/documents").json()
    question = signed_client.post(
        f"/api/v1/offline/projects/{project}/questions",
        json={"question": "Alpha accuracy", "version_ids": [str(docs[0]["id"])]},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert question.status_code == 202, question.text
    result = wait(question.json()["id"])
    assert result["status"] == "succeeded", result
    history = signed_client.get(f"/api/v1/offline/projects/{project}/runs").json()
    assert history[0]["id"] == question.json()["id"] and history[0]["result_json"] == result["result_json"]
    e = result["result_json"]["citations"][0]
    located = signed_client.get("/api/v1/offline/evidence/" + e["id"])
    assert (
        located.status_code == 200
        and located.json()["text"][located.json()["start"] : located.json()["end"]] == e["quote"]
    )
    assert signed_client.get("/api/v1/offline/evidence/" + str(uuid4())).status_code == 404
    assert (
        signed_client.post(
            f"/api/v1/offline/projects/{accounts['reviewer']['project']['id']}/build",
            headers={"Idempotency-Key": uuid4().hex},
        ).status_code
        == 404
    )


def test_real_pdf_bytes_and_page_reference_preserved(case, signed_client):
    """Use actual local text read-only; this does not measure MinerU/OCR quality."""
    from pathlib import Path
    from pypdf import PdfReader
    from scholarship.config import settings
    from scholarship.domains.deep_parse import persist_file
    from scholarship.domains.retrieval_profile import digest

    source = Path("/Users/cuijinglei/Documents/homework/比赛/科研小助手/pdf/1607.06450v1.pdf")
    if not source.exists():
        pytest.skip("local competition sample not present")
    raw = source.read_bytes()
    before = digest(raw)
    reader = PdfReader(BytesIO(raw))
    pages = [[(p.extract_text() or "").strip()[:120]] for p in reader.pages]
    docraw = fixture_bundle(pages)
    with zipfile.ZipFile(BytesIO(docraw)) as z:
        doc = json.loads(z.read("middle_json.json"))
    for i, p in enumerate(reader.pages):
        w, h = float(p.mediabox.width), float(p.mediabox.height)
        doc["extensions"]["docvortex_layout"]["pages"][i].update(width_pt=w, height_pt=h)
        doc["pages"][i]["blocks"][0]["bbox"] = [0, 0, w, h]  # Whole-page region, no claimed precise text box.
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        z.writestr("middle_json.json", json.dumps(doc))
    owner, project = case
    with tenant_transaction(owner) as conn:
        fid = persist_file(conn, owner, raw, "pdf", "application/pdf")
        paper = conn.scalar(
            T["papers"]
            .insert()
            .values(
                owner_id=owner,
                project_id=project,
                title="read-only sample",
                normalized_title="read-only sample",
                display_metadata={},
            )
            .returning(T["papers"].c.id)
        )
        vid = conn.scalar(
            T["paper_versions"]
            .insert()
            .values(
                owner_id=owner,
                project_id=project,
                paper_id=paper,
                file_id=fid,
                metadata_json={"page_count": len(pages), "source": "local_test"},
            )
            .returning(T["paper_versions"].c.id)
        )
    pid = import_synthetic(owner, vid, output.getvalue(), set(range(1, len(pages) + 1)))
    run = enqueue(owner, project, "offline_build", uuid4().hex, {})["id"]
    build_index(owner, vid, pid, run)
    question = enqueue(
        owner, project, "offline_qa", uuid4().hex, {"question": "Layer Normalization", "version_ids": [str(vid)]}
    )["id"]
    result = qa_graph(owner, project, question)
    assert result["citations"]
    for citation in result["citations"]:
        located = signed_client.get("/api/v1/offline/evidence/" + citation["id"]).json()
        assert located["original_pdf_available"]
        assert citation["quote"] in pages[citation["page"] - 1][0]
    response = signed_client.get(f"/api/v1/paper-versions/{vid}/file")
    assert response.status_code == 200 and digest(response.content) == before == digest(source.read_bytes())
    assert (Path(settings().scholar_data_dir) / "files").is_dir()


def test_storage_preflight_rejects_missing_file(case, database):
    from pathlib import Path
    from storage_preflight import verify_storage

    owner, project = case
    demo_versions(owner, project)
    from scholarship.infrastructure.database import engine

    runtime = engine("runtime")
    with database.connect() as admin, runtime.connect() as restricted:
        expected = admin.scalar(sa.text("SELECT count(*) FROM scholar.file_objects WHERE state='ready'"))
        inventory = restricted.scalar(sa.text("SELECT count(*) FROM scholar.storage_manifest()"))
        assert inventory == expected and inventory > 0
        assert restricted.scalar(sa.text("SELECT current_user")) == "scholar_runtime"
        assert not restricted.scalar(sa.text(
            "SELECT has_function_privilege('scholar_auth', 'scholar.storage_manifest()', 'EXECUTE')"
        ))
    report = verify_storage(runtime, Path("/nonexistent/scholarship-storage-test"))
    assert not report["passed"] and report["missing"] > 0
