"""Local, explicit editorial derivations; original model artifacts stay immutable."""

from copy import deepcopy
from io import BytesIO
from uuid import UUID
import re
import zipfile
import sqlalchemy as sa
from scholarship.adapters.providers import ContractError
from scholarship.domains.deep_parse import version_scope
from scholarship.domains.structured_retrieval import ordered_authors
from scholarship.domains.retrieval_profile import canonical
from scholarship.domains.studio.artifacts import publish, write_member
from scholarship.domains.studio.anchors import source_map, locate
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T


def source_bibliography(conn, project, pid, fallback):
    p = (
        conn.execute(
            sa.select(T["document_parses"]).where(
                T["document_parses"].c.id == pid,
                T["document_parses"].c.project_id == project,
                T["document_parses"].c.status == "ready",
            )
        )
        .mappings()
        .first()
    )
    if not p:
        raise ContractError("bibliographic_source_unavailable")
    version_scope(conn, p["version_id"])
    rows = (
        conn.execute(
            sa.select(T["document_blocks"])
            .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
            .where(T["document_blocks"].c.parse_id == pid, T["document_pages"].c.page_no == 1)
            .order_by(T["document_blocks"].c.ordinal)
        )
        .mappings()
        .all()
    )
    authors = ordered_authors(rows)
    headings = [
        r
        for r in rows
        if r["kind"] in {"title", "heading"}
        and r["bbox"]
        and r["canonical_text"].strip().casefold() not in {"abstract", "introduction"}
    ]
    title = min(headings, key=lambda r: r["bbox"][1]) if headings else None
    footer = [r for r in rows if re.search(r"arxiv\s*:", r["canonical_text"], re.I)]
    if not title or not authors or len(footer) != 1:
        return {**fallback, "identity_status": "local_file_identity_unverified", "external_identity_verified": False}
    aid = re.search(r"arxiv\s*:\s*(\d{4}\.\d{4,5}(?:v\d+)?)", footer[0]["canonical_text"], re.I)
    year = re.search(r"\b(?:19|20)\d{2}\b(?!\.)", footer[0]["canonical_text"])
    names = [re.split(r"\\\*|\$|\n", a["canonical_text"], maxsplit=1)[0].strip() for a in authors]
    return {
        **fallback,
        "title": title["canonical_text"],
        "authors": names,
        "year": int(year[0]) if year else None,
        "arxiv_id": aid[1] if aid else None,
        "url": "https://arxiv.org/abs/" + aid[1] if aid else None,
        "identity_status": "native_front_matter_self_reported",
        "external_identity_verified": False,
        "metadata_block_ids": [str(title["id"])] + [str(a["id"]) for a in authors] + [str(footer[0]["id"])],
    }


def review(owner, project, run, inputs):
    source_id = UUID(inputs["artifact_version_id"])
    with tenant_transaction(owner) as conn:
        source = (
            conn.execute(
                sa.select(T["artifact_versions"], T["artifacts"].c.kind)
                .join(T["artifacts"], T["artifacts"].c.id == T["artifact_versions"].c.artifact_id)
                .where(
                    T["artifact_versions"].c.id == source_id,
                    T["artifacts"].c.project_id == project,
                    T["artifacts"].c.deleted_at.is_(None),
                )
            )
            .mappings()
            .first()
        )
        if not source or source["kind"] not in {"summary", "draft"}:
            raise ContractError("review_requires_summary_or_draft_artifact")
        output = deepcopy(source["metadata_json"])
        if source["kind"] == "summary":
            keep = [
                (i, s)
                for i, s in enumerate(output.get("sections", []))
                if s["title"].strip().casefold()
                in {
                    "摘要",
                    "abstract",
                    "summary",
                    "source-grounded notes",
                    "source-grounded summary",
                    "来源摘要",
                    "来源笔记",
                }
            ]
            if len(keep) != 1:
                raise ContractError("summary_section_ambiguous_requires_manual_review")
            removed = [s["title"] for i, s in enumerate(output["sections"]) if i != keep[0][0]]
            output["sections"] = [keep[0][1]]
            output["excluded_unrequested_section_titles"] = removed
            output["hypotheses"] = []
        output["bibliography"] = [
            source_bibliography(conn, project, UUID(b["source_id"]), b) for b in output.get("bibliography", [])
        ]
        maps = {}
        for window in output.get("summary_spans", []):
            pid = UUID(window["parse_id"])
            if pid not in maps:
                parse = (
                    conn.execute(
                        sa.select(T["document_parses"]).where(
                            T["document_parses"].c.id == pid, T["document_parses"].c.project_id == project
                        )
                    )
                    .mappings()
                    .first()
                )
                if not parse:
                    raise ContractError("review_evidence_scope_mismatch")
                version_scope(conn, parse["version_id"])
                rows = (
                    conn.execute(
                        sa.select(T["document_blocks"], T["document_pages"].c.page_no)
                        .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
                        .where(T["document_blocks"].c.parse_id == pid)
                        .order_by(T["document_pages"].c.page_no, T["document_blocks"].c.ordinal)
                    )
                    .mappings()
                    .all()
                )
                text, blocks = source_map(rows, parse["version_id"], pid)
                maps[pid] = (text, blocks)
            text, blocks = maps[pid]
            for anchor in window["anchors"]:
                if text[anchor["start"] : anchor["end"]] != anchor["quote"]:
                    raise ContractError("review_quote_changed")
                anchor["block_spans"] = locate(blocks, anchor["start"], anchor["end"])
    output.update(
        source_artifact_version_id=str(source_id),
        local_postprocessing_only=True,
        new_model_requests=0,
        verification="draft_unverified",
        review_notice="保留原模型初稿；本版本只整理已保存摘要段与原件自报文献信息。未做独立外部身份认证或逐条科学主张验证。",
    )
    md = "# " + ("论文摘要" if source["kind"] == "summary" else "论文框架") + "\n\n"
    md += "\n\n".join(
        "## " + s["title"] + "\n\n" + s["text"] + "\n\n来源修订：" + ", ".join(s["source_ids"])
        for s in output["sections"]
    )
    md += "\n\n## References\n\n"
    bibtex = []

    def escape(value):
        return str(value).replace("\\", "\\textbackslash{}").replace("{", "\\{").replace("}", "\\}").replace("%", "\\%")

    for i, b in enumerate(output["bibliography"]):
        authors = ", ".join(b.get("authors", [])) or "作者待核验"
        md += f"{authors} ({b.get('year') or '年份待核验'}). {b['title']}. {b.get('url') or '本地固定版本'}\n\n"
        if b.get("authors") and b.get("year"):
            fields = {"title": b["title"], "author": " and ".join(b["authors"]), "year": b["year"]}
            if b.get("arxiv_id"):
                fields.update(eprint=b["arxiv_id"], archivePrefix="arXiv", url=b["url"])
            bibtex.append(
                "@misc{scholarship"
                + str(i)
                + ",\n"
                + ",\n".join("  " + k + " = {" + escape(v) + "}" for k, v in fields.items())
                + "\n}"
            )
    md += "\n" + output["review_notice"] + "\n"
    raw = BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        write_member(z, "draft.md", md)
        write_member(z, "evidence-manifest.json", canonical(output))
        write_member(z, "references.bib", "\n\n".join(bibtex) + "\n")
    with tenant_transaction(owner) as conn:
        aid = publish(
            conn,
            owner,
            project,
            run,
            "review_artifact",
            "reviewed-" + source["kind"] + ".zip",
            raw.getvalue(),
            metadata=output,
            inputs=[{"role": "original_model_artifact", "source_artifact_version_id": source_id}]
            + [{"role": "source", "source_parse_id": UUID(b["source_id"])} for b in output["bibliography"]],
        )
        conn.execute(
            T["audit_events"]
            .insert()
            .values(
                owner_id=owner,
                project_id=project,
                action="research_artifact_source_review",
                subject_json={"run_id": str(source["producing_run_id"]), "artifact_version_id": str(source_id)},
                safe_payload={
                    "verdict": "editorial_derivative_available",
                    "reason": "原输出混有未请求的计划章节；已另存只含摘要的整理版。"
                    if source["kind"] == "summary"
                    else "已另存从原件作者区与页首信息整理的 References；仍待独立身份核验。",
                    "reviewed_artifact_version_id": str(aid),
                },
            )
        )
    return {**output, "artifact_version_id": str(aid)}
