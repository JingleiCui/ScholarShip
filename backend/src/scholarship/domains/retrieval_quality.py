"""Source-preserving prose windows and explicit retrieval quality checks."""

import re
from scholarship.domains.retrieval_profile import chunks


def substantive(text, kind="paragraph"):
    if kind in {"heading", "other", "formula"}:
        return False
    if kind == "table":
        return len(text) >= 40
    clean = re.sub(r"\(cid:\d+\)", "", text)
    words = len(re.findall(r"[A-Za-z]{2,}", clean))
    return (
        words >= 6
        or (words >= 5 and len(clean) >= 25 and bool(re.search(r"\d", clean)))
        or len(re.findall(r"[\u3400-\u9fff]", clean)) >= 16
    )


def index_chunks(block):
    text, kind = block["canonical_text"], block["kind"]
    policy = block.get("structured_json", {}).get("pipeline_policy", {})
    if policy.get("revision") != "local-regions-ocr-v3":
        yield from chunks(text, kind)
        return
    if not substantive(text, kind):
        return
    if kind in {"table", "formula", "code"}:
        yield from chunks(text, kind)
        return
    yield from chunks(text, kind)


FACETS = [
    (
        "problem",
        r"问题|目标|动机|目的|problem|motivation|objective",
        "research problem motivation objective",
        r"abstract|introduction|motivation",
    ),
    (
        "contribution",
        r"创新|贡献|特点|contribution|novel",
        "novel contribution proposed approach advantages",
        r"abstract|introduction|conclusion",
    ),
    (
        "method",
        r"方法|机制|原理|实现|method|approach|mechanism",
        "proposed method algorithm architecture mechanism",
        r"method|approach|model|normalization|architecture",
    ),
    (
        "experiment",
        r"实验|评估|指标|数据集|experiment|evaluation|dataset",
        "experiments evaluation datasets metrics baseline results",
        r"experiment|evaluation|result|empirical",
    ),
    (
        "limitation",
        r"局限|限制|不足|limitation",
        "limitations future work assumptions",
        r"limitation|conclusion|discussion",
    ),
]


def query_plan(question):
    facets = [name for name, pattern, _, _ in FACETS if re.search(pattern, question, re.I)]
    if not facets and re.search(r"概括|总结|介绍|summari|overview", question, re.I):
        facets = ["problem", "method", "experiment"]
    expansions = [query for name, _, query, _ in FACETS if name in facets]
    # Query expansions contain research vocabulary, never invented paper facts.
    return {"original": question, "facets": facets, "queries": [question, *expansions[:4]]}
