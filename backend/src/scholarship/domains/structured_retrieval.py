"""Explicit ordinal extraction from small, unambiguous front-matter author blocks."""

import re
from statistics import median
from uuid import UUID
import sqlalchemy as sa
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.domains.deep_parse import version_scope
from scholarship.domains.retrieval_profile import digest
from scholarship.adapters.providers import ContractError


def requested_author(question):
    match = re.search(r"第\s*([0-9一二三四五六七八九十]+)\s*(?:位|个)?\s*作者", question)
    if match:
        token = match[1]
        if token.isdigit():
            return int(token)
        return {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}.get(token)
    match = re.search(r"\b(\d+)(?:st|nd|rd|th)\s+author\b", question, re.I)
    return int(match[1]) if match else None


def ordered_authors(rows):
    # Do not infer completeness from arbitrary prose or mixed email blocks.
    headings = [
        r["bbox"][1] for r in rows if r["bbox"] and re.fullmatch(r"abstract", r["canonical_text"].strip(), re.I)
    ]
    if not headings:
        return []
    cutoff = min(headings)
    candidates = [
        r
        for r in rows
        if r["kind"] in {"paragraph", "author"}
        and r["bbox"]
        and r["bbox"][3] < cutoff
        and len(re.findall(r"[^\s@]+@[^\s@]+", r["canonical_text"])) == 1
        and len(r["canonical_text"]) < 700
    ]
    if not 2 <= len(candidates) <= 40:
        return []
    tolerance = max(3, median(r["bbox"][3] - r["bbox"][1] for r in candidates) * 0.4)
    lines = []
    for row in sorted(candidates, key=lambda r: r["bbox"][1]):
        if not lines or abs(row["bbox"][1] - lines[-1][0]["bbox"][1]) > tolerance:
            lines.append([row])
        else:
            lines[-1].append(row)
    return [r for line in lines for r in sorted(line, key=lambda r: r["bbox"][0])]


def author_evidence(owner, project, scope, question):
    ordinal = requested_author(question)
    if ordinal is None or len(scope) != 1:
        return None
    source = scope[0]
    with tenant_transaction(owner) as conn:
        version = version_scope(conn, UUID(source["version_id"]))
        if version["project_id"] != project:
            raise ContractError("structured_evidence_scope")
        valid = conn.scalar(
            sa.select(T["index_generations"].c.id).where(
                T["index_generations"].c.id == UUID(source["id"]),
                T["index_generations"].c.version_id == version["id"],
                T["index_generations"].c.parse_id == UUID(source["parse_id"]),
                T["index_generations"].c.project_id == project,
                T["index_generations"].c.status == "ready",
            )
        )
        if not valid:
            raise ContractError("structured_generation_unavailable")
        rows = (
            conn.execute(
                sa.select(T["document_blocks"])
                .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
                .where(T["document_blocks"].c.parse_id == UUID(source["parse_id"]), T["document_pages"].c.page_no == 1)
            )
            .mappings()
            .all()
        )
        authors = ordered_authors(rows)
        if not 1 <= ordinal <= len(authors):
            return None
        block = authors[ordinal - 1]
        chunk = (
            conn.execute(
                sa.select(T["retrieval_chunks"], T["chunk_spans"].c.block_start, T["chunk_spans"].c.block_end)
                .join(T["chunk_spans"], T["chunk_spans"].c.chunk_id == T["retrieval_chunks"].c.id)
                .where(
                    T["retrieval_chunks"].c.generation_id == UUID(source["id"]),
                    T["chunk_spans"].c.block_id == block["id"],
                    T["chunk_spans"].c.block_start == 0,
                )
            )
            .mappings()
            .first()
        )
        if (
            not chunk
            or chunk["canonical_text"] != block["canonical_text"]
            or chunk["content_sha256"] != digest(block["canonical_text"])
        ):
            return None
        item = {
            "id": str(chunk["id"]),
            "text": chunk["canonical_text"],
            "page": 1,
            "version_id": source["version_id"],
            "parse_id": source["parse_id"],
            "generation_id": source["id"],
            "block_id": str(block["id"]),
            "block_start": 0,
            "bbox": block["bbox"],
            "rrf": 0,
        }
        display = re.sub(r"\S+@\S+", "", block["canonical_text"])
        display = re.sub(r"\$[^$]*\$|\\\*", "", display)
        display = " ".join(display.split())
        selected = [item]
        citations = [{"evidence_id": item["id"], "quote": item["text"], "start": 0, "end": len(item["text"])}]
        if re.search(r"dagger|†", block["canonical_text"]):
            notes = [
                r
                for r in rows
                if r["id"] != block["id"]
                and re.search(r"dagger|†", r["canonical_text"])
                and re.search(r"work performed|affiliation", r["canonical_text"], re.I)
                and len(r["canonical_text"]) < 400
            ]
            if len(notes) == 1:
                note = notes[0]
                footnote = {
                    **item,
                    "id": "block:" + str(note["id"]),
                    "text": note["canonical_text"],
                    "block_id": str(note["id"]),
                    "bbox": note["bbox"],
                    "retrieval_mode": "author_affiliation_footnote",
                }
                selected.append(footnote)
                citations.append(
                    {"evidence_id": footnote["id"], "quote": footnote["text"], "start": 0, "end": len(footnote["text"])}
                )
                display += "；首页工作地点注记：" + re.sub(r"\$[^$]*\$", "", note["canonical_text"]).strip()
        return {
            "selected": selected,
            "answer": {
                "answer": f"第 {ordinal} 位作者及署名机构（按首页作者区逐行从左至右）：{display}",
                "insufficient_evidence": False,
                "claims": [
                    {
                        "text": display,
                        "citations": citations,
                    }
                ],
                "method": "deterministic_author_order",
            },
            "trace": [
                {
                    "method": "front-matter-email-blocks-row-major-v1",
                    "author_count": len(authors),
                    "requested_ordinal": ordinal,
                    "block_ids": [str(a["id"]) for a in authors],
                    "limitation": "只对摘要前独立含邮箱作者块进行几何排序；不把语义相似度当作者次序。",
                }
            ],
        }


def table_evidence(owner, project, scope, question):
    """Supplement dense chunks with intact, bounded native tables; never relabel as vectors."""
    from scholarship.domains.retrieval_profile import lexical

    terms = set(lexical(question).split())
    result = []
    with tenant_transaction(owner) as conn:
        for source in scope:
            valid = conn.scalar(
                sa.select(T["index_generations"].c.id).where(
                    T["index_generations"].c.id == UUID(source["id"]),
                    T["index_generations"].c.project_id == project,
                    T["index_generations"].c.version_id == UUID(source["version_id"]),
                    T["index_generations"].c.parse_id == UUID(source["parse_id"]),
                    T["index_generations"].c.status == "ready",
                )
            )
            if not valid:
                raise ContractError("structured_generation_unavailable")
            rows = conn.execute(
                sa.select(T["document_blocks"], T["document_pages"].c.page_no)
                .join(T["document_pages"], T["document_pages"].c.id == T["document_blocks"].c.page_id)
                .where(
                    T["document_blocks"].c.parse_id == UUID(source["parse_id"]), T["document_blocks"].c.kind == "table"
                )
            ).mappings()
            for b in rows:
                text = b["canonical_text"]
                score = len(terms & set(lexical(text).split()))
                if not score or not 1 <= len(text.encode()) <= 4000 or b["content_sha256"] != digest(text):
                    continue
                result.append(
                    {
                        "id": "block:" + str(b["id"]),
                        "text": text,
                        "page": b["page_no"],
                        "version_id": source["version_id"],
                        "parse_id": source["parse_id"],
                        "generation_id": source["id"],
                        "block_id": str(b["id"]),
                        "block_start": 0,
                        "bbox": b["bbox"],
                        "rrf": 0,
                        "structural_score": score,
                        "retrieval_mode": "native_table_lexical_supplement",
                    }
                )
    return sorted(result, key=lambda b: (-b["structural_score"], b["id"]))[:4]


def table_comparison_evidence(owner, project, scope, question):
    from scholarship.domains.table_calculation import compare_bleu

    found = []
    for item in table_evidence(owner, project, scope, question):
        calculation = compare_bleu(item["text"], question)
        if calculation:
            found.append((item, calculation))
    if len(found) != 1:
        return None
    item, calculation = found[0]
    answer = f"{calculation['target']} 在 {calculation['direction']} 上为 {calculation['target_score']} BLEU；此前表中最高为 {' / '.join(calculation['baseline_labels'])} 的 {calculation['baseline_score']} BLEU（包含集成模型）。差值：{calculation['target_score']} − {calculation['baseline_score']} = {calculation['difference']} BLEU 分。"
    return {
        "selected": [item],
        "answer": {
            "answer": answer,
            "insufficient_evidence": False,
            "claims": [
                {
                    "text": answer,
                    "citations": [
                        {"evidence_id": item["id"], "quote": item["text"], "start": 0, "end": len(item["text"])}
                    ],
                }
            ],
            "method": "deterministic_table_decimal",
        },
        "trace": [calculation],
    }
