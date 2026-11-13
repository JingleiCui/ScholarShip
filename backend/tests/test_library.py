from io import BytesIO
import time
from uuid import uuid4
from pypdf import PdfWriter
from scholarship.infrastructure.schema import tables as T
import sqlalchemy as sa


def blank():
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def test_pdf_import_preview_and_tenant(signed_client, accounts, background, database):
    client = signed_client
    p = accounts["researcher"]["project"]["id"]
    data = blank()
    key = uuid4().hex
    r = client.post(
        f"/api/v1/projects/{p}/uploads",
        files={"file": ("scan.pdf", data, "application/pdf")},
        headers={"Idempotency-Key": key},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    vid = body["version_id"]
    pid = body["parse_id"]
    rid = body["run_id"]
    assert client.get(f"/api/v1/paper-versions/{vid}/file").content == data
    assert client.post(
        f"/api/v1/projects/{p}/uploads", files={"file": ("scan.pdf", data)}, headers={"Idempotency-Key": key}
    ).json()["reused"]
    end = time.monotonic() + 30
    while time.monotonic() < end:
        state = client.get(f"/api/v1/runs/{rid}").json()
        if state["status"] in ["succeeded", "partially_succeeded", "failed"]:
            break
        time.sleep(0.3)
    assert state["status"] == "partially_succeeded", state
    page = client.get(f"/api/v1/parses/{pid}/pages/1").json()
    assert page["needs_ocr"] and page["blocks"] == []
    assert client.get(f"/api/v1/parses/{pid}/pages/2").status_code == 404
    assert client.get(f"/api/v1/paper-versions/{uuid4()}/file").status_code == 404
    assert (
        client.post(
            f"/api/v1/projects/{accounts['reviewer']['project']['id']}/uploads",
            files={"file": ("x.pdf", data)},
            headers={"Idempotency-Key": uuid4().hex},
        ).status_code
        == 404
    )
    with database.connect() as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(T["paper_versions"])
                .where(
                    T["paper_versions"].c.file_id
                    == sa.select(T["paper_versions"].c.file_id).where(T["paper_versions"].c.id == vid).scalar_subquery()
                )
            )
            == 1
        )


def test_invalid_pdf(signed_client, accounts):
    p = accounts["researcher"]["project"]["id"]
    for data in [b"not pdf", b"%PDF-broken"]:
        r = signed_client.post(
            f"/api/v1/projects/{p}/uploads", files={"file": ("x.pdf", data)}, headers={"Idempotency-Key": uuid4().hex}
        )
        assert r.status_code == 422


def test_identifier_normalization():
    from scholarship.domains.identifiers import normalize_identifiers

    assert normalize_identifiers("DOI: 10.1234/ABC.xyz. arXiv:1706.03762v1") == [
        {"scheme": "doi", "normalized_value": "10.1234/abc.xyz"},
        {"scheme": "arxiv", "normalized_value": "1706.03762"},
    ]


def test_real_worker_crash_preserves_finished_pages(signed_client, accounts, background, database):
    import os
    import signal
    import subprocess
    from dev import application_env
    from scholarship.config import PROJECT_ROOT

    writer = PdfWriter()
    for _ in range(80):
        writer.add_blank_page(width=600, height=800)
    output = BytesIO()
    writer.write(output)
    p = accounts["researcher"]["project"]["id"]
    response = signed_client.post(
        f"/api/v1/projects/{p}/uploads",
        files={"file": ("recovery.pdf", output.getvalue())},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    first = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with database.connect() as conn:
            first = conn.execute(
                sa.select(T["document_pages"].c.id).where(
                    T["document_pages"].c.parse_id == job["parse_id"], T["document_pages"].c.page_no == 1
                )
            ).scalar()
        if first:
            break
        time.sleep(0.01)
    assert first
    worker = background[0]
    os.kill(worker.pid, signal.SIGKILL)
    worker.wait(timeout=5)
    with database.begin() as conn:
        row = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == job["run_id"])).mappings().one()
        assert row["status"] == "running"
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == job["run_id"])
            .values(lease_until=sa.func.now() - sa.text("interval '1 second'"))
        )
        conn.execute(
            T["task_outbox"]
            .update()
            .where(T["task_outbox"].c.run_id == job["run_id"])
            .values(available_at=sa.func.now())
        )
    with (PROJECT_ROOT / "var/logs/test-worker-recovery.log").open("w") as log:
        background[0] = subprocess.Popen(
            worker.args,
            env={**application_env(), "CELERY_QUEUE": "scholarship_test"},
            cwd=PROJECT_ROOT,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        state = signed_client.get(f"/api/v1/runs/{job['run_id']}").json()
        if state["status"] in ["failed", "partially_succeeded", "succeeded"]:
            break
        time.sleep(0.2)
    assert state["status"] == "partially_succeeded", state
    with database.connect() as conn:
        assert (
            conn.scalar(
                sa.select(T["document_pages"].c.id).where(
                    T["document_pages"].c.parse_id == job["parse_id"], T["document_pages"].c.page_no == 1
                )
            )
            == first
        )
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(T["document_pages"])
                .where(T["document_pages"].c.parse_id == job["parse_id"])
            )
            == 80
        )
        assert (
            conn.scalar(
                sa.select(sa.func.count()).select_from(T["run_steps"]).where(T["run_steps"].c.run_id == job["run_id"])
            )
            == 2
        )


def test_append_version_encryption_and_limits(signed_client, accounts, monkeypatch):
    from scholarship.api import library

    p = accounts["researcher"]["project"]["id"]
    first = signed_client.post(
        f"/api/v1/projects/{p}/uploads",
        files={"file": ("first.pdf", blank())},
        headers={"Idempotency-Key": uuid4().hex},
    ).json()
    rows = signed_client.get(f"/api/v1/projects/{p}/papers").json()
    paper = next(r for r in rows if r["version_id"] == first["version_id"])
    w = PdfWriter()
    w.add_blank_page(width=601, height=800)
    out = BytesIO()
    w.write(out)
    second = signed_client.post(
        f"/api/v1/projects/{p}/uploads",
        data={"paper_id": paper["id"]},
        files={"file": ("second.pdf", out.getvalue())},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert second.status_code == 202, second.text
    rows = signed_client.get(f"/api/v1/projects/{p}/papers").json()
    assert len([r for r in rows if r["id"] == paper["id"]]) == 2
    w.encrypt("password")
    encrypted = BytesIO()
    w.write(encrypted)
    assert (
        signed_client.post(
            f"/api/v1/projects/{p}/uploads",
            files={"file": ("locked.pdf", encrypted.getvalue())},
            headers={"Idempotency-Key": uuid4().hex},
        ).status_code
        == 422
    )
    monkeypatch.setattr(library, "MAX_PDF_BYTES", 20)
    assert (
        signed_client.post(
            f"/api/v1/projects/{p}/uploads",
            files={"file": ("large.pdf", blank())},
            headers={"Idempotency-Key": uuid4().hex},
        ).status_code
        == 413
    )


def test_duplicate_review_and_foreign_file(signed_client, accounts, database):
    from dotenv import dotenv_values
    from scholarship.config import PROJECT_ROOT

    p = accounts["researcher"]["project"]["id"]
    items = []
    for width in [620, 621]:
        writer = PdfWriter()
        writer.add_blank_page(width=width, height=800)
        writer.add_metadata({"/Title": "Shared candidate title"})
        out = BytesIO()
        writer.write(out)
        r = signed_client.post(
            f"/api/v1/projects/{p}/uploads",
            files={"file": ("candidate.pdf", out.getvalue())},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert r.status_code == 202, r.text
        items.append(r.json())
    review = items[1]["review_id"]
    assert review
    assert (
        signed_client.post(f"/api/v1/duplicate-reviews/{review}/decision", json={"decision": "reject"}).status_code
        == 200
    )
    assert (
        signed_client.post(f"/api/v1/duplicate-reviews/{review}/decision", json={"decision": "reject"}).status_code
        == 200
    )
    assert (
        signed_client.post(f"/api/v1/duplicate-reviews/{review}/decision", json={"decision": "keep_both"}).status_code
        == 409
    )
    visible = signed_client.get(f"/api/v1/projects/{p}/papers").json()
    assert any(r["version_id"] == items[0]["version_id"] for r in visible)
    assert all(r["version_id"] != items[1]["version_id"] for r in visible)
    values = dotenv_values(PROJECT_ROOT / ".env")
    assert (
        signed_client.post(
            "/api/v1/auth/login", json={"username": "reviewer", "password": values["INITIAL_REVIEWER_PASSWORD"]}
        ).status_code
        == 200
    )
    assert signed_client.get(f"/api/v1/paper-versions/{items[0]['version_id']}/file").status_code == 404
    assert signed_client.get(f"/api/v1/parses/{items[0]['parse_id']}/pages/1").status_code == 404


def test_missing_original_fails_and_explicit_resume(signed_client, accounts, database, background):
    from scholarship.api.library import FILE_ROOT
    from scholarship.workers.documents import preview_parse

    import os
    import signal

    worker = background[0]
    os.kill(worker.pid, signal.SIGSTOP)
    try:
        p = accounts["researcher"]["project"]["id"]
        owner = accounts["researcher"]["user"]["id"]
        w = PdfWriter()
        w.add_blank_page(width=633, height=800)
        out = BytesIO()
        w.write(out)
        data = out.getvalue()
        r = signed_client.post(
            f"/api/v1/projects/{p}/uploads",
            files={"file": ("repair.pdf", data)},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert r.status_code == 202
        job = r.json()
        with database.connect() as conn:
            key = conn.scalar(
                sa.select(T["file_objects"].c.storage_key)
                .join(T["paper_versions"], T["paper_versions"].c.file_id == T["file_objects"].c.id)
                .where(T["paper_versions"].c.id == job["version_id"])
            )
        path = FILE_ROOT / key
        path.unlink()
        preview_parse(str(owner), str(p), job["run_id"])
        assert signed_client.get(f"/api/v1/runs/{job['run_id']}").json()["status"] == "failed"
        path.write_bytes(data)
        r = signed_client.post(f"/api/v1/parses/{job['parse_id']}/resume")
        assert r.status_code == 202, r.text
    finally:
        os.kill(worker.pid, signal.SIGCONT)
    end = time.monotonic() + 30
    while time.monotonic() < end:
        state = signed_client.get(f"/api/v1/runs/{job['run_id']}").json()
        if state["status"] in ["failed", "partially_succeeded", "succeeded"]:
            break
        time.sleep(0.2)
    assert state["status"] == "partially_succeeded", state
