"""Durable offline execution harness. Every simulated provider call is reserved."""

from dataclasses import dataclass, asdict, is_dataclass
from decimal import Decimal, ROUND_CEILING
import hashlib
import json

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from scholarship.adapters.providers import (
    ProviderResult,
    ExternalDisabled,
    UnknownOutcome,
    Embeddings,
    Reranker,
    Answerer,
    EMBEDDING_MODEL,
    RERANK_MODEL,
    CHAT_MODEL,
    MAX_OUTPUT_TOKENS,
)
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables

# USER CONFIGURATION: reviewed peak/no-cache CNY rates per million tokens.
RATES = {
    "chat": (Decimal("2"), Decimal("8")),
    "embedding": (Decimal(".5"), Decimal("0")),
    "rerank": (Decimal(".5"), Decimal("0")),
}
MICRO = Decimal(".000001")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def price(operation, input_tokens, output_tokens=0):
    if (
        operation not in RATES
        or type(input_tokens) is not int
        or type(output_tokens) is not int
        or min(input_tokens, output_tokens) < 0
    ):
        raise ValueError("invalid_usage_or_operation")
    a, b = RATES[operation]
    return ((input_tokens * a + output_tokens * b) / Decimal(1000000)).quantize(MICRO, rounding=ROUND_CEILING)


@dataclass(frozen=True)
class Reservation:
    call_id: object
    state: str


class Budget:
    def __init__(self, owner):
        self.owner = owner

    def reserve(self, step, intent, provider, operation, request, amount, price_snapshot, *, offline=True):
        with tenant_transaction(self.owner) as conn:
            sql = sa.text(
                "SELECT billing.reserve_call(:sid,:intent,:provider,:operation,:hash,:amount,:price,:offline)"
            ).bindparams(sa.bindparam("price", type_=JSONB))
            cid = conn.scalar(
                sql,
                {
                    "sid": step,
                    "intent": intent,
                    "provider": provider,
                    "operation": operation,
                    "hash": fingerprint(request),
                    "amount": Decimal(amount).quantize(MICRO, rounding=ROUND_CEILING),
                    "price": price_snapshot,
                    "offline": offline,
                },
            )
            state = conn.scalar(sa.text("SELECT state FROM billing.allocations WHERE call_id=:cid"), {"cid": cid})
            return Reservation(cid, state)

    def sent(self, cid):
        with tenant_transaction(self.owner) as conn:
            return bool(conn.scalar(sa.text("SELECT billing.mark_sent(:cid)"), {"cid": cid}))

    def unknown(self, cid):
        with tenant_transaction(self.owner) as conn:
            conn.execute(sa.text("SELECT billing.mark_unknown(:cid)"), {"cid": cid})

    def release(self, cid):
        with tenant_transaction(self.owner) as conn:
            conn.execute(sa.text("SELECT billing.release_unsent(:cid)"), {"cid": cid})

    def settle(self, cid, actual, receipt):
        sql = sa.text("SELECT billing.settle_call(:cid,:amount,:receipt,:hash)").bindparams(
            sa.bindparam("receipt", type_=JSONB)
        )
        with tenant_transaction(self.owner) as conn:
            conn.execute(
                sql,
                {
                    "cid": cid,
                    "amount": Decimal(actual).quantize(MICRO, rounding=ROUND_CEILING),
                    "receipt": receipt,
                    "hash": fingerprint(receipt),
                },
            )

    def receipt(self, cid):
        with tenant_transaction(self.owner) as conn:
            return conn.scalar(
                sa.select(tables["external_calls"].c.receipt_json).where(tables["external_calls"].c.id == cid)
            )


class OfflineExecutor:
    """Internal fixture runner, never attached to a public paid-execution route.

    invoke must call an adapter constructed with MockTransport. Invalid responses
    after dispatch remain unknown. There are no hidden retries or model fallbacks.
    """

    def __init__(self, owner):
        self.budget = Budget(owner)

    def execute(self, step, intent, adapter, operation, arguments, maximum_input, maximum_output=0):
        bindings = {
            Embeddings: ("dashscope", "embedding", "embed"),
            Reranker: ("dashscope", "rerank", "rank"),
            Answerer: ("deepseek", "chat", "answer"),
        }
        binding = bindings.get(type(adapter))
        if binding is None or binding[1] != operation:
            raise ExternalDisabled("offline_adapter_operation_required")
        provider, _, method = binding

        def serialize(value):
            if is_dataclass(value):
                return asdict(value)
            if isinstance(value, list):
                return [serialize(v) for v in value]
            if isinstance(value, dict):
                return {k: serialize(v) for k, v in value.items()}
            return value

        model = {Embeddings: EMBEDDING_MODEL, Reranker: RERANK_MODEL, Answerer: CHAT_MODEL}[type(adapter)]
        if (operation, model) not in {
            ("embedding", "text-embedding-v4"),
            ("rerank", "qwen3-rerank"),
            ("chat", "deepseek-flash"),
        }:
            raise ExternalDisabled("model_price_requires_review")
        if operation == "chat":
            maximum_output = max(maximum_output, MAX_OUTPUT_TOKENS)
        request = {
            "contract_revision": "offline-v1",
            "operation": operation,
            "arguments": serialize(arguments),
            "model": model,
            "endpoint": getattr(adapter, "url", "https://api.deepseek.com/chat/completions"),
            "prompt_revision": "evidence-json-v1" if operation == "chat" else None,
        }

        upper = max(MICRO, price(operation, maximum_input, maximum_output))
        quote = {
            "currency": "CNY",
            "model": model,
            "operation": operation,
            "per_million": [str(x) for x in RATES[operation]],
            "source_date": "2026-11-13",
            "offline": True,
        }
        hold = self.budget.reserve(step, intent, provider, operation, request, upper, quote)
        if hold.state == "settled":
            return self.budget.receipt(hold.call_id)
        if not self.budget.sent(hold.call_id):
            raise UnknownOutcome("existing_call_requires_reconciliation")
        try:
            result = getattr(adapter, method)(**arguments)
            if not isinstance(result, ProviderResult):
                raise ValueError("invalid_adapter_result")
            amount = price(operation, result.usage["input_tokens"], result.usage.get("output_tokens", 0))
            receipt = {
                "offline": True,
                "provider_request_id": result.request_id,
                "model": result.reported_model,
                "usage": result.usage,
                "result": result.value,
            }
            # A MinerU signed upload URL is intentionally not an executor result.
            if any(key in canonical(receipt) for key in ['"upload_url"', '"full_zip_url"', '"result_url"']):
                raise ValueError("url_receipts_require_private_artifact_storage")
            self.budget.settle(hold.call_id, amount, receipt)
            return receipt
        except Exception:
            try:
                self.budget.unknown(hold.call_id)
            except Exception:
                existing = self.budget.receipt(hold.call_id)
                if existing is not None:
                    return existing
            raise UnknownOutcome("offline_call_needs_reconciliation") from None
