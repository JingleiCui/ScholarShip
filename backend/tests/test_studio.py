from io import BytesIO
import ast
import json
from uuid import UUID, uuid4
import zipfile
import httpx
import pytest
import sqlalchemy as sa
from scholarship.adapters.providers import ContractError
from scholarship.domains.studio.charting import chart_bundle
from scholarship.domains.studio.translation import protect, restore
from scholarship.domains.studio.discovery import DiscoveryClient, search
from scholarship.domains.studio.graph import graph
from scholarship.domains.studio.workflow import submit
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.workers.offline import execute_offline
from test_offline_business import case as case
from test_live_business import imported

CSV = b"name,a,b,c,year\nA,1,3,9,2024\nB,2,5,8,2025\nC,3,8,6,2026\n"


@pytest.mark.parametrize("kind", ["table", "line", "bar", "radar", "radar3d"])
def test_chart_artifact_reproducible_manifest_and_script(kind, tmp_path):
    config = {
        "kind": kind,
        "x": "name",
        "y": ["a", "b", "c"],
        "z": "year",
        "units": {"year": "year"},
        "missing": "reject",
        "title": "Test",
    }
    raw, meta = chart_bundle(CSV, config)
    with zipfile.ZipFile(BytesIO(raw)) as z:
        assert z.read("chart.png").startswith(b"\x89PNG") and z.read("input.csv") == CSV
        ast.parse(z.read("plot.py").decode())
        saved = json.loads(z.read("metadata.json"))
        assert saved["rows_used"] == 3 and saved["summary"]["a"]["mean"] == 2
        assert saved["third_dimension"] == ("year" if kind == "radar3d" else None)
        assert "exec(" not in z.read("plot.py").decode()
    assert meta["source_sha256"]


def test_exported_script_reproduces_actual_chart(tmp_path):
    import subprocess
    import sys

    raw, meta = chart_bundle(CSV, {"kind": "line", "x": "name", "y": ["a"], "title": "Reproduce", "units": {"a": "ms"}})
    with zipfile.ZipFile(BytesIO(raw)) as z:
        expected = z.read("chart.png")
        for name in ["plot.py", "input.csv"]:
            (tmp_path / name).write_bytes(z.read(name))
    result = subprocess.run([sys.executable, str(tmp_path / "plot.py")], cwd=tmp_path, capture_output=True, timeout=30)
    assert result.returncode == 0
    assert (tmp_path / "chart.png").read_bytes() == expected
    assert json.loads((tmp_path / "metadata.json").read_text()) == meta


def test_chinese_chart_uses_explicit_available_font():
    raw, meta = chart_bundle(
        "模型,准确率\n基准,80\n候选,90\n".encode(),
        {"kind": "bar", "x": "模型", "y": ["准确率"], "title": "合成数据验证", "units": {"准确率": "百分比"}},
    )
    assert meta["font_family"] != "DejaVu Sans" and meta["config"]["font_family"] == meta["font_family"]
    with zipfile.ZipFile(BytesIO(raw)) as z:
        assert z.read("chart.png").startswith(b"\x89PNG")


def test_native_table_supplement_uses_exact_scoped_source(case, signed_client):
    from test_live_business import bundle, source
    from scholarship.domains.mineru_native import import_native
    from scholarship.domains.research import enqueue_research
    from scholarship.domains.structured_retrieval import table_evidence

    owner, project = case
    vid, _ = imported(case, signed_client)
    text = "BLEU: <table><tr><td>Model</td><td>score</td></tr><tr><td>Transformer</td><td>28.4</td></tr></table>"
    pid = import_native(
        owner,
        vid,
        bundle([{"type": "table", "table_body": text, "page_idx": 0, "bbox": [100, 100, 900, 500]}]),
        expected_source_sha256=__import__("hashlib").sha256(source()).hexdigest(),
    )
    job = enqueue_research(owner, project, uuid4().hex, "research_build", [vid])
    execute_offline(str(owner), str(project), str(job["id"]))
    with tenant_transaction(owner) as conn:
        generation = dict(
            conn.execute(sa.select(T["index_generations"]).where(T["index_generations"].c.parse_id == pid))
            .mappings()
            .one()
        )
    scope = [{k: str(v) if isinstance(v, UUID) else v for k, v in generation.items()}]
    tables = table_evidence(owner, project, scope, "Transformer BLEU score?")
    assert len(tables) == 1 and text in tables[0]["text"]
    assert tables[0]["retrieval_mode"] == "native_table_lexical_supplement"
    with pytest.raises(ContractError):
        table_evidence(uuid4(), project, scope, "BLEU")


@pytest.mark.parametrize(
    "data,config",
    [
        (b"a,a\n1,2", {"kind": "line", "x": "a", "y": ["a"]}),
        (CSV, {"kind": "radar3d", "x": "name", "y": ["a", "b", "c"]}),
        (b"name,a\nA,inf\n", {"kind": "line", "x": "name", "y": ["a"]}),
    ],
)
def test_invalid_charts_fail_closed(data, config):
    with pytest.raises(ValueError):
        chart_bundle(data, config)


def test_translation_formula_and_code_integrity():
    source = "Use $\\beta_1=0.9$ and $$x^2$$. ```python\nx=1\n``` Cite [18]."
    masked, tokens = protect(source)
    assert restore(masked, tokens) == source
    with pytest.raises(ContractError):
        restore(masked + next(iter(tokens)), tokens)
    with pytest.raises(ContractError):
        restore(masked.replace(next(iter(tokens)), ""), tokens)


def test_local_parse_marks_unrecognized_nul_glyphs_without_changing_pdf(case, signed_client, monkeypatch):
    from types import SimpleNamespace
    from scholarship.domains.studio.local_parse import parse_local

    owner, project = case
    vid, _ = imported(case, signed_client)

    class PDF:
        pages = [
            SimpleNamespace(
                rotation=0,
                cropbox=[0, 0, 600, 800],
                mediabox=[0, 0, 600, 800],
                width=600,
                height=800,
                close=lambda: None,
                extract_words=lambda **kwargs: [
                    {
                        "text": "Long source text with unknown \x00 glyph and retained context.",
                        "x0": 20,
                        "x1": 500,
                        "top": 100,
                        "bottom": 115,
                        "size": 12,
                        "fontname": "Times",
                    }
                ],
            )
        ]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("scholarship.domains.studio.local_parse.pdfplumber.open", lambda path: PDF())
    parsed = parse_local(owner, project, vid)
    assert parsed["nul_glyph_replacements"] == 1 and parsed["quality_warning"]
    with tenant_transaction(owner) as conn:
        b = (
            conn.execute(
                sa.select(T["document_blocks"]).where(T["document_blocks"].c.parse_id == UUID(parsed["parse_id"]))
            )
            .mappings()
            .one()
        )
        assert "\x00" not in b["canonical_text"] and "\ufffd" in b["canonical_text"]
        assert b["structured_json"]["nul_glyph_replacements"] == 1


def test_explicit_code_links_checked_without_claiming_reproduction(case):
    from scholarship.domains.studio.discovery import store_records, verify_code

    owner, project = case
    record = {
        "provider": "arxiv",
        "external_id": "1706.03762v1",
        "title": "Attention",
        "abstract": "Code https://github.com/example/research.git and https://github.com.evil/unsafe/path",
        "scope": "abstract",
        "url": "https://arxiv.org/abs/1706.03762v1",
        "pdf_url": None,
    }
    stored = store_records(owner, project, "attention", [record])[0]
    requests = []

    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "full_name": "example/research",
                "private": False,
                "archived": False,
                "license": {"spdx_id": "MIT"},
                "stargazers_count": 10,
            },
        )

    result = verify_code(owner, UUID(stored["recommendation_id"]), transport=httpx.MockTransport(handle))
    assert requests == ["https://api.github.com/repos/example/research"]
    assert result["repositories"][0]["license_spdx"] == "MIT"
    assert not result["reproduction_verified"] and not result["repositories"][0]["reproduction_verified"]


def test_studio_translation_draft_plot_and_tenant_artifacts(case, signed_client):
    owner, project = case
    vid, pid = imported(case, signed_client)

    def run(op, inputs):
        r = submit(owner, project, uuid4().hex, op, inputs)
        execute_offline(str(owner), str(project), str(r["id"]))
        with tenant_transaction(owner) as conn:
            row = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == r["id"])).mappings().one()
            assert row["status"] == "succeeded", row["error_json"]
            return row["result_json"]

    translated = run("translate", {"parse_id": str(pid), "language": "zh-CN", "block_ids": []})
    segments = signed_client.get("/api/v1/studio/translations/" + translated["translation_id"] + "/segments").json()
    assert len(segments) == 3 and segments[0]["placeholders_json"]["model_simulated"]
    assert run("translate", {"parse_id": str(pid), "language": "zh-CN", "block_ids": []})["cached"]
    draft = run("draft", {"parse_ids": [str(pid)], "idea": "A planned experiment, no claimed results."})
    downloaded = signed_client.get("/api/v1/studio/artifacts/" + draft["artifact_version_id"])
    assert downloaded.status_code == 200
    with zipfile.ZipFile(BytesIO(downloaded.content)) as z:
        manifest = json.loads(z.read("evidence-manifest.json"))
        assert manifest["verification"] == "draft_unverified" and manifest["model_simulated"]
    diagram = run("diagram", {"nodes": ["Input", "Model", "Evidence"], "edges": [[0, 1], [1, 2]]})
    assert "N0 --> N1" in diagram["mermaid"]
    summary = run("summary", {"parse_ids": [str(pid)]})
    reviewed = run("review_artifact", {"artifact_version_id": summary["artifact_version_id"]})
    assert len(reviewed["sections"]) == 1 and reviewed["local_postprocessing_only"]
    assert reviewed["source_artifact_version_id"] == summary["artifact_version_id"]
    assert isinstance(reviewed["excluded_unrequested_section_titles"], list)
    with tenant_transaction(uuid4()) as conn:
        assert (
            conn.scalar(
                sa.select(T["artifact_versions"].c.id).where(
                    T["artifact_versions"].c.id == UUID(draft["artifact_version_id"])
                )
            )
            is None
        )


def test_metadata_contract_dedup_feedback_and_no_pdf_guess(case):
    owner, project = case
    xml = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1706.03762v1</id><title>Attention Is All You Need</title><summary>Transformer attention</summary><published>2017-06-12T00:00:00Z</published><updated>2017-06-12T00:00:00Z</updated><author><name>A. Author</name></author></entry></feed>"""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=xml))
    first = search(owner, project, "arxiv", "attention", transport=transport)
    second = search(owner, project, "arxiv", "attention", transport=transport)
    assert first[0]["source_record_id"] == second[0]["source_record_id"]
    assert first[0]["recommendation_id"] == second[0]["recommendation_id"]
    client = DiscoveryClient(httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html>no PDF</html>")))
    with pytest.raises(ContractError):
        client.pdf(first[0])
    client.close()


def test_feedback_changes_recommendation_order_and_explains_basis():
    from scholarship.domains.studio.discovery import personalize

    rows = [
        {
            "id": "a",
            "record_json": {"title": "attention transformer", "abstract": ""},
            "vote": 1,
            "score_json": {"lexical_overlap": 1},
            "reason_json": {},
        },
        {
            "id": "b",
            "record_json": {"title": "attention transformer architecture", "abstract": ""},
            "vote": None,
            "score_json": {"lexical_overlap": 1},
            "reason_json": {},
        },
        {
            "id": "c",
            "record_json": {"title": "protein laboratory", "abstract": ""},
            "vote": None,
            "score_json": {"lexical_overlap": 1},
            "reason_json": {},
        },
    ]
    ranked = personalize(rows)
    assert [r["id"] for r in ranked] == ["a", "b", "c"]
    assert ranked[1]["score_json"]["feedback_influence"] > ranked[2]["score_json"]["feedback_influence"]
    assert ranked[1]["reason_json"]["feedback_samples"] == 1


def test_long_source_notes_cached_between_summary_and_draft(case, signed_client):
    from test_live_business import bundle, source
    from scholarship.domains.mineru_native import import_native
    from scholarship.domains.retrieval_profile import digest

    owner, project = case
    vid, _ = imported(case, signed_client)
    original = "Repeated source with $x^2$ and exact evidence. " * 320
    pid = import_native(
        owner,
        vid,
        bundle([{"type": "text", "text": original, "page_idx": 0, "bbox": [100, 100, 900, 900]}]),
        expected_source_sha256=digest(source()),
    )
    before = None
    for operation in ["summary", "draft"]:
        job = submit(
            owner, project, uuid4().hex, operation, {"parse_ids": [str(pid)], "idea": "Planned experiment only"}
        )
        execute_offline(str(owner), str(project), str(job["id"]))
        with tenant_transaction(owner) as conn:
            saved = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == job["id"])).mappings().one()
            assert saved["status"] == "succeeded", saved["error_json"]
            total = conn.scalar(
                sa.text("SELECT count(*) FROM model_runtime.calls WHERE project_id=:p AND role='chat'"), {"p": project}
            )
        if before is not None:
            assert total - before == 1  # draft reuses all prior map receipts
        before = total


def test_metadata_subscription_due_lease_and_no_duplicate_recommendations(case):
    from scholarship.domains.studio.discovery import run_subscription

    owner, project = case
    with tenant_transaction(owner) as conn:
        sid = conn.scalar(
            T["subscriptions"]
            .insert()
            .values(
                owner_id=owner,
                project_id=project,
                name="test",
                sources_json=["arxiv"],
                query_json={"query": "attention"},
                schedule_json={"hours": 24},
                timezone="Asia/Shanghai",
                cursor_state={},
                next_due_at=sa.func.now(),
                enabled=True,
            )
            .returning(T["subscriptions"].c.id)
        )
    xml = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1706.03762v1</id><title>Attention</title><summary>Transformer</summary></entry></feed>'
    requests = []
    transport = httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(200, content=xml))
    first = run_subscription(owner, project, sid, transport=transport)
    assert first["status"] == "completed" and first["found"] == 1
    assert run_subscription(owner, project, sid, transport=transport) is None
    assert len(requests) == 1
    with tenant_transaction(owner) as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(T["recommendations"])
                .where(T["recommendations"].c.subscription_id == sid)
            )
            == 1
        )


def test_followup_search_reports_scope_without_claiming_problem_unsolved(case, signed_client):
    from scholarship.domains.studio.direction_checks import check

    owner, project = case
    _, pid = imported(case, signed_client)
    job = submit(
        owner,
        project,
        uuid4().hex,
        "direction_check",
        {"parse_ids": [str(pid)], "search_query": "attention future work"},
    )
    xml = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2501.12948v2</id><title>Later attention research</title><summary>A candidate abstract, not full-text proof.</summary><published>2025-01-22T00:00:00Z</published></entry></feed>'
    result = check(
        owner,
        project,
        job["id"],
        {"parse_ids": [str(pid)], "search_query": "attention future work"},
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=xml)),
    )
    assert result["problem_solved"] is None and not result["scientific_quality_verified"]
    assert result["model_requests"] == 0 and result["coverage"]["metadata_only"]
    assert result["candidates"][0]["assessment"] == "metadata_only_requires_fulltext"
    assert signed_client.get("/api/v1/studio/artifacts/" + result["artifact_version_id"]).status_code == 200


def test_graph_20_unrelated_papers_does_not_invent_edges(case):
    owner, project = case
    with tenant_transaction(owner) as conn:
        ids = [
            conn.scalar(
                T["papers"]
                .insert()
                .values(
                    owner_id=owner,
                    project_id=project,
                    title="Paper " + str(i),
                    normalized_title="paper " + str(i),
                    display_metadata={},
                )
                .returning(T["papers"].c.id)
            )
            for i in range(20)
        ]
    g = graph(owner, project, ids)
    assert len(g["nodes"]) == 20 and g["minimum_20_met"] and g["edges"] == []
    assert g["papers_with_active_parse"] == 0
    with pytest.raises(ContractError):
        graph(uuid4(), project, ids)


def test_segmented_translation_preserves_full_source_and_tokens():
    from scholarship.domains.studio.translation import batches

    source = "Research text $x^2$ and `code()` [1]. " * 300
    bid = uuid4()
    groups = batches([{"id": bid, "canonical_text": source}])
    items = [x for g in groups for x in g]
    assert len(items) > 1
    assert "".join(restore(x["text"], x["tokens"]) for x in items) == source
    assert items[0]["source_start"] == 0 and items[-1]["source_end"] == len(source)
    assert len({i["id"] for i in items}) == len(items)


def test_summary_unicode_windows_map_back_to_exact_pdf_blocks():
    from scholarship.domains.studio.anchors import source_map, locate
    from scholarship.domains.studio.workflow import source_windows

    rows = [
        {"id": uuid4(), "canonical_text": "原文 α $x^2$", "page_no": 1, "source_locator": {"pdf_bbox": [1, 2, 30, 40]}},
        {
            "id": uuid4(),
            "canonical_text": "第二段 β evidence",
            "page_no": 2,
            "source_locator": {"pdf_bbox": [2, 3, 40, 50]},
        },
    ]
    text, blocks = source_map(rows, uuid4(), uuid4())
    windows = source_windows(text, 12)
    assert "".join(w for _, w in windows) == text and all(len(w.encode()) <= 12 for _, w in windows)
    spans = locate(blocks, 3, len(text) - 2)
    assert spans[0]["page"] == 1 and spans[1]["page"] == 2
    assert "\n".join(s["quote"] for s in spans) == text[3:-2]


def test_plaintext_translation_keeps_literals_out_of_requests_and_stable_ids():
    from scholarship.domains.studio.translation_parts import assemble_parts, LITERALS, html_safe_groups

    source = "Use $x^2$ and `code()` [18]. https://example.org a@example.org <table><tr><td>Score</td><td>28.4</td></tr></table>\n```python\nx=1\n```"
    b = {"id": "block:0", "source": source}
    parts, layouts = assemble_parts([b])
    assert all(not LITERALS.search(p["text"]) for p in parts)
    values = {p["id"]: p["text"] for p in parts}
    rebuilt = "".join(v if k == "literal" else values[v] for k, v in layouts[b["id"]])
    assert rebuilt == source
    more, _ = assemble_parts([{"id": "other:0", "source": "Some other paragraph."}, b])
    assert [p["id"] for p in more if p["block_key"] == b["id"]] == [p["id"] for p in parts]
    long = "<table>" + ("<tr><td>value</td></tr>" * 150) + "</table>"
    bid = uuid4()
    rows = [{"id": bid, "canonical_text": long}]
    safe = html_safe_groups(
        __import__("scholarship.domains.studio.translation", fromlist=["batches"]).batches(rows), rows
    )
    assert "".join(b["source"] for g in safe for b in g) == long
    assert all(not b["source"].endswith("<") and not b["source"].endswith("<td") for g in safe for b in g)


def test_partial_translation_stops_but_preserves_valid_parts_for_next_explicit_run(case, signed_client, monkeypatch):
    from scholarship.domains.studio import workflow

    owner, project = case
    _, pid = imported(case, signed_client)
    # This fault response is OpenAI wire format; do not couple it to the app default.
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["projects"]
            .update()
            .where(T["projects"].c.id == project)
            .values(preferences={"model_configuration": {"preset": "free_api"}})
        )
    original_factory = workflow.transport_factory

    def partial(request):
        wire = json.loads(request.content)
        payload = json.loads(wire["messages"][-1]["content"])
        item = payload["segments"][0]
        content = json.dumps(
            {"segments": [{"id": item["id"], "text": "[模拟翻译] " + item["text"]}]}, ensure_ascii=False
        )
        return httpx.Response(
            200,
            json={
                "model": wire["model"],
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            },
        )

    monkeypatch.setattr(workflow, "transport_factory", lambda selection: lambda role: httpx.MockTransport(partial))
    bad = submit(owner, project, uuid4().hex, "translate", {"parse_id": str(pid)})
    execute_offline(str(owner), str(project), str(bad["id"]))
    with tenant_transaction(owner) as conn:
        failed = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == bad["id"])).mappings().one()
        assert (
            failed["status"] == "failed"
            and failed["error_json"]["contract_code"] == "incomplete_plaintext_translation_response"
        )
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(T["artifacts"])
                .where(T["artifacts"].c.kind == "translation_part", T["artifacts"].c.project_id == project)
            )
            == 1
        )
    monkeypatch.setattr(workflow, "transport_factory", original_factory)
    next_run = submit(owner, project, uuid4().hex, "translate", {"parse_id": str(pid)})
    execute_offline(str(owner), str(project), str(next_run["id"]))
    with tenant_transaction(owner) as conn:
        result = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == next_run["id"])).mappings().one()
        assert result["status"] == "succeeded", result["error_json"]
        assert (
            conn.scalar(
                sa.text("SELECT count(*) FROM model_runtime.calls WHERE role='translation' AND project_id=:p"),
                {"p": project},
            )
            == 2
        )


def test_production_scope_denies_other_parse_before_wire(case, database, monkeypatch):
    from test_live_business import production_case
    from scholarship.adapters.providers import ExternalDisabled

    owner, project, run, selection, policy, Gateway = production_case(case, database, monkeypatch)
    allowed = str(uuid4())
    other = str(uuid4())
    with database.begin() as conn:
        conn.execute(
            sa.text("UPDATE model_runtime.production_policies SET data_scope_json=CAST(:s AS jsonb) WHERE id=:id"),
            {"id": policy, "s": json.dumps({"parse_ids": [allowed]})},
        )
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["runs"]
            .update()
            .where(T["runs"].c.id == run)
            .values(kind="research_qa", input_scope_json={"generations": [{"parse_id": other}]})
        )
    sent = []
    with Gateway(
        owner, project, run, selection, transport_factory=lambda p: httpx.MockTransport(lambda r: sent.append(r))
    ) as gateway:
        with pytest.raises(ExternalDisabled):
            gateway.invoke("scope", "embedding", "embed", {"texts": ["not approved"]})
    assert not sent
    with tenant_transaction(owner) as conn:
        assert (
            conn.scalar(
                sa.text("SELECT requests_used FROM model_runtime.production_policies WHERE id=:id"), {"id": policy}
            )
            == 0
        )


def test_cloud_maintenance_requires_explicit_gate_without_reading_admin():
    import approve_cloud_business as maintenance

    assert maintenance.USER_APPROVED is False
    with pytest.raises(PermissionError):
        maintenance.issue({})
    with pytest.raises(PermissionError):
        maintenance.resume()


def test_followup_preserves_conversation_and_rejects_changed_scope(case, signed_client):
    from scholarship.domains.research import enqueue_research

    owner, project = case
    vid, _ = imported(case, signed_client)
    build = enqueue_research(owner, project, uuid4().hex, "research_build", [vid])
    execute_offline(str(owner), str(project), str(build["id"]))
    first = enqueue_research(owner, project, uuid4().hex, "research_qa", [vid], question="NVIDIA P100?")
    execute_offline(str(owner), str(project), str(first["id"]))
    followup = enqueue_research(
        owner, project, uuid4().hex, "research_qa", [vid], question="其中多少块 GPU？", parent_run_id=first["id"]
    )
    execute_offline(str(owner), str(project), str(followup["id"]))
    with tenant_transaction(owner) as conn:
        a = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == first["id"])).mappings().one()
        b = conn.execute(sa.select(T["runs"]).where(T["runs"].c.id == followup["id"])).mappings().one()
        assert b["status"] == "succeeded", b["error_json"]
        assert a["conversation_id"] == b["conversation_id"]
        messages = (
            conn.execute(
                sa.select(T["messages"])
                .where(T["messages"].c.conversation_id == a["conversation_id"])
                .order_by(T["messages"].c.ordinal)
            )
            .mappings()
            .all()
        )
        assert [m["ordinal"] for m in messages] == [0, 1, 2, 3]
        assert messages[2]["content_text"] == "其中多少块 GPU？"
        assert "NVIDIA P100?" in b["input_json"]["query_context"]
    history = signed_client.get(f"/api/v1/research/projects/{project}/runs").json()
    first_saved = next(r for r in history if r["id"] == str(first["id"]))
    next_saved = next(r for r in history if r["id"] == str(followup["id"]))
    assert first_saved["conversation_id"] == next_saved["conversation_id"]
    assert next_saved["parent_run_id"] == str(first["id"])
    assert next_saved["source_version_ids"] == [str(vid)]
    with pytest.raises(ContractError, match="conversation_advanced"):
        enqueue_research(owner, project, uuid4().hex, "research_qa", [vid], question="again", parent_run_id=first["id"])
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["projects"]
            .update()
            .where(T["projects"].c.id == project)
            .values(preferences={"model_configuration": {"preset": "mock", "overrides": {}}})
        )
    rebuild = enqueue_research(owner, project, uuid4().hex, "research_build", [vid])
    execute_offline(str(owner), str(project), str(rebuild["id"]))
    with pytest.raises(ContractError, match="same_source_scope"):
        enqueue_research(
            owner, project, uuid4().hex, "research_qa", [vid], question="changed scope", parent_run_id=followup["id"]
        )


def test_author_ordinal_uses_geometry_and_rejects_missing_front_matter():
    from scholarship.domains.structured_retrieval import ordered_authors, requested_author

    rows = [{"kind": "heading", "canonical_text": "Abstract", "bbox": [0, 500, 600, 520]}]
    for i, (x, y) in enumerate(
        [(10, 10), (150, 10), (290, 10), (430, 10), (10, 150), (150, 150), (290, 150), (10, 300)]
    ):
        rows.insert(
            0,
            {
                "kind": "paragraph",
                "canonical_text": f"Author {i + 1}\nInstitute {i + 1}\na{i + 1}@example.org",
                "bbox": [x, y, x + 100, y + 60],
            },
        )
    assert requested_author("第 6 作者是谁") == 6
    assert requested_author("第六位作者") == 6
    assert requested_author("the 6th author") == 6
    assert ordered_authors(rows)[5]["canonical_text"].startswith("Author 6\nInstitute 6")
    assert ordered_authors([r for r in rows if r["kind"] != "heading"]) == []


def test_quote_menu_preserves_latex_unicode_and_rejects_invalid_ids():
    from scholarship.models.adapters import resolve_quote_ids, resolve_quote_offsets
    from scholarship.adapters.providers import Evidence

    text = "Formula: $\\beta_1=0.9, \\beta_2=0.98$; 中文 Unicode. " * 7
    evidence = [Evidence(id="e1", text=text, page=1)]
    body = {
        "answer": "0.9 / 0.98",
        "claims": [{"text": "parameters", "citations": [{"evidence_id": "e1", "quote_id": "q1"}]}],
    }
    result = json.loads(resolve_quote_offsets(resolve_quote_ids(json.dumps(body), evidence), evidence))
    cite = result["claims"][0]["citations"][0]
    assert cite["start"] == 180 and cite["quote"] == text[180:360]
    compact = {
        "answer": {"β₁": 0.9, "β₂": 0.98},
        "insufficient_evidence": False,
        "claims": [{"evidence_id": "e1", "quote_id": "q0"}],
    }
    repaired = json.loads(resolve_quote_ids(json.dumps(compact), evidence))
    assert json.loads(repaired["answer"]) == compact["answer"]
    assert repaired["claims"][0]["citations"][0]["quote"] == text[:180]
    for bad in [
        None,
        3,
        {},
        {"claims": [None]},
        {"claims": [{"citations": [None]}]},
        {"claims": [{"citations": [{"evidence_id": "e2", "quote_id": "q0"}]}]},
    ]:
        with pytest.raises(ContractError):
            resolve_quote_ids(json.dumps(bad), evidence)


@pytest.mark.parametrize("score,previous,difference", [("30.5", "28.15", "2.35"), ("29.1", "28.15", "0.95")])
def test_table_calculator_uses_correct_metric_maximum_and_decimal(score, previous, difference):
    from scholarship.domains.table_calculation import compare_bleu

    text = (
        '<table><tr><td rowspan="2">Model</td><td colspan="2">BLEU</td><td>Cost</td></tr><tr><td>EN-DE</td><td>EN-FR</td><td>EN-DE</td></tr><tr><td>Old single</td><td>27.5</td><td>42.0</td><td>900</td></tr><tr><td>Old ensemble</td><td>'
        + previous
        + "</td><td>43.0</td><td>1000</td></tr><tr><td>Target (base)</td><td>29.0</td><td>40.0</td><td>100</td></tr><tr><td>Target (large)</td><td>"
        + score
        + "</td><td>41.0</td><td>200</td></tr></table>"
    )
    result = compare_bleu(text, "Target (large) English-to-German BLEU previous best difference?")
    assert result["baseline_score"] == previous and result["difference"] == difference
    assert result["baseline_labels"] == ["Old ensemble"]
    assert compare_bleu(text, "What is BLEU?") is None
    assert (
        compare_bleu(text, "Target (base) and Target (large) English-to-German BLEU previous best difference?") is None
    )
