"""Production invocation port, separate from bounded validation grants.

No policies are enabled here. Free routes need an explicit, expiring DB policy
AND the shared live budget AND configuration gates. Unknown sends never retry.
"""

from contextlib import AbstractContextManager
from dataclasses import asdict, replace
import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from scholarship.adapters.providers import ExternalDisabled, ContractError
from scholarship.config import settings
from scholarship.infrastructure.database import tenant_transaction
from scholarship.models.adapters import ProviderClient
from scholarship.models.catalog import credential
from scholarship.models.configuration import restore_snapshot
from scholarship.models.contracts import ROLE_OPERATIONS, ProviderFailure
from scholarship.models.live import request_hash
from scholarship.models.runtime import ModelExecutor
from scholarship.models.transports import network_transport
from scholarship.domains.indexing import call_step


def require_cloud(selection):
    if not selection.allow_cloud or not settings().external_services_enabled:
        raise ExternalDisabled("production_configuration_closed")


class AdmissionTransport(httpx.BaseTransport):
    def __init__(self, owner, call_id, step_id, provider, output_limit, inner):
        self.owner, self.call_id, self.step_id, self.provider = owner, call_id, step_id, provider
        self.output_limit, self.inner, self.used = output_limit, inner, False

    def handle_request(self, request):
        if self.used:
            raise ExternalDisabled("production_one_wire_per_intent")
        secret = credential(self.provider)
        if not secret:
            raise ExternalDisabled("production_credential_missing")
        # Conservative UTF-8 byte bound including prompt/JSON overhead; no guessed price.
        maximum = len(request.content) + self.output_limit + 8192
        try:
            with tenant_transaction(self.owner) as conn:
                conn.execute(
                    sa.text("SELECT model_runtime.admit_production(:c,:s,:m)"),
                    {"c": self.call_id, "s": self.step_id, "m": maximum},
                )
        except DBAPIError:
            raise ExternalDisabled("production_admission_closed") from None
        self.used = True
        request.headers["Authorization"] = "Bearer " + secret
        return self.inner.handle_request(request)

    def close(self):
        pass  # gateway owns the shared connection pool


class ProductionClient(ProviderClient):
    def result(self, *args, **kwargs):
        return replace(super().result(*args, **kwargs), simulated=False)


class ProductionGateway(AbstractContextManager):
    simulated = False

    def __init__(self, owner, project, run, selection, *, transport_factory=network_transport):
        self.owner, self.project, self.run, self.selection = owner, project, run, selection
        self.transport_factory, self.transports = transport_factory, {}

    def invoke(self, intent, role, operation, arguments):
        require_cloud(self.selection)
        restore_snapshot(self.selection.snapshot())
        if operation not in ROLE_OPERATIONS.get(role, set()):
            raise ContractError("invalid_model_operation")
        spec = self.selection.roles[role]
        try:
            with tenant_transaction(self.owner) as conn:
                stmt = sa.text("SELECT model_runtime.prepare_production(:r,:k,:h,:c,:role)").bindparams(
                    sa.bindparam("c", type_=JSONB)
                )
                cid = conn.scalar(
                    stmt,
                    {
                        "r": self.run,
                        "k": intent,
                        "h": request_hash(self.selection, role, operation, arguments),
                        "c": self.selection.snapshot(),
                        "role": role,
                    },
                )
                row = (
                    conn.execute(sa.text("SELECT * FROM model_runtime.calls WHERE id=:id"), {"id": cid})
                    .mappings()
                    .one()
                )
        except DBAPIError:
            raise ExternalDisabled("production_policy_closed") from None
        if row["state"] == "succeeded":
            receipt = row["receipt_json"]
            if receipt["usage"].get("total_tokens") is None:
                raise ProviderFailure("production_usage_reconciliation_required", unknown=True)
            self.settle(cid, receipt)
            return receipt
        if row["state"] != "prepared":
            raise ProviderFailure("production_call_reconciliation_required", unknown=True)
        step = call_step(
            self.owner, self.project, self.run, "production:" + str(cid), {"request_sha256": row["request_sha256"]}
        )
        if spec.provider not in self.transports:
            self.transports[spec.provider] = self.transport_factory(spec.provider)
        client = ProductionClient(
            spec,
            role,
            transport=httpx.MockTransport(lambda r: None),
            timeout=self.selection.timeout_seconds,
            max_output=self.selection.max_output_tokens,
        )
        if client.binding != self.selection.bindings[role]:
            client.close()
            raise ExternalDisabled("production_binding_changed")
        client.client.close()
        transport = AdmissionTransport(
            self.owner, cid, step, spec.provider, self.selection.max_output_tokens, self.transports[spec.provider]
        )
        client.client = httpx.Client(
            transport=transport, timeout=self.selection.timeout_seconds, follow_redirects=False, trust_env=False
        )
        try:
            result = getattr(client, operation)(**arguments)
            if result.simulated or not transport.used:
                raise ContractError("production_receipt_without_dispatch")
            receipt = asdict(result)
            usage = receipt["usage"]
            if usage.get("total_tokens") is None:
                if usage.get("input_tokens") is not None and usage.get("output_tokens") is not None:
                    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
                    usage["total_tokens_basis"] = "input_plus_output"
                elif role in {"embedding", "rerank"} and usage.get("input_tokens") is not None:
                    usage["total_tokens"] = usage["input_tokens"]
                    usage["total_tokens_basis"] = "non_generating_input_only"
            raw = client.last_response or {}
            cost = (raw.get("usage") or {}).get("cost")
            if cost is not None:
                from decimal import Decimal, InvalidOperation

                try:
                    if Decimal(str(cost)) != 0:
                        raise ContractError("unexpected_paid_usage")
                except InvalidOperation:
                    raise ContractError("invalid_reported_cost") from None
            receipt["cost_verification"] = "reported_zero" if cost is not None else "not_reported_free_policy_only"
            ModelExecutor(self.owner).finish(cid, "succeeded", receipt)
            self.settle(cid, receipt)
            return receipt
        except Exception as exc:
            if transport.used:
                with tenant_transaction(self.owner) as conn:
                    conn.execute(sa.text("SELECT model_runtime.suspend_production(:id)"), {"id": cid})
                with tenant_transaction(self.owner) as conn:
                    state = conn.scalar(sa.text("SELECT state FROM model_runtime.calls WHERE id=:id"), {"id": cid})
                if state == "sent":
                    final = "rejected" if isinstance(exc, ProviderFailure) and not exc.unknown else "unknown"
                    import json
                    import re

                    diagnostic_file_id = None
                    response = client.last_response or {}
                    choices = response.get("choices")
                    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                        message = choices[0].get("message", {})
                        content = message.get("content") if isinstance(message, dict) else None
                        if isinstance(content, str):
                            secret = credential(spec.provider)
                            if secret:
                                content = content.replace(secret, "<credential-redacted>")
                            content = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "<credential-redacted>", content[:20000])
                            from scholarship.domains.deep_parse import persist_file

                            with tenant_transaction(self.owner) as conn:
                                diagnostic_file_id = persist_file(
                                    conn,
                                    self.owner,
                                    json.dumps(
                                        {
                                            "model_call_id": str(cid),
                                            "content": content,
                                            "finish_reason": client.failure_diagnostic()["finish_reason"],
                                        },
                                        ensure_ascii=False,
                                    ).encode(),
                                    "json",
                                    "application/json",
                                )
                    ModelExecutor(self.owner).finish(
                        cid,
                        final,
                        {
                            "simulated": False,
                            "contract_code": str(exc)
                            if isinstance(exc, ContractError) and re.fullmatch(r"[a-z_]{1,100}", str(exc))
                            else None,
                            "finish_reason": client.failure_diagnostic()["finish_reason"],
                            "diagnostic_file_id": str(diagnostic_file_id) if diagnostic_file_id else None,
                            "error_code": exc.code
                            if isinstance(exc, ProviderFailure)
                            else "production_response_unknown",
                        },
                    )
                with tenant_transaction(self.owner) as conn:
                    bid = conn.scalar(
                        sa.text(
                            "SELECT billing_call_id FROM model_runtime.production_admissions WHERE call_id=:id AND NOT settled"
                        ),
                        {"id": cid},
                    )
                    if bid:
                        conn.execute(sa.text("SELECT billing.mark_unknown(:id)"), {"id": bid})
            raise
        finally:
            client.close()

    def settle(self, cid, receipt):
        actual = receipt["usage"].get("total_tokens")
        if actual is None:
            raise ProviderFailure("production_usage_reconciliation_required", unknown=True)
        try:
            with tenant_transaction(self.owner) as conn:
                conn.execute(sa.text("SELECT model_runtime.settle_production(:id,:n)"), {"id": cid, "n": actual})
        except DBAPIError:
            raise ProviderFailure("production_settlement_reconciliation_required", unknown=True) from None

    def __exit__(self, *exc):
        for t in self.transports.values():
            t.close()
        self.transports.clear()
