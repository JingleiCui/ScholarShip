"""Offline proposal calculator. No HTTP, SDK clients, credentials or database."""

from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import json

# USER CONFIGURATION: proposed trial only; not a spending authorization.
ROOT = Path(__file__).resolve().parent
QUESTIONS = 30
QUESTION_TOKENS = 512
LLM_INPUT_PER_QUESTION = 12000
LLM_OUTPUT_PER_QUESTION = 2048
DOCUMENT_EMBEDDING_TOKENS = 480000
RERANK_DOCUMENTS = 20
CHUNK_TOKENS = 900
TRIAL_CAP_CNY = Decimal("5")
# Official public Beijing/DeepSeek peak rates checked 2026-11-13, per million tokens.
LLM_INPUT_RATE = Decimal("2")
LLM_OUTPUT_RATE = Decimal("8")
EMBEDDING_RATE = Decimal("0.5")
RERANK_RATE = Decimal("0.5")


def estimate():
    denominator = Decimal(1000000)
    embedding = DOCUMENT_EMBEDDING_TOKENS + QUESTIONS * QUESTION_TOKENS
    reranking = QUESTIONS * RERANK_DOCUMENTS * (QUESTION_TOKENS + CHUNK_TOKENS)
    parts = {
        "llm": (
            QUESTIONS * LLM_INPUT_PER_QUESTION * LLM_INPUT_RATE + QUESTIONS * LLM_OUTPUT_PER_QUESTION * LLM_OUTPUT_RATE
        )
        / denominator,
        "embedding": embedding * EMBEDDING_RATE / denominator,
        "reranking": reranking * RERANK_RATE / denominator,
    }
    total = sum(parts.values())
    return {
        "proposal_only": True,
        "source_date": "2026-11-13",
        "free_allowance_assumed": 0,
        "questions": QUESTIONS,
        "embedding_input_tokens": embedding,
        "rerank_input_tokens_including_repeated_query": reranking,
        "cost_cny": {k: str(v) for k, v in parts.items()},
        "estimated_model_cny": str(total.quantize(Decimal(".01"), rounding=ROUND_CEILING)),
        "estimated_with_50_percent_margin_cny": str(
            (total * Decimal("1.5")).quantize(Decimal(".01"), rounding=ROUND_CEILING)
        ),
        "proposed_trial_cap_cny": str(TRIAL_CAP_CNY),
        "mineru_cny": None,
        "mineru_status": "unknown; separate confirmation required before upload",
        "includes_hosting": False,
        "network_calls": 0,
    }


if __name__ == "__main__":
    result = estimate()
    path = ROOT / "docs/evidence/m2-budget-proposal.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
