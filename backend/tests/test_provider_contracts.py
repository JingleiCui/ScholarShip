"""Synthetic wire contracts; socket networking is forbidden in this module."""

from io import BytesIO
import json
import socket
import zipfile

import httpx
import pytest

from scholarship.adapters.providers import (
    Embeddings,
    Reranker,
    Answerer,
    MinerU,
    Evidence,
    ExternalDisabled,
    ContractError,
    UnknownOutcome,
)
from scholarship.adapters.bundles import inspect_bundle


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def deny(*a, **k):
        raise AssertionError("provider contract attempted real network")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


@pytest.mark.parametrize("cls", [Embeddings, Reranker, Answerer, MinerU])
def test_live_transport_unavailable(cls):
    with pytest.raises(ExternalDisabled):
        cls()
    with pytest.raises(ExternalDisabled):
        cls(httpx.HTTPTransport())


def test_embedding_contract_and_index_order():
    def handler(req):
        body = json.loads(req.content)
        assert req.url.path == "/compatible-mode/v1/embeddings"
        assert body["dimensions"] == 1024 and body["model"] == "text-embedding-v4"
        assert body["input"] == ["alpha", "beta"]
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-v4",
                "data": [{"index": 1, "embedding": [0.2] * 1024}, {"index": 0, "embedding": [0.1] * 1024}],
                "usage": {"total_tokens": 12},
            },
        )

    adapter = Embeddings(httpx.MockTransport(handler))
    try:
        result = adapter.embed(["alpha", "beta"])
        assert result.value[0][0] == 0.1 and result.usage["input_tokens"] == 12
        with pytest.raises(ContractError):
            adapter.embed(["x"] * 11)
    finally:
        adapter.close()


@pytest.mark.parametrize(
    "rows",
    [
        [{"index": 0, "embedding": [1.0]}],
        [{"index": True, "embedding": [1.0] * 1024}],
        [{"index": 1, "embedding": [1.0] * 1024}],
        [{"index": 0, "embedding": [0.0] * 1024}],
    ],
)
def test_embedding_malformed(rows):
    adapter = Embeddings(
        httpx.MockTransport(lambda r: httpx.Response(200, json={"data": rows, "usage": {"total_tokens": 1}}))
    )
    try:
        with pytest.raises(ContractError):
            adapter.embed(["x"])
    finally:
        adapter.close()


def test_rerank_path_and_indices():
    def handler(req):
        assert req.url.path == "/compatible-api/v1/reranks"
        body = json.loads(req.content)
        assert "input" not in body and body["top_n"] == 2
        return httpx.Response(
            200,
            json={
                "results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}],
                "usage": {"total_tokens": 14},
            },
        )

    adapter = Reranker(httpx.MockTransport(handler))
    try:
        assert [r["index"] for r in adapter.rank("q", ["a", "b"], 2).value] == [1, 0]
    finally:
        adapter.close()


def answer_response(content, finish="stop"):
    return {
        "model": "deepseek-flash",
        "id": "offline-id",
        "choices": [{"finish_reason": finish, "message": {"content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 20},
    }


def test_answer_exact_unicode_span_and_no_tools():
    source = "准确率为 0.91。"
    content = {
        "answer": "准确率为 0.91。",
        "insufficient_evidence": False,
        "claims": [
            {
                "text": "准确率为 0.91。",
                "citations": [{"evidence_id": "e1", "quote": source, "start": 0, "end": len(source), "page": 999}],
            }
        ],
    }

    def handler(req):
        body = json.loads(req.content)
        assert body["thinking"] == {"type": "disabled"} and body["max_tokens"] == 2048 and "tools" not in body
        return httpx.Response(200, json=answer_response(content))

    adapter = Answerer(httpx.MockTransport(handler))
    try:
        result = adapter.answer("准确率？", [Evidence("e1", source, 2)])
        assert result.value["claims"][0]["citations"][0]["page"] == 2
        content["claims"][0]["citations"][0]["quote"] = "0.99"
        with pytest.raises(ContractError):
            adapter.answer("准确率？", [Evidence("e1", source, 2)])
    finally:
        adapter.close()


def test_missing_evidence_does_not_send_request():
    adapter = Answerer(httpx.MockTransport(lambda r: pytest.fail("must not request without evidence")))
    try:
        assert adapter.answer("q", []).value["insufficient_evidence"]
    finally:
        adapter.close()


@pytest.mark.parametrize("change", ["unknown_id", "out_of_bounds", "generated", "truncated"])
def test_bad_answer_rejected(change):
    content = {
        "answer": "alpha",
        "insufficient_evidence": False,
        "claims": [{"text": "alpha", "citations": [{"evidence_id": "e", "quote": "alpha", "start": 0, "end": 5}]}],
    }
    e = Evidence("e", "alpha", 1)
    if change == "unknown_id":
        content["claims"][0]["citations"][0]["evidence_id"] = "foreign"
    if change == "out_of_bounds":
        content["claims"][0]["citations"][0]["end"] = 100
    if change == "generated":
        e = Evidence("e", "alpha", 1, "generated_description")
    adapter = Answerer(
        httpx.MockTransport(
            lambda r: httpx.Response(200, json=answer_response(content, "length" if change == "truncated" else "stop"))
        )
    )
    try:
        with pytest.raises(ContractError):
            adapter.answer("q", [e])
    finally:
        adapter.close()


def test_mineru_upload_no_authorization_and_batch_mapping():
    calls = []

    def handler(req):
        calls.append(req.method)
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["model_version"] == "vlm" and body["files"][0]["data_id"] == "doc1"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": "batch1",
                        "file_urls": ["https://mineru.oss-cn-shanghai.aliyuncs.com/upload?signature=offline"],
                    },
                },
            )
        if req.method == "PUT":
            assert "authorization" not in req.headers and req.content == b"%PDF-offline"
            return httpx.Response(200)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "batch_id": "batch1",
                    "extract_result": [
                        {
                            "data_id": "doc1",
                            "state": "done",
                            "full_zip_url": "https://cdn-mineru.openxlab.org.cn/results/offline.zip",
                        }
                    ],
                },
            },
        )

    adapter = MinerU(httpx.MockTransport(handler))
    try:
        job = adapter.prepare("paper.pdf", "doc1", pages=2, byte_size=12, ocr=True)
        adapter.upload(job["upload_url"], b"%PDF-offline")
        assert adapter.poll(job["batch_id"], "doc1")["state"] == "done"
        assert calls == ["POST", "PUT", "GET"]
        with pytest.raises(ContractError):
            adapter.upload("http://127.0.0.1/private", b"%PDF-offline")
    finally:
        adapter.close()


@pytest.mark.parametrize("status", [302, 429, 500])
def test_no_automatic_retry_or_redirect(status):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, headers={"location": "https://attacker.invalid"})

    adapter = Embeddings(httpx.MockTransport(handler))
    try:
        with pytest.raises(UnknownOutcome):
            adapter.embed(["x"])
        assert len(calls) == 1
    finally:
        adapter.close()


def bundle(doc, extra=None):
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("middle_json.json", json.dumps(doc))
        if extra:
            z.writestr(extra, "bad")
    return out.getvalue()


def document():
    return {
        "schema": "docvortex.middle",
        "schema_version": "2.0",
        "metadata": {"producer": {"version": "synthetic-contract"}},
        "pages": [{"page_idx": 0, "blocks": []}, {"page_idx": 2, "blocks": []}],
        "extensions": {
            "docvortex_layout": {
                "version": 1,
                "pages": [
                    {"page_idx": 0, "width_pt": 600, "height_pt": 800},
                    {"page_idx": 2, "width_pt": 600, "height_pt": 800},
                ],
            }
        },
    }


def test_bundle_original_page_indices_and_not_publishable():
    result = inspect_bundle(bundle(document()), {1, 3})
    assert result.page_numbers == (1, 3) and not result.publishable


@pytest.mark.parametrize("path", ["../escape", "/absolute", "images/../../escape", "C:/escape", "images\\escape"])
def test_archive_paths_rejected(path):
    with pytest.raises(ContractError):
        inspect_bundle(bundle(document(), path), {1, 3})


def test_unknown_protocol_and_wrong_page_map():
    d = document()
    d["schema"] = "some.other.schema"
    with pytest.raises(ContractError):
        inspect_bundle(bundle(d), {1, 3})
    with pytest.raises(ContractError):
        inspect_bundle(bundle(document()), {1, 2})


def test_archive_symlink_and_bomb_rejected():
    import stat

    out = BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("middle_json.json", json.dumps(document()))
        symlink = zipfile.ZipInfo("images/link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(symlink, "../../private")
    with pytest.raises(ContractError):
        inspect_bundle(out.getvalue(), {1, 3})
    out = BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("middle_json.json", json.dumps(document()))
        z.writestr("padding", "A" * 100000)
    with pytest.raises(ContractError):
        inspect_bundle(out.getvalue(), {1, 3})


def test_archive_aliases_and_limits(monkeypatch):
    from scholarship.adapters import bundles

    out = BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("middle_json.json", json.dumps(document()))
        z.writestr("images/x", "a")
        z.writestr("images//x", "b")
    with pytest.raises(ContractError):
        inspect_bundle(out.getvalue(), {1, 3})
    monkeypatch.setattr(bundles, "MAX_MEMBER_BYTES", 16)
    with pytest.raises(ContractError):
        inspect_bundle(bundle(document()), {1, 3})


def test_unexpected_model_and_bad_signature_url():
    adapter = Embeddings(
        httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "model": "another-model",
                    "data": [{"index": 0, "embedding": [0.1] * 1024}],
                    "usage": {"total_tokens": 1},
                },
            )
        )
    )
    try:
        with pytest.raises(ContractError):
            adapter.embed(["x"])
    finally:
        adapter.close()
    adapter = MinerU(httpx.MockTransport(lambda r: pytest.fail("must reject before sending")))
    try:
        with pytest.raises(ContractError):
            adapter.upload("https://mineru.oss-cn-shanghai.aliyuncs.com:bad/x", b"%PDF-x")
        with pytest.raises(ContractError):
            adapter.prepare("x.pdf", "x", pages=1, byte_size=10, ocr="false")
    finally:
        adapter.close()


def test_mineru_result_download_is_private_and_not_publishable():
    content = bundle(document())

    def handler(req):
        assert req.method == "GET" and "authorization" not in req.headers
        return httpx.Response(200, content=content)

    adapter = MinerU(httpx.MockTransport(handler))
    try:
        result = adapter.download_result("https://cdn-mineru.openxlab.org.cn/results/synthetic.zip", {1, 3})
        assert result.page_numbers == (1, 3) and not result.publishable
    finally:
        adapter.close()
