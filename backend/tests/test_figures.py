from io import BytesIO
from uuid import uuid4, UUID
import zipfile
import sqlalchemy as sa
from test_offline_business import case as case, enqueue
from test_live_business import source as pdf_source, bundle
from scholarship.domains.mineru_native import import_native
from scholarship.domains.retrieval_profile import digest
from scholarship.domains.studio.figures import describe, source
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T


import base64
import json
import httpx
import pytest
from scholarship.adapters.providers import ContractError
from scholarship.models.adapters import ProviderClient
from scholarship.models.configuration import load_selection
from scholarship.models.figure_contract import validate_description

VALID = {
    "figure_type": "architecture",
    "visible_text": ["A", "B"],
    "nodes": [{"id": "a", "text": "A", "bbox": [0, 0, 0.4, 0.4]}, {"id": "b", "text": "B", "bbox": [0.6, 0.6, 1, 1]}],
    "edges": [{"source": "a", "target": "b", "direction": "directed", "label": ""}],
    "summary": "A connects to B",
    "uncertainties": [],
}


def test_vision_wire_and_result():
    def handler(request):
        body = json.loads(request.content)
        assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(VALID)}}],
            },
        )

    c = ProviderClient(load_selection("free_api").roles["chat"], "chat", transport=httpx.MockTransport(handler))
    try:
        r = c.describe_figure(base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode(), "image/png", "caption")
        assert r.value["edges"][0]["source"] == "a"
        assert r.simulated
    finally:
        c.close()


@pytest.mark.parametrize(
    "change",
    [
        {"edges": [{"source": "missing", "target": "b", "direction": "directed"}]},
        {"nodes": [{"id": "a", "text": "A", "bbox": [0, 0, 2, 1]}]},
        {"figure_type": "unreadable"},
        {"nodes": VALID["nodes"] + VALID["nodes"]},
    ],
)
def test_invalid_visual_structure(change):
    with pytest.raises(ContractError):
        validate_description(json.dumps({**VALID, **change}))


def test_caption_only_is_not_structure():
    with pytest.raises(ContractError):
        validate_description('{"caption":"Figure 1: architecture"}')


def test_figure_publication_retains_source_and_caption(case, signed_client):
    client = signed_client
    owner, project = case
    pdf = pdf_source()
    uploaded = client.post(
        f"/api/v1/projects/{project}/uploads",
        files={"file": ("figure.pdf", pdf, "application/pdf")},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert uploaded.status_code == 202
    version = UUID(uploaded.json()["version_id"])
    archive = BytesIO(
        bundle(
            [
                {
                    "type": "image",
                    "page_idx": 0,
                    "bbox": [100, 100, 900, 900],
                    "image_caption": ["Figure 1. Architecture"],
                    "img_path": "images/abcd.png",
                }
            ]
        )
    )
    with zipfile.ZipFile(archive, "a") as z:
        z.writestr("images/abcd.png", b"\x89PNG\r\n\x1a\nfixture")
    parse = import_native(owner, version, archive.getvalue(), expected_source_sha256=digest(pdf))
    with tenant_transaction(owner) as conn:
        block = conn.scalar(sa.select(T["document_blocks"].c.id).where(T["document_blocks"].c.parse_id == parse))
    run = enqueue(owner, project, "research_studio", uuid4().hex, {})

    from sqlalchemy.dialects.postgresql import JSONB

    with tenant_transaction(owner) as conn:
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == run["id"])
            .values(config_json={"operation": "figure"}, input_json={"arguments": {"block_ids": [str(block)]}})
        )
        stmt = sa.text("SELECT model_runtime.production_scope_allowed(:run,:scope)").bindparams(
            sa.bindparam("scope", type_=JSONB)
        )
        assert conn.scalar(stmt, {"run": run["id"], "scope": {"parse_ids": [str(parse)], "block_ids": [str(block)]}})
        assert not conn.scalar(
            stmt, {"run": run["id"], "scope": {"parse_ids": [str(parse)], "block_ids": [str(uuid4())]}}
        )
        assert not conn.scalar(stmt, {"run": run["id"], "scope": {}})

    class Gateway:
        def invoke(self, intent, role, operation, args):
            assert operation == "describe_figure"
            assert args["caption"] == "Figure 1. Architecture"
            return {"value": VALID, "simulated": True, "provider": "contract", "requested_model": "fixture"}

    result = describe(owner, project, run["id"], {"block_ids": [str(block)]}, Gateway())
    # describe publishes an immutable artifact; the worker separately completes the task row.
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == run["id"])
            .values(result_json=result, status="succeeded", finished_at=sa.func.now())
        )
    assert result["source"]["parse_id"] == str(parse)
    assert result["review_state"] == "unreviewed"
    with tenant_transaction(owner) as conn:
        assert source(conn, block)["canonical_text"] == "Figure 1. Architecture"
    response = client.get(f"/api/v1/studio/figures/{block}/results")
    assert response.status_code == 200
    assert client.get(f"/api/v1/studio/figures/{uuid4()}/results").status_code == 404

    artifact = result["artifact_version_id"]
    review = client.post(
        f"/api/v1/studio/figure-artifacts/{artifact}/reviews",
        json={"assessment": "rejected", "reason": "Arrow direction contradicts the original image."},
    )
    assert review.status_code == 200
    history = client.get(f"/api/v1/studio/projects/{project}/runs").json()
    reviewed = next(r for r in history if (r["result_json"] or {}).get("artifact_version_id") == artifact)
    assert reviewed["content_review"]["assessment"] == "rejected"
    assert reviewed["source_version_ids"] == [result["source"]["version_id"]]
    with tenant_transaction(owner) as conn:
        saved = conn.scalar(
            sa.select(T["artifact_versions"].c.metadata_json).where(T["artifact_versions"].c.id == UUID(artifact))
        )
        assert saved["review_state"] == "unreviewed"  # audit overlay, immutable original
    assert (
        client.post(
            f"/api/v1/studio/figure-artifacts/{uuid4()}/reviews", json={"assessment": "accepted", "reason": "checked"}
        ).status_code
        == 404
    )

    corrected = client.post(
        f"/api/v1/studio/figure-artifacts/{artifact}/corrections",
        headers={"Idempotency-Key": uuid4().hex},
        json={"description": VALID, "reason": "Checked node boxes against source."},
    )
    assert corrected.status_code == 202, corrected.text
    from scholarship.domains.studio.workflow import execute

    new = execute(owner, project, UUID(corrected.json()["id"]))
    assert new["external_requests"] == 0
    assert new["previous_artifact_version_id"] == artifact
    assert new["artifact_version_id"] != artifact
    assert new["description"] == VALID
    bad = {**VALID, "edges": [{"source": "absent", "target": "a", "direction": "directed"}]}
    assert (
        client.post(
            f"/api/v1/studio/figure-artifacts/{artifact}/corrections",
            headers={"Idempotency-Key": uuid4().hex},
            json={"description": bad, "reason": "checked"},
        ).status_code
        == 422
    )


def test_cloud_presets_and_independent_visual_selection():
    base = load_selection("free_api")
    visual = load_selection("free_api", overrides={"chat": "openrouter_vision"})
    assert base.roles["parser"].provider == "mineru_cloud"
    assert base.roles["chat"].model == "gpt-4o-mini"
    assert visual.roles["chat"].model == "nvidia/nemotron-3.5-super-vl:free"
    assert visual.roles["embedding"] == base.roles["embedding"]
    assert visual.roles["translation"] == base.roles["translation"]
