"""Idempotent simulated execution; no monetary estimates or inference permissions."""

from dataclasses import asdict
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from scholarship.adapters.providers import ContractError, ExternalDisabled
from scholarship.models.configuration import fingerprint, restore_snapshot
from scholarship.models.adapters import ModelResult, ProviderFailure, ProviderClient
from scholarship.infrastructure.database import tenant_transaction


class ModelExecutor:
    def __init__(self, owner):
        self.owner = owner

    def execute(self, run, intent, selection, role, client, arguments, operation):
        if (
            type(client) is not ProviderClient
            or client.spec != selection.roles[role]
            or client.role != role
            or client.timeout != selection.timeout_seconds
            or client.max_output != selection.max_output_tokens
        ):
            raise ExternalDisabled("provider_snapshot_mismatch")
        methods = {
            "chat": {"answer", "generate"},
            "translation": {"generate"},
            "embedding": {"embed"},
            "rerank": {"rank"},
        }
        if operation not in methods.get(role, set()):
            raise ContractError("invalid_model_operation")

        def serialize(v):
            from dataclasses import is_dataclass

            if is_dataclass(v):
                return asdict(v)
            if isinstance(v, list):
                return [serialize(x) for x in v]
            if isinstance(v, dict):
                return {k: serialize(x) for k, x in v.items()}
            return v

        snapshot = selection.snapshot()
        restore_snapshot(snapshot)
        h = fingerprint(
            {"operation": operation, "arguments": serialize(arguments), "snapshot": snapshot["fingerprint"]}
        )
        spec = client.spec
        with tenant_transaction(self.owner) as conn:
            stmt = sa.text(
                "SELECT model_runtime.prepare_call(:run,:key,:hash,:config,:role,:provider,:model,:quota)"
            ).bindparams(sa.bindparam("config", type_=JSONB))
            cid = conn.scalar(
                stmt,
                {
                    "run": run,
                    "key": intent,
                    "hash": h,
                    "config": snapshot,
                    "role": role,
                    "provider": spec.provider,
                    "model": spec.model,
                    "quota": selection.max_requests_per_run,
                },
            )
            row = (
                conn.execute(sa.text("SELECT state,receipt_json FROM model_runtime.calls WHERE id=:id"), {"id": cid})
                .mappings()
                .one()
            )
            if row["state"] == "succeeded":
                return row["receipt_json"]
            if not conn.scalar(sa.text("SELECT model_runtime.mark_sent(:id)"), {"id": cid}):
                raise ProviderFailure("existing_call_requires_reconciliation", unknown=row["state"] == "unknown")
        try:
            result = getattr(client, operation)(**arguments)
            if not isinstance(result, ModelResult) or not result.simulated:
                raise ContractError("simulation_result_required")
            receipt = asdict(result)
        except Exception as exc:
            state = "unknown" if not isinstance(exc, ProviderFailure) or exc.unknown else "rejected"
            self.finish(
                cid,
                state,
                {
                    "error_code": exc.code
                    if isinstance(exc, ProviderFailure)
                    else "response_contract_or_transport_unknown",
                    "retry_after": exc.retry_after if isinstance(exc, ProviderFailure) else None,
                    "simulated": True,
                },
            )
            raise
        try:
            self.finish(cid, "succeeded", receipt)
        except Exception:
            # Lost commit acknowledgement: never overwrite a settled receipt.
            with tenant_transaction(self.owner) as conn:
                saved = (
                    conn.execute(
                        sa.text("SELECT state,receipt_json FROM model_runtime.calls WHERE id=:id"), {"id": cid}
                    )
                    .mappings()
                    .one()
                )
                if saved["state"] == "succeeded":
                    return saved["receipt_json"]
            raise ProviderFailure("settlement_acknowledgement_unknown", unknown=True) from None
        return receipt

    def finish(self, cid, state, receipt):
        with tenant_transaction(self.owner) as conn:
            stmt = sa.text("SELECT model_runtime.finish_call(:id,:state,:receipt)").bindparams(
                sa.bindparam("receipt", type_=JSONB)
            )
            conn.execute(stmt, {"id": cid, "state": state, "receipt": receipt})


def register_embedding_profile(owner, spec, result):
    if (
        result.provider != spec.provider
        or result.requested_model != spec.model
        or result.dimensions is None
        or not result.simulated
    ):
        raise ContractError("validated_simulated_embedding_receipt_required")
    config = {
        "contract_revision": "embedding-profile-v1",
        "provider": spec.provider,
        "model": spec.model,
        "base_url": spec.base_url,
        "dimensions": result.dimensions,
        "simulated": True,
        "metric": "cosine",
        "tokenizer": "utf8-bytes-v1",
        "target_tokens": 600,
        "max_tokens": 900,
        "overlap_tokens": 80,
        "query_prefix": "",
        "document_prefix": "",
        "input_type_policy": "search_query/search_document" if spec.provider == "openrouter" else "same_text",
        "lexical_revision": "nfkc-word-cjk-bigram-v1",
        "normalization": "original-unmodified",
    }
    with tenant_transaction(owner) as conn:
        stmt = sa.text("SELECT model_runtime.register_profile(:config,:hash,:dim)").bindparams(
            sa.bindparam("config", type_=JSONB)
        )
        return conn.scalar(stmt, {"config": config, "hash": fingerprint(config), "dim": result.dimensions})
