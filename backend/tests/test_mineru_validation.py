"""One-file MinerU lifecycle never uploads twice or exposes signed URLs."""

from io import BytesIO
import hashlib
import json
from uuid import uuid4
import zipfile

import httpx
import pytest
from pydantic import SecretStr

from scholarship.models.mineru_validation import (
    plan_parts,
    inspect_scan_archive,
    issue_scan_approval,
    MinerUScanValidation,
)
from scholarship.config import settings
from scholarship.adapters.providers import MinerU, ContractError
from test_offline_business import case as case


def bundle():
    raw = BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr(
            "full.md",
            "We trained on 8 NVIDIA P100 GPUs. Adam beta values 0.9 and 0.98. " + "Scientific OCR text. " * 10,
        )
        archive.writestr("middle.json", json.dumps({"pdf_info": [{"page_idx": i} for i in range(15)]}))
    return raw.getvalue()


def test_provider_limit_and_global_page_mapping():
    parts = plan_parts(541)
    assert [(p.first, p.last) for p in parts] == [(1, 200), (201, 400), (401, 541)]
    assert [p.source_page(i) for p in parts for i in range(1, p.last - p.first + 2)] == list(range(1, 542))
    with pytest.raises(ContractError):
        parts[0].source_page(201)
    with pytest.raises(ContractError):
        MinerU(transport=httpx.MockTransport(lambda r: pytest.fail("must not send"))).prepare(
            "book.pdf", "book", pages=201, byte_size=10
        )
    inspected = inspect_scan_archive(bundle(), 15)
    assert inspected["pages"] == 15 and inspected["hardware_sample_found"] and inspected["publishable"] is False


@pytest.mark.parametrize("deny,download_failure", [(False, False), (True, False), (False, True)])
def test_scan_admission_precedes_upload_and_completed_steps_are_reused(
    case, database, tmp_path, monkeypatch, deny, download_failure
):
    owner, project = case
    source = tmp_path / "1706.03762v1_img.pdf"
    source.write_bytes(b"public fixture")
    scope = {
        "filename": source.name,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "pages": 15,
        "free_policy": {"commercial_charging": False},
        "maximum_files": 1,
    }
    monkeypatch.setattr("scholarship.models.mineru_validation.scan_scope", lambda source, policy: scope)
    monkeypatch.setattr(settings(), "mineru_api_key", SecretStr("test-mineru-only"))
    row = issue_scan_approval(database, key=uuid4().hex, scope=scope, owner_id=owner, project_id=project, approved=True)
    sent = []
    data_id = None
    downloads = 0

    def reply(request):
        nonlocal data_id, downloads
        sent.append((request.method, request.url.path))
        if request.url.path.endswith("/file-urls/batch"):
            assert request.headers["Authorization"] == "Bearer test-mineru-only"
            if deny:
                return httpx.Response(403)
            data_id = json.loads(request.content)["files"][0]["data_id"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": "test_batch",
                        "file_urls": ["https://mineru.oss-cn-shanghai.aliyuncs.com/upload?sig=signed-private"],
                    },
                },
            )
        if request.method == "PUT":
            assert "authorization" not in request.headers
            return httpx.Response(200)
        if "/extract-results/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": data_id,
                                "state": "done",
                                "full_zip_url": "https://mineru.oss-cn-shanghai.aliyuncs.com/result.zip?sig=signed-private",
                            }
                        ]
                    },
                },
            )
        assert "authorization" not in request.headers
        downloads += 1
        if download_failure and downloads == 1:
            raise httpx.ReadTimeout("private signed url must not leak")
        return httpx.Response(200, content=bundle())

    monkeypatch.setattr("scholarship.models.mineru_validation.PROJECT_ROOT", tmp_path)
    client = MinerUScanValidation(owner, row["id"], source, poll_interval=0, transport=httpx.MockTransport(reply))
    try:
        result = client.execute()
    finally:
        client.close()
    assert "signed-private" not in json.dumps(result)
    assert sum(m == "PUT" for m, path in sent) == (0 if deny else 1)
    if not deny and not download_failure:
        assert result["passed"] and result["inspection"]["pages"] == 15
    count = len(sent)
    again = MinerUScanValidation(owner, row["id"], source, poll_interval=0, transport=httpx.MockTransport(reply))
    try:
        resumed = again.execute()
    finally:
        again.close()
    if download_failure:
        assert resumed["passed"] and downloads == 2
        assert sum(method == "PUT" for method, path in sent) == 1
        assert sum(method == "POST" for method, path in sent) == 1
    else:
        assert len(sent) == count


def test_native_layout_filename_and_signed_download_dns_are_supported(tmp_path, monkeypatch):
    from scholarship.models.transports import download_with_dns_override
    import subprocess

    raw = BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        z.writestr("full.md", "8 NVIDIA P100 GPUs and 0.9 0.98. " + "text " * 50)
        z.writestr(
            "layout.json",
            json.dumps(
                {
                    "_version_name": "3.4.4",
                    "_backend": "hybrid",
                    "pdf_info": [{"page_idx": i, "page_size": [612, 792]} for i in range(15)],
                }
            ),
        )
    assert inspect_scan_archive(raw.getvalue(), 15)["producer_version"] == "3.4.4"
    captured = []

    def command(args, **kwargs):
        captured.append((args, kwargs))
        assert "signed-private" not in " ".join(args)
        assert "--insecure" not in args and "-k" not in args and "--location" not in args
        assert kwargs["input"].startswith('url = "https://cdn-mineru.openxlab.org.cn/')
        assert "API_KEY" not in " ".join(kwargs["env"])
        target = tmp_path / "result.zip"
        target.write_bytes(b"zip fixture")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("subprocess.run", command)
    download_with_dns_override(
        "https://cdn-mineru.openxlab.org.cn/file.zip?sig=signed-private",
        tmp_path / "result.zip",
        address="8.222.82.255",
        proxy="http://127.0.0.1:7897",
    )
    assert "--connect-to" in captured[0][0]
