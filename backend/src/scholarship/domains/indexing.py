"""Resumable manifest builds and PG-authoritative scoped hybrid retrieval."""

from contextlib import closing

from uuid import UUID, uuid5, NAMESPACE_URL
import sqlalchemy as sa
from qdrant_client import models as m

from scholarship.adapters.providers import ContractError
from scholarship.adapters.synthetic import adapters
from scholarship.domains.billing import OfflineExecutor
from scholarship.domains.deep_parse import version_scope
from scholarship.domains.retrieval_profile import PROFILE, PROFILE_KEY, PROFILE_HASH, canonical, digest, chunks, lexical
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.health import qdrant_client
from scholarship.infrastructure.schema import tables as T

# USER CONFIGURATION
RECALL = 40
RERANK_CANDIDATES = 20
EVIDENCE_LIMIT = 8


def call_step(owner, project, run, key, payload):
    h = digest(canonical(payload))
    with tenant_transaction(owner) as conn:
        conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"), {"k": str(run)})
        old = (
            conn.execute(
                sa.select(T["run_steps"]).where(T["run_steps"].c.run_id == run, T["run_steps"].c.step_key == key)
            )
            .mappings()
            .first()
        )
        if old:
            if old["input_sha256"] != h:
                raise ContractError("step_input_conflict")
            return old["id"]
        return conn.scalar(
            T["run_steps"]
            .insert()
            .values(
                owner_id=owner,
                project_id=project,
                run_id=run,
                step_key=key,
                attempt_no=1,
                tool_name="offline_contract",
                status="pending",
                input_sha256=h,
            )
            .returning(T["run_steps"].c.id)
        )


def finish_call(owner, step, receipt):
    with tenant_transaction(owner) as conn:
        conn.execute(
            T["run_steps"]
            .update()
            .where(T["run_steps"].c.id == step)
            .values(
                status="succeeded",
                output_json={"offline": True, "usage": receipt["usage"], "model": receipt["model"]},
                updated_at=sa.func.now(),
            )
        )


def profile(conn):
    p = (
        conn.execute(sa.select(T["index_profiles"]).where(T["index_profiles"].c.profile_key == PROFILE_KEY))
        .mappings()
        .one()
    )
    if p["fingerprint"] != PROFILE_HASH or p["config_json"] != PROFILE:
        raise ContractError("offline_profile_mismatch")
    return p


def block_rows(conn, pid):
    return (
        conn.execute(
            sa.select(T["document_blocks"], T["document_pages"].c.page_no)
            .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
            .where(T["document_blocks"].c.parse_id == pid)
            .order_by(T["document_pages"].c.page_no, T["document_blocks"].c.ordinal)
        )
        .mappings()
        .all()
    )


def prepare_generation(owner, vid, pid, profile_id=None):
    with tenant_transaction(owner) as conn:
        v = version_scope(conn, vid)
        conn.execute(sa.select(T["paper_versions"].c.id).where(T["paper_versions"].c.id == vid).with_for_update())
        p = (
            profile(conn)
            if profile_id is None
            else conn.execute(sa.select(T["index_profiles"]).where(T["index_profiles"].c.id == profile_id))
            .mappings()
            .one()
        )
        if profile_id is not None and p["config_json"].get("simulated") is not True:
            raise ContractError("simulation_profile_required")
        parse = (
            conn.execute(
                sa.select(T["document_parses"]).where(
                    T["document_parses"].c.id == pid,
                    T["document_parses"].c.version_id == vid,
                    T["document_parses"].c.status == "ready",
                    T["document_parses"].c.mode == "deep",
                )
            )
            .mappings()
            .first()
        )
        if not parse:
            raise ContractError("ready_synthetic_parse_required")
        old = (
            conn.execute(
                sa.select(T["index_generations"]).where(
                    T["index_generations"].c.parse_id == pid, T["index_generations"].c.profile_id == p["id"]
                )
            )
            .mappings()
            .first()
        )
        if old:
            return dict(old)
        scope = {"owner_id": owner, "project_id": v["project_id"]}
        number = 1 + conn.scalar(
            sa.select(sa.func.coalesce(sa.func.max(T["index_generations"].c.generation_no), 0)).where(
                T["index_generations"].c.version_id == vid
            )
        )
        collection = f"ss_p_{v['project_id'].hex}_{p['fingerprint'][:16]}"
        gid = conn.scalar(
            T["index_generations"]
            .insert()
            .values(
                **scope,
                version_id=vid,
                parse_id=pid,
                profile_id=p["id"],
                generation_no=number,
                collection_name=collection,
                status="building",
                expected_points=0,
                verified_points=0,
            )
            .returning(T["index_generations"].c.id)
        )
        manifest = []
        for b in block_rows(conn, pid):
            if b["kind"] == "other":
                continue
            for a, z, text, count in chunks(b["canonical_text"], b["kind"]):
                n = len(manifest)
                point = uuid5(NAMESPACE_URL, f"scholarship:{v['project_id']}:{gid}:{n}")
                cid = conn.scalar(
                    T["retrieval_chunks"]
                    .insert()
                    .values(
                        **scope,
                        generation_id=gid,
                        parse_id=pid,
                        chunk_no=n,
                        point_id=point,
                        canonical_text=text,
                        lexical_text=lexical(text),
                        content_sha256=digest(text),
                        token_count=count,
                        section_path=[],
                        block_kind=b["kind"],
                        page_start=b["page_no"],
                        page_end=b["page_no"],
                        representation="verbatim",
                    )
                    .returning(T["retrieval_chunks"].c.id)
                )
                conn.execute(
                    T["chunk_spans"]
                    .insert()
                    .values(
                        **scope,
                        chunk_id=cid,
                        parse_id=pid,
                        block_id=b["id"],
                        span_no=0,
                        block_start=a,
                        block_end=z,
                        chunk_start=0,
                        chunk_end=len(text),
                        representation="verbatim",
                    )
                )
                manifest.append({"point_id": str(point), "chunk_id": str(cid), "sha256": digest(text)})
        if not manifest:
            raise ContractError("no_indexable_blocks")
        conn.execute(
            T["index_generations"]
            .update()
            .where(T["index_generations"].c.id == gid)
            .values(expected_points=len(manifest), manifest_sha256=digest(canonical(manifest)))
        )
        return dict(
            conn.execute(sa.select(T["index_generations"]).where(T["index_generations"].c.id == gid)).mappings().one()
        )


def payload(g, c, v):
    return {
        "owner_id": str(g["owner_id"]),
        "project_id": str(g["project_id"]),
        "profile_id": str(g["profile_id"]),
        "paper_id": str(v["paper_id"]),
        "paper_version_id": str(g["version_id"]),
        "parse_revision_id": str(g["parse_id"]),
        "index_generation_id": str(g["id"]),
        "chunk_id": str(c["id"]),
        "content_sha256": c["content_sha256"],
        "block_kind": c["block_kind"],
        "page_start": c["page_start"],
        "page_end": c["page_end"],
        "lexical_text": c["lexical_text"],
        "synthetic": True,
    }


def ensure_collection(client, name, dimensions=1024):
    # Serialize create/index initialization across generations in the same project.
    if not client.collection_exists(name):
        client.create_collection(
            name,
            vectors_config={"dense": m.VectorParams(size=dimensions, distance=m.Distance.COSINE, on_disk=True)},
            on_disk_payload=True,
        )
    for key in [
        "owner_id",
        "project_id",
        "profile_id",
        "paper_version_id",
        "index_generation_id",
        "chunk_id",
        "content_sha256",
    ]:
        client.create_payload_index(name, key, m.PayloadSchemaType.KEYWORD, wait=True)
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


def build_index(owner, vid, pid, run, *, after_upsert=None, model_selection=None, model_client=None):
    profile_id = None
    expected_vectors = {}
    dimensions = 1024
    if model_selection is not None:
        from scholarship.models.runtime import ModelExecutor, register_embedding_profile
        from scholarship.models.adapters import ModelResult
        from scholarship.models.configuration import fingerprint

        if model_client is None or model_client.spec != model_selection.roles["embedding"]:
            raise ContractError("embedding_config_mismatch")
        with tenant_transaction(owner) as conn:
            version_scope(conn, vid)
            texts = [
                text
                for b in block_rows(conn, pid)
                if b["kind"] != "other"
                for _, _, text, _ in chunks(b["canonical_text"], b["kind"])
            ]
        if not texts:
            raise ContractError("no_indexable_blocks")
        executor_new = ModelExecutor(owner)
        probe_key = f"dimension:{pid}:{fingerprint(model_selection.snapshot())[:16]}"
        probe = executor_new.execute(
            run,
            probe_key,
            model_selection,
            "embedding",
            model_client,
            {"texts": [texts[0]], "input_kind": "document"},
            "embed",
        )
        result = ModelResult(**probe)
        profile_id = register_embedding_profile(owner, model_client.spec, result)
        dimensions = result.dimensions
    g = prepare_generation(owner, vid, pid, profile_id)
    if g["status"] == "ready":
        return g["id"]
    with tenant_transaction(owner) as conn:
        v = version_scope(conn, vid)
        rows = (
            conn.execute(
                sa.select(T["retrieval_chunks"])
                .where(T["retrieval_chunks"].c.generation_id == g["id"])
                .order_by(T["retrieval_chunks"].c.chunk_no)
            )
            .mappings()
            .all()
        )
    manifest = [{"point_id": str(c["point_id"]), "chunk_id": str(c["id"]), "sha256": c["content_sha256"]} for c in rows]
    if len(rows) != g["expected_points"] or digest(canonical(manifest)) != g["manifest_sha256"]:
        raise ContractError("pg_manifest_mismatch")
    embed, _, _ = adapters()
    executor = OfflineExecutor(owner)
    with closing(qdrant_client()) as client:
        with tenant_transaction(owner) as conn:
            conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"), {"k": g["collection_name"]})
            ensure_collection(client, g["collection_name"], dimensions)
        for offset in range(0, len(rows), 10):
            batch = rows[offset : offset + 10]
            arguments = {"texts": [c["canonical_text"] for c in batch]}
            if model_selection is None:
                step = call_step(owner, g["project_id"], run, f"index:{g['id']}:{offset}", arguments)
                receipt = executor.execute(
                    step,
                    f"index:{g['id']}:{offset}",
                    embed,
                    "embedding",
                    arguments,
                    maximum_input=sum(len(t.encode()) for t in arguments["texts"]),
                )
                finish_call(owner, step, receipt)
                vectors = receipt["result"]
            else:
                # The dimension probe already encoded this exact first chunk.
                cached = [probe["value"][0]] if offset == 0 and batch[0]["canonical_text"] == texts[0] else []
                remaining = arguments["texts"][len(cached) :]
                if remaining:
                    receipt = executor_new.execute(
                        run,
                        f"embed:{g['id']}:{offset}",
                        model_selection,
                        "embedding",
                        model_client,
                        {"texts": remaining, "expected_dimensions": dimensions, "input_kind": "document"},
                        "embed",
                    )
                    vectors = cached + receipt["value"]
                else:
                    vectors = cached
            for c, vec in zip(batch, vectors, strict=True):
                length = sum(n * n for n in vec) ** 0.5
                expected_vectors[str(c["point_id"])] = [n / length for n in vec]
            client.upsert(
                g["collection_name"],
                points=[
                    m.PointStruct(id=str(c["point_id"]), vector={"dense": vec}, payload=payload(g, c, v))
                    for c, vec in zip(batch, vectors, strict=True)
                ],
                wait=True,
            )
        if after_upsert:
            after_upsert(g)
        # Verify exact identities and all authoritative payload fields, not just counts.
        got = client.retrieve(
            g["collection_name"], ids=[str(c["point_id"]) for c in rows], with_payload=True, with_vectors=True
        )
        byid = {str(p.id): p for p in got}
        if len(byid) != len(rows):
            raise ContractError("index_manifest_count_mismatch")
        for c in rows:
            point = byid.get(str(c["point_id"]))
            expected = expected_vectors[str(c["point_id"])]
            if (
                not point
                or point.payload != payload(g, c, v)
                or len(point.vector.get("dense", [])) != dimensions
                or any(abs(a - b) > 1e-5 for a, b in zip(point.vector["dense"], expected, strict=True))
            ):
                raise ContractError("index_manifest_content_mismatch")
        scope = m.Filter(must=[m.FieldCondition(key="index_generation_id", match=m.MatchValue(value=str(g["id"])))])
        if client.count(g["collection_name"], count_filter=scope, exact=True).count != len(rows):
            raise ContractError("unexpected_generation_points")
    with tenant_transaction(owner) as conn:
        conn.execute(sa.select(T["paper_versions"].c.id).where(T["paper_versions"].c.id == vid).with_for_update())
        version_scope(conn, vid)
        current = (
            conn.execute(
                sa.select(T["index_generations"]).where(T["index_generations"].c.id == g["id"]).with_for_update()
            )
            .mappings()
            .one()
        )
        if current["status"] != "ready":
            conn.execute(
                T["index_generations"]
                .update()
                .where(T["index_generations"].c.id == g["id"])
                .values(status="ready", verified_points=len(rows), verified_at=sa.func.now())
            )
        # A late retry may not roll back a newer revision.
        active = conn.scalar(sa.select(T["paper_versions"].c.active_index_id).where(T["paper_versions"].c.id == vid))
        active_no = (
            conn.scalar(sa.select(T["index_generations"].c.generation_no).where(T["index_generations"].c.id == active))
            if active
            else 0
        )
        if active_no <= g["generation_no"]:
            conn.execute(
                T["paper_versions"]
                .update()
                .where(T["paper_versions"].c.id == vid)
                .values(active_parse_id=pid, active_index_id=g["id"])
            )
    return g["id"]


def receipt_vector(text):
    from scholarship.adapters.synthetic import vector

    return vector(text)


def snapshot(owner, project, versions, *, model_selection=None):
    with tenant_transaction(owner) as conn:
        p = profile(conn) if model_selection is None else None
        result = []
        for vid in sorted(set(versions), key=str):
            v = version_scope(conn, vid)
            if v["project_id"] != project or not v["active_index_id"]:
                raise ContractError("version_not_indexed_in_project")
            g = (
                conn.execute(
                    sa.select(T["index_generations"]).where(
                        T["index_generations"].c.id == v["active_index_id"],
                        T["index_generations"].c.parse_id == v["active_parse_id"],
                        sa.true() if p is None else T["index_generations"].c.profile_id == p["id"],
                        T["index_generations"].c.status == "ready",
                    )
                )
                .mappings()
                .first()
            )
            if not g:
                raise ContractError("matching_ready_generation_required")
            if model_selection is not None:
                from scholarship.models.catalog import binding_snapshot

                configured = model_selection.roles["embedding"]
                actual = (
                    conn.execute(sa.select(T["index_profiles"]).where(T["index_profiles"].c.id == g["profile_id"]))
                    .mappings()
                    .one()
                )
                if (
                    actual["provider"] != configured.provider
                    or actual["model_name"] != configured.model
                    or actual["config_json"].get("simulated") is not True
                    or actual["config_json"].get("adapter_binding") != binding_snapshot(configured, "embedding")
                    or (configured.dimensions is not None and actual["dimensions"] != configured.dimensions)
                ):
                    raise ContractError("embedding_profile_requires_rebuild")
            result.append({k: str(g[k]) for k in ["id", "version_id", "parse_id", "profile_id", "collection_name"]})
        return result


def hybrid(owner, project, scope, question, query_vector):
    if not scope:
        return {"candidates": [], "trace": []}
    from scholarship.adapters.providers import finite

    if len({g["profile_id"] for g in scope}) != 1:
        raise ContractError("mixed_embedding_profiles")
    with tenant_transaction(owner) as conn:
        dimensions = conn.scalar(
            sa.select(T["index_profiles"].c.dimensions).where(T["index_profiles"].c.id == UUID(scope[0]["profile_id"]))
        )
    if (
        not isinstance(query_vector, list)
        or len(query_vector) != dimensions
        or not any(finite(x) for x in query_vector)
    ):
        raise ContractError("query_vector_profile_mismatch")
    # Validate every component, including values after the first non-zero one.
    for value in query_vector:
        finite(value)
    trace = []
    candidates = []
    with closing(qdrant_client()) as client:
        for g in scope:
            expected = {
                "owner_id": str(owner),
                "project_id": str(project),
                "profile_id": g["profile_id"],
                "paper_version_id": g["version_id"],
                "index_generation_id": g["id"],
                "parse_revision_id": g["parse_id"],
            }
            f = m.Filter(must=[m.FieldCondition(key=k, match=m.MatchValue(value=v)) for k, v in expected.items()])
            dense = client.query_points(
                g["collection_name"], query=query_vector, using="dense", query_filter=f, limit=RECALL, with_payload=True
            ).points
            sparse = client.query_points(
                g["collection_name"],
                query=m.TextQuery(text=lexical(question)),
                using="lexical_text",
                query_filter=f,
                limit=RECALL,
                with_payload=True,
            ).points
            scores = {}
            points = {}
            for group in [dense, sparse]:
                for rank, point in enumerate(group):
                    key = str(point.id)
                    points[key] = point
                    scores[key] = scores.get(key, 0) + 1 / (60 + rank + 1)
            trace.append(
                {
                    "generation_id": g["id"],
                    "dense": [{"point": str(p.id), "score": p.score} for p in dense],
                    "bm25": [{"point": str(p.id), "score": p.score} for p in sparse],
                }
            )
            with tenant_transaction(owner) as conn:
                v = version_scope(conn, UUID(g["version_id"]))
                valid = (
                    conn.execute(
                        sa.select(T["index_generations"]).where(
                            T["index_generations"].c.id == UUID(g["id"]),
                            T["index_generations"].c.project_id == project,
                            T["index_generations"].c.version_id == v["id"],
                            T["index_generations"].c.parse_id == UUID(g["parse_id"]),
                            T["index_generations"].c.profile_id == UUID(g["profile_id"]),
                            T["index_generations"].c.collection_name == g["collection_name"],
                            T["index_generations"].c.status == "ready",
                        )
                    )
                    .mappings()
                    .first()
                )
                if not valid:
                    raise ContractError("scope_generation_unavailable")
                ids = [UUID(p.payload["chunk_id"]) for p in points.values()]
                rows = (
                    conn.execute(
                        sa.select(T["retrieval_chunks"]).where(
                            T["retrieval_chunks"].c.id.in_(ids), T["retrieval_chunks"].c.generation_id == UUID(g["id"])
                        )
                    )
                    .mappings()
                    .all()
                    if ids
                    else []
                )
                for c in rows:
                    pt = points.get(str(c["point_id"]))
                    if (
                        not pt
                        or pt.payload != payload(valid, c, v)
                        or c["content_sha256"] != digest(c["canonical_text"])
                    ):
                        continue
                    spans = (
                        conn.execute(
                            sa.select(
                                T["chunk_spans"],
                                T["document_blocks"].c.canonical_text.label("original"),
                                T["document_blocks"].c.bbox,
                            )
                            .join(T["document_blocks"], T["document_blocks"].c.id == T["chunk_spans"].c.block_id)
                            .where(T["chunk_spans"].c.chunk_id == c["id"])
                        )
                        .mappings()
                        .all()
                    )
                    if len(spans) != 1:
                        continue
                    s = spans[0]
                    if s["original"][s["block_start"] : s["block_end"]] != c["canonical_text"]:
                        raise ContractError("chunk_original_span_mismatch")
                    candidates.append(
                        {
                            "id": str(c["id"]),
                            "text": c["canonical_text"],
                            "page": c["page_start"],
                            "version_id": str(v["id"]),
                            "parse_id": g["parse_id"],
                            "generation_id": g["id"],
                            "block_id": str(s["block_id"]),
                            "block_start": s["block_start"],
                            "bbox": s["bbox"],
                            "rrf": scores[str(c["point_id"])],
                        }
                    )
    # Round-robin by source gives multi-paper evidence a bounded share.
    groups = {
        g["id"]: sorted([c for c in candidates if c["generation_id"] == g["id"]], key=lambda c: (-c["rrf"], c["id"]))
        for g in scope
    }
    merged = []
    while len(merged) < RERANK_CANDIDATES and any(groups.values()):
        for group in groups.values():
            if group and len(merged) < RERANK_CANDIDATES:
                merged.append(group.pop(0))
    return {"candidates": merged, "trace": trace}
