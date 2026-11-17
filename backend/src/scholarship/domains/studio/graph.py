"""A reproducible selected-paper citation graph; unknown relationships stay absent."""

import re
import networkx as nx
import numpy as np
import sqlalchemy as sa
from scholarship.adapters.providers import ContractError
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T


def canonical_identifier(scheme, value):
    value = value.casefold().strip()
    if scheme == "doi":
        return "doi:" + value.removeprefix("https://doi.org/").removeprefix("http://dx.doi.org/")
    if scheme == "arxiv":
        return "arxiv:" + re.sub(r"v\d+$", "", value.removeprefix("https://arxiv.org/abs/"))
    return scheme + ":" + value


def graph(owner, project, papers):
    chosen = sorted(set(papers), key=str)
    if not 1 <= len(chosen) <= 100:
        raise ContractError("graph_requires_1_to_100_papers")
    with tenant_transaction(owner) as conn:
        rows = (
            conn.execute(
                sa.select(T["papers"]).where(
                    T["papers"].c.project_id == project,
                    T["papers"].c.id.in_(chosen),
                    T["papers"].c.deleted_at.is_(None),
                )
            )
            .mappings()
            .all()
        )
        if len(rows) != len(chosen):
            raise ContractError("graph_paper_scope_mismatch")
        identities = {}
        linked_identifiers = set()
        for row in conn.execute(
            sa.select(T["paper_identifiers"]).where(
                T["paper_identifiers"].c.project_id == project, T["paper_identifiers"].c.paper_id.in_(chosen)
            )
        ).mappings():
            identities.setdefault(canonical_identifier(row["scheme"], row["normalized_value"]), set()).add(
                row["paper_id"]
            )
            if row["source_record_id"]:
                linked_identifiers.add(row["paper_id"])
        versions = (
            conn.execute(
                sa.select(T["paper_versions"]).where(
                    T["paper_versions"].c.paper_id.in_(chosen), T["paper_versions"].c.deleted_at.is_(None)
                )
            )
            .mappings()
            .all()
        )
        parses = {}
        for v in versions:
            pid = v["active_parse_id"] or conn.scalar(
                sa.select(T["document_parses"].c.id)
                .where(
                    T["document_parses"].c.version_id == v["id"],
                    T["document_parses"].c.status == "ready",
                    T["document_parses"].c.mode == "deep",
                )
                .order_by(T["document_parses"].c.revision_no.desc())
                .limit(1)
            )
            if pid:
                parses[pid] = v["paper_id"]
        edges = []
        if parses:
            from scholarship.domains.studio.reference_reviews import apply_reviews

            refs = conn.execute(
                sa.select(T["bibliographic_refs"]).where(T["bibliographic_refs"].c.parse_id.in_(list(parses)))
            ).mappings()
            for ref in apply_reviews(conn, refs):
                targets = set()
                if ref["resolution_state"] == "verified" and ref["resolved_paper_id"] in chosen:
                    targets.add(ref["resolved_paper_id"])
                for doi in (
                    []
                    if ref["resolution_state"] == "verified"
                    else re.findall(r"10\.\d{4,9}/[^\s<>]+", ref["raw_reference"])
                ):
                    targets |= identities.get(canonical_identifier("doi", doi.rstrip(".,;)")), set())
                for aid in (
                    []
                    if ref["resolution_state"] == "verified"
                    else re.findall(r"(?:arXiv:|arxiv\.org/abs/)(\d{4}\.\d{4,5}(?:v\d+)?)", ref["raw_reference"], re.I)
                ):
                    targets |= identities.get(canonical_identifier("arxiv", aid), set())
                if len(targets) == 1:
                    target = next(iter(targets))
                    source = parses[ref["parse_id"]]
                    if source != target:
                        edges.append(
                            {
                                "source": str(source),
                                "target": str(target),
                                "reference_id": str(ref["id"]),
                                "block_id": str(ref["entry_block_id"]) if ref["entry_block_id"] else None,
                                "parse_id": str(ref["parse_id"]),
                                "basis": "explicit_identifier_or_verified_resolution",
                                "identity_confidence": "verified_resolution"
                                if ref["resolution_state"] == "verified"
                                else "source_linked_identifier"
                                if target in linked_identifiers
                                else "local_identifier_candidate_needs_review",
                            }
                        )
    g = nx.DiGraph()
    g.add_nodes_from(str(p) for p in chosen)
    g.add_edges_from((e["source"], e["target"]) for e in edges)
    order = list(g.nodes)
    matrix = nx.to_numpy_array(g, nodelist=order, dtype=float)
    totals = matrix.sum(axis=1)
    for i, total in enumerate(totals):
        matrix[i, :] = matrix[i, :] / total if total else 1 / len(order)
    rank = np.full(len(order), 1 / len(order))
    for _ in range(300):
        updated = 0.85 * (rank @ matrix) + 0.15 / len(order)
        if np.abs(updated - rank).sum() < 1e-12:
            rank = updated
            break
        rank = updated
    else:
        raise ContractError("pagerank_did_not_converge")
    ranks = dict(zip(order, map(float, rank), strict=True))
    return {
        "nodes": [
            {
                "id": str(r["id"]),
                "title": r["title"],
                "pagerank": ranks[str(r["id"])],
                "in_degree": g.in_degree(str(r["id"])),
            }
            for r in rows
        ],
        "edges": edges,
        "scope": "selected_local_papers_only",
        "minimum_20_met": len(chosen) >= 20,
        "papers_with_active_parse": len(set(parses.values())),
        "unknown_edges_not_invented": True,
        "candidate_identity_edges": sum(
            e["identity_confidence"] == "local_identifier_candidate_needs_review" for e in edges
        ),
        "algorithm": "networkx-graph-numpy-pagerank-0.85-v1",
        "limitations": "局域中心性不是全局学术影响力；没有边可能是未解析或未完成文献身份核验。",
    }
