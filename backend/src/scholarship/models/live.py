"""One-shot manifest authorization. All real sends require committed PG slots."""

from dataclasses import asdict, replace, is_dataclass
import json

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from scholarship.adapters.providers import ExternalDisabled, ContractError
from scholarship.config import settings
from scholarship.infrastructure.database import tenant_transaction
from scholarship.models.configuration import fingerprint, CREDENTIALS
from scholarship.models.adapters import ProviderClient, ProviderFailure, ModelResult
from scholarship.models.runtime import ModelExecutor


def serialize(v):
    if is_dataclass(v):
        return asdict(v)
    if isinstance(v, list):
        return [serialize(x) for x in v]
    if isinstance(v, dict):
        return {k: serialize(x) for k, x in v.items()}
    return v


def request_hash(selection, role, operation, arguments):
    return fingerprint(
        {
            "role": role,
            "operation": operation,
            "arguments": serialize(arguments),
            "snapshot": selection.snapshot()["fingerprint"],
        }
    )


def wire_hash(request):
    return fingerprint({"method": request.method, "url": str(request.url), "body": json.loads(request.content)})


class Captured(RuntimeError):
    pass


def make_manifest(selection, requests):
    """Build exact approved wire hashes without sending HTTP or reading any key."""
    if not 1 <= len(requests) <= 5 or not 1 <= selection.max_output_tokens <= 256:
        raise ValueError("invalid_live_validation_limits")
    manifest = {"revision": "synthetic-live-v1", "snapshot": selection.snapshot(), "requests": {}}
    for item in requests:
        intent, role, operation, arguments = item
        spec = selection.roles[role]
        if (
            role not in {"chat", "embedding", "rerank"}
            or spec.provider not in {"chatanywhere", "openrouter"}
            or intent in manifest["requests"]
        ):
            raise ValueError("unapproved_validation_role")
        if spec.provider == "chatanywhere" and spec.model != "gpt-4o-mini":
            raise ValueError("unapproved_chat_model")
        captured = []

        def capture(r):
            captured.append(wire_hash(r))
            raise Captured()

        client = ProviderClient(
            spec,
            role,
            transport=httpx.MockTransport(capture),
            timeout=selection.timeout_seconds,
            max_output=selection.max_output_tokens,
        )
        try:
            try:
                getattr(client, operation)(**arguments)
            except Captured:
                pass
        finally:
            client.close()
        if len(captured) != 1:
            raise ValueError("exactly_one_wire_request_required")
        manifest["requests"][intent] = {
            "role": role,
            "provider": spec.provider,
            "model": spec.model,
            "request_sha256": request_hash(selection, role, operation, arguments),
            "wire_sha256": captured[0],
        }
    return manifest


class GrantTransport(httpx.BaseTransport):
    def __init__(self, owner, call_id, provider, *, inner=None):
        self.owner = owner
        self.call_id = call_id
        self.provider = provider
        self.inner = inner or httpx.HTTPTransport(retries=0)
        self.used = False

    def handle_request(self, request):
        if self.used:
            raise ExternalDisabled("one_wire_request_per_call")
        cfg = settings()
        key_name = CREDENTIALS.get(self.provider)
        secret = getattr(cfg, key_name).get_secret_value() if key_name else ""
        if not secret:
            raise ExternalDisabled("credential_missing_before_dispatch")
        h = wire_hash(request)
        with tenant_transaction(self.owner) as conn:
            conn.execute(sa.text("SELECT model_runtime.consume_live_slot(:id,:hash)"), {"id": self.call_id, "hash": h})
        self.used = True
        request.headers["Authorization"] = "Bearer " + secret
        return self.inner.handle_request(request)

    def close(self):
        self.inner.close()


class LiveProviderClient(ProviderClient):
    def __init__(self, selection, role, owner, call_id, *, inner=None):
        super().__init__(
            selection.roles[role],
            role,
            transport=httpx.MockTransport(lambda r: None),
            timeout=selection.timeout_seconds,
            max_output=selection.max_output_tokens,
        )
        self.client.close()
        self.client = httpx.Client(
            transport=GrantTransport(owner, call_id, self.spec.provider, inner=inner),
            timeout=self.timeout,
            follow_redirects=False,
            trust_env=False,
        )

    def result(self, *args, **kwargs):
        return replace(super().result(*args, **kwargs), simulated=False)


def reported_model_allowed(requested, reported):
    if reported is None:
        return True  # Not reported: retain unknown, never claim identity proof.
    aliases = {requested}
    if requested.endswith(":free"):
        aliases.add(requested.removesuffix(":free"))
    if requested == "gpt-4o-mini":
        aliases.add("gpt-4o-mini-2024-07-18")
    return reported in aliases


class LiveExecutor:
    def __init__(self, owner, grant_id, selection):
        self.owner = owner
        self.grant_id = grant_id
        self.selection = selection

    def execute(self, intent, role, operation, arguments, *, inner=None):
        if not 1 <= self.selection.max_output_tokens <= 256:
            raise ExternalDisabled("validation_output_limit")
        spec = self.selection.roles[role]
        h = request_hash(self.selection, role, operation, arguments)
        with tenant_transaction(self.owner) as conn:
            stmt = sa.text("SELECT model_runtime.prepare_live_call(:grant,:intent,:hash,:config)").bindparams(
                sa.bindparam("config", type_=JSONB)
            )
            cid = conn.scalar(
                stmt, {"grant": self.grant_id, "intent": intent, "hash": h, "config": self.selection.snapshot()}
            )
            row = (
                conn.execute(sa.text("SELECT state,receipt_json FROM model_runtime.calls WHERE id=:id"), {"id": cid})
                .mappings()
                .one()
            )
            if row["state"] == "succeeded":
                return row["receipt_json"]
            if row["state"] != "prepared":
                raise ProviderFailure("live_call_requires_reconciliation", unknown=row["state"] in {"sent", "unknown"})
        client = LiveProviderClient(self.selection, role, self.owner, cid, inner=inner)
        try:
            result = getattr(client, operation)(**arguments)
            if not isinstance(result, ModelResult) or result.simulated:
                raise ContractError("live_receipt_required")
            if not reported_model_allowed(spec.model, result.reported_model):
                raise ContractError("unexpected_reported_model")
            receipt = asdict(result)
            receipt["model_identity_reported"] = result.reported_model is not None
            try:
                ModelExecutor(self.owner).finish(cid, "succeeded", receipt)
            except Exception:
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
                raise ProviderFailure("live_settlement_acknowledgement_unknown", unknown=True) from None
            return receipt
        except Exception as exc:
            self.stop(exc.code if isinstance(exc, ProviderFailure) else "live_contract_or_transport_failure")
            with tenant_transaction(self.owner) as conn:
                state = conn.scalar(sa.text("SELECT state FROM model_runtime.calls WHERE id=:id"), {"id": cid})
            if state in {"sent", "unknown"}:
                final = "rejected" if isinstance(exc, ProviderFailure) and not exc.unknown else "unknown"
                ModelExecutor(self.owner).finish(
                    cid,
                    final,
                    {
                        "error_code": exc.code if isinstance(exc, ProviderFailure) else "live_contract_failure",
                        "status": exc.status if isinstance(exc, ProviderFailure) else None,
                        "simulated": False,
                    },
                )
            raise
        finally:
            client.close()

    def stop(self, reason):
        with tenant_transaction(self.owner) as conn:
            conn.execute(
                sa.text("SELECT model_runtime.stop_grant(:id,:reason)"), {"id": self.grant_id, "reason": reason}
            )
