"""Local native/OCR extraction, ordering, provenance and durable orchestration."""

from io import BytesIO
from uuid import UUID, uuid4
import shutil

import pytest
import sqlalchemy as sa
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from scholarship.adapters.providers import ContractError
from scholarship.domains.studio.document_engine import extract, policy_snapshot, reading_order
from scholarship.domains.studio.workflow import submit
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.workers.offline import execute_offline
from test_offline_business import case as case


def native_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(600, 800)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 16 Tf 40 760 Td (Document evidence) Tj ET\n"
        b"BT /F1 12 Tf 40 700 Td (Left first source paragraph.) Tj ET\n"
        b"BT /F1 12 Tf 40 680 Td (Left second source paragraph.) Tj ET\n"
        b"BT /F1 12 Tf 330 700 Td (Right first source paragraph.) Tj ET\n"
        b"BT /F1 12 Tf 330 680 Td (Right second source paragraph.) Tj ET\n"
        b"40 430 180 120 re S 350 430 180 120 re S 220 490 m 350 490 l S\n"
        b"BT /F1 12 Tf 70 485 Td (Input) Tj ET\n"
        b"BT /F1 12 Tf 380 485 Td (Output) Tj ET\n"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def scan_pdf():
    from matplotlib import font_manager

    image = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_manager.findfont("DejaVu Sans"), 32)
    draw.text((80, 100), "Local OCR evidence document", fill="black", font=font)
    draw.text((80, 160), "The sample uses eight GPU devices.", fill="black", font=font)
    draw.rectangle((120, 400, 450, 650), outline="black", width=6)
    draw.rectangle((700, 400, 1050, 650), outline="black", width=6)
    draw.line((450, 525, 700, 525), fill="black", width=6)
    draw.text((180, 500), "Input", fill="black", font=font)
    draw.text((760, 500), "Output", fill="black", font=font)
    output = BytesIO()
    image.save(output, format="PDF", resolution=150)
    return output.getvalue()


def test_native_regions_keep_columns_and_vector_figure(tmp_path):
    path = tmp_path / "native.pdf"
    path.write_bytes(native_pdf())
    pages, assets = extract(path, policy_snapshot())
    assert len(pages) == 1 and not pages[0]["ocr_performed"]
    joined = "\n".join(b["text"] for b in pages[0]["blocks"])
    assert joined.index("Left second") < joined.index("Right first")
    assert assets and any(b["kind"] == "figure" for b in pages[0]["blocks"])
    for b in pages[0]["blocks"]:
        assert len(b["pdf_bbox"]) == 4 and b["method"]


@pytest.mark.skipif(not shutil.which("tesseract"), reason="real local OCR executable not installed")
def test_real_scan_ocr_and_image_region(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(scan_pdf())
    pages, assets = extract(path, policy_snapshot())
    assert pages[0]["ocr_performed"]
    text = " ".join(b["text"] for b in pages[0]["blocks"])
    assert "eight GPU" in text and assets
    assert all(b["method"] in {"tesseract-tsv", "local-image-region-candidate"} for b in pages[0]["blocks"])


def test_missing_ocr_and_policy_fail_explicitly(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(scan_pdf())
    with pytest.raises(ContractError, match="local_ocr_not_installed"):
        extract(path, policy_snapshot({"tesseract_command": "scholarship-missing-ocr"}))
    with pytest.raises(ContractError):
        policy_snapshot({"ocr_language": "eng;bad"})


def test_spanning_title_then_columns():
    blocks = [
        {"text": text, "bbox": box}
        for text, box in [
            ("R2", [330, 180, 550, 200]),
            ("L2", [40, 180, 260, 200]),
            ("Title", [40, 40, 550, 65]),
            ("R1", [330, 140, 550, 160]),
            ("L1", [40, 140, 260, 160]),
        ]
    ]
    assert [b["text"] for b in reading_order(blocks)] == ["Title", "L1", "L2", "R1", "R2"]


def upload(case, client):
    response = client.post(
        f"/api/v1/projects/{case[1]}/uploads",
        files={"file": ("local.pdf", native_pdf(), "application/pdf")},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 202, response.text
    return UUID(response.json()["version_id"])


def test_upload_automatically_saves_one_durable_preparation(case, signed_client):
    owner, project = case
    vid = upload(case, signed_client)
    status = signed_client.get(f"/api/v1/research/versions/{vid}/preparation").json()
    assert not status["index_ready"] and status["preparation"]["status"] == "queued"
    duplicate = signed_client.post(
        f"/api/v1/projects/{project}/uploads",
        files={"file": ("again.pdf", native_pdf(), "application/pdf")},
        headers={"Idempotency-Key": uuid4().hex},
    ).json()
    assert duplicate["reused"] and duplicate["preparation_run_id"] == status["preparation"]["id"]
    with tenant_transaction(owner) as conn:
        outbox = conn.scalar(
            sa.select(sa.func.count())
            .select_from(T["task_outbox"])
            .where(T["task_outbox"].c.run_id == UUID(status["preparation"]["id"]))
        )
        assert outbox == 1
    from scholarship.domains.chat_preparation import progress
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        progress(uuid4(), vid)


def test_auto_preparation_pins_index_before_optional_enrichment_and_preserves_unknown(case, signed_client):
    owner, project = case
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["projects"]
            .update()
            .where(T["projects"].c.id == project)
            .values(preferences={"model_configuration": {"preset": "local", "overrides": {}}})
        )
    vid = upload(case, signed_client)
    status = signed_client.get(f"/api/v1/research/versions/{vid}/preparation").json()
    run = status["preparation"]["id"]
    # Change project choice after upload: queued work must keep its original local snapshot.
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["projects"]
            .update()
            .where(T["projects"].c.id == project)
            .values(preferences={"model_configuration": {"preset": "mock", "overrides": {}}})
        )
    execute_offline(str(owner), str(project), run)
    status = signed_client.get(f"/api/v1/research/versions/{vid}/preparation").json()
    assert status["parse_ready"] and not status["index_ready"]
    assert status["preparation"]["status"] == "succeeded" and status["index"]["status"] == "queued"
    with tenant_transaction(owner) as conn:
        index = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == UUID(status["index"]["id"]))).mappings().one()
        assert index["config_json"]["models"]["preset"] == "local"
        for child in status["extras"]:
            created = conn.scalar(sa.select(T["runs"].c.created_at).where(T["runs"].c.id == UUID(child["id"])))
            assert index["created_at"] <= created
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == index["id"])
            .values(status="waiting_user", error_json={"code": "production_call_reconciliation_required"})
        )
    retry = signed_client.post(f"/api/v1/research/versions/{vid}/prepare", headers={"Idempotency-Key": uuid4().hex})
    assert retry.status_code == 202 and retry.json()["id"] == run
    status = signed_client.get(f"/api/v1/research/versions/{vid}/preparation").json()
    assert status["index"]["status"] == "waiting_user"


def test_missing_local_configuration_fails_without_inference_and_can_recover(case, signed_client):
    owner, project = case
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["projects"]
            .update()
            .where(T["projects"].c.id == project)
            .values(preferences={"model_configuration": {"preset": "mock", "overrides": {}}})
        )
    vid = upload(case, signed_client)
    state = signed_client.get(f"/api/v1/research/versions/{vid}/preparation").json()
    run = state["preparation"]["id"]
    execute_offline(str(owner), str(project), run)
    state = signed_client.get(f"/api/v1/research/versions/{vid}/preparation").json()
    assert state["parse_ready"] and state["preparation"]["status"] == "failed"
    assert state["preparation"]["error"]["contract_code"] == "local_preparation_configuration_required"
    with tenant_transaction(owner) as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM model_runtime.calls WHERE project_id=:p"), {"p": project}) == 0
        conn.execute(
            T["projects"]
            .update()
            .where(T["projects"].c.id == project)
            .values(preferences={"model_configuration": {"preset": "local", "overrides": {}}})
        )
    recovered = signed_client.post(f"/api/v1/research/versions/{vid}/prepare", headers={"Idempotency-Key": uuid4().hex})
    assert recovered.status_code == 202 and recovered.json()["id"] != run
    assert signed_client.get(f"/api/v1/runs/{run}").json()["status"] == "failed"


def test_pipeline_publication_alignment_and_original_retention(case, signed_client):
    owner, project = case
    vid = upload(case, signed_client)
    run = submit(owner, project, uuid4().hex, "local_parse", {"version_id": str(vid)})
    execute_offline(str(owner), str(project), str(run["id"]))
    job = signed_client.get(f"/api/v1/runs/{run['id']}").json()
    assert job["status"] == "succeeded", job
    pid = job["result_json"]["parse_id"]
    aligned = signed_client.get(f"/api/v1/studio/versions/{vid}/alignment").json()
    assert aligned["parse_id"] == pid and aligned["translation_blocks"] == 0
    assert any(b["has_image"] for b in aligned["blocks"])
    assert all(b["translation_state"] == "untranslated" for b in aligned["blocks"])
    assert signed_client.get(f"/api/v1/paper-versions/{vid}/file").content == native_pdf()
    with tenant_transaction(owner) as conn:
        assert not conn.scalar(sa.select(T["paper_versions"].c.active_index_id).where(T["paper_versions"].c.id == vid))
    again = submit(owner, project, uuid4().hex, "local_parse", {"version_id": str(vid)})
    execute_offline(str(owner), str(project), str(again["id"]))
    assert signed_client.get(f"/api/v1/runs/{again['id']}").json()["result_json"]["parse_id"] == pid
    assert signed_client.get(f"/api/v1/studio/versions/{uuid4()}/alignment").status_code == 404


def test_alignment_uses_source_spans_and_never_claims_partial_translation_complete(case, signed_client):
    owner, project = case
    vid = upload(case, signed_client)
    run = submit(owner, project, uuid4().hex, "local_parse", {"version_id": str(vid)})
    execute_offline(str(owner), str(project), str(run["id"]))
    aligned = signed_client.get(f"/api/v1/studio/versions/{vid}/alignment").json()
    block = next(b for b in aligned["blocks"] if len(b["canonical_text"]) > 20)
    selection = {"block_id": block["id"], "start": 0, "end": 10, "text": block["canonical_text"][:10]}
    partial = submit(
        owner,
        project,
        uuid4().hex,
        "translate",
        {"parse_id": aligned["parse_id"], "source_selection": selection, "language": "zh-CN"},
    )
    execute_offline(str(owner), str(project), str(partial["id"]))
    state = signed_client.get(f"/api/v1/runs/{partial['id']}").json()
    assert state["status"] == "succeeded", state
    rows = signed_client.get(f"/api/v1/studio/versions/{vid}/alignment").json()
    result = next(b for b in rows["blocks"] if b["id"] == block["id"])
    assert result["translation_state"] == "partial"
    assert "".join(p["source"] for p in result["parts"]) == block["canonical_text"]
    assert result["parts"][0]["model_simulated"] and result["parts"][-1]["translation"] is None


def test_preparation_queues_only_local_children_and_replay_deduplicates(case, signed_client, monkeypatch):
    from scholarship.models.configuration import load_selection

    owner, project = case
    vid = upload(case, signed_client)
    selection = load_selection("local")
    monkeypatch.setattr("scholarship.domains.studio.workflow.project_selection", lambda *_: selection)
    run = submit(owner, project, uuid4().hex, "prepare_document", {"version_id": str(vid)})
    execute_offline(str(owner), str(project), str(run["id"]))
    job = signed_client.get(f"/api/v1/runs/{run['id']}").json()
    assert job["status"] == "succeeded", job
    result = job["result_json"]
    assert result["figure_tasks"] and result["translation_tasks"] == 1
    with tenant_transaction(owner) as conn:
        rows = (
            conn.execute(sa.select(T["runs"]).where(T["runs"].c.id.in_([UUID(c["id"]) for c in result["children"]])))
            .mappings()
            .all()
        )
        assert len(rows) == len(result["children"])
        assert all(r["config_json"]["mode"] == "local_inference" for r in rows)
        assert all(r["config_json"]["models"] == selection.snapshot() for r in rows)
    execute_offline(str(owner), str(project), str(run["id"]))
    again = signed_client.get(f"/api/v1/runs/{run['id']}").json()["result_json"]
    assert again["children"] == result["children"]


def test_partial_dispatch_recovery_keeps_pinned_models_and_existing_child(case, signed_client, monkeypatch):
    from scholarship.domains.studio import workflow
    from scholarship.models.configuration import load_selection

    owner, project = case
    vid = upload(case, signed_client)
    selection = load_selection("local")
    monkeypatch.setattr(workflow, "project_selection", lambda *_: selection)
    run = workflow.submit(owner, project, uuid4().hex, "prepare_document", {"version_id": str(vid)})
    original = workflow.submit
    calls = []

    def interrupted(*args, **kwargs):
        if calls:
            raise ContractError("injected_dispatch_interruption")
        child = original(*args, **kwargs)
        calls.append(child["id"])
        return child

    monkeypatch.setattr(workflow, "submit", interrupted)
    from scholarship.domains.studio.document_pipeline import prepare_document

    with tenant_transaction(owner) as conn:
        job = dict(conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == run["id"])).mappings().one())
    with pytest.raises(ContractError, match="injected_dispatch_interruption"):
        prepare_document(owner, project, run["id"], job)
    assert len(calls) == 1
    # Simulate a dead worker's expired lease, not replay of a terminal failed task.
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == run["id"])
            .values(status="running", lease_token=uuid4(), lease_until=sa.func.now() - sa.text("interval '1 second'"))
        )
    monkeypatch.setattr(workflow, "submit", original)
    monkeypatch.setattr(workflow, "project_selection", lambda *_: pytest.fail("must not read changed project models"))
    execute_offline(str(owner), str(project), str(run["id"]))
    recovered = signed_client.get(f"/api/v1/runs/{run['id']}").json()
    assert recovered["status"] == "succeeded", recovered
    ids = [child["id"] for child in recovered["result_json"]["children"]]
    assert str(calls[0]) in ids and len(ids) == len(set(ids))
