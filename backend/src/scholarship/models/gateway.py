"""Business-facing invocation ports with explicit ownership and execution mode."""

from contextlib import AbstractContextManager
from scholarship.domains.cancellation import check_cancelled
from typing import Protocol

from scholarship.models.adapters import ProviderClient
from scholarship.models.runtime import ModelExecutor
from scholarship.models.live import LiveExecutor
from scholarship.models.transports import network_transport
from scholarship.models.configuration import restore_snapshot
from scholarship.models.live import request_hash
from scholarship.infrastructure.database import tenant_transaction
from scholarship.adapters.providers import ContractError
import sqlalchemy as sa


class ModelGateway(Protocol):
    selection: object

    def invoke(self, intent, role, operation, arguments) -> dict: ...


class SimulatedGateway(AbstractContextManager):
    """Mock-only contract gateway; an injected transport cannot open real HTTP."""

    simulated = True

    def __init__(self, owner, run, selection, transport_factory):
        self.owner, self.run, self.selection = owner, run, selection
        self.transport_factory = transport_factory
        self.clients = {}

    def client(self, role):
        if role not in self.clients:
            self.clients[role] = ProviderClient(
                self.selection.roles[role],
                role,
                transport=self.transport_factory(role),
                timeout=self.selection.timeout_seconds,
                max_output=self.selection.max_output_tokens,
            )
        return self.clients[role]

    def invoke(self, intent, role, operation, arguments):
        check_cancelled(self.owner, self.run)
        try:
            return ModelExecutor(self.owner).execute(
                self.run, intent, self.selection, role, self.client(role), arguments, operation
            )
        except sa.exc.DBAPIError as exc:
            message = getattr(getattr(exc.orig, "diag", None), "message_primary", "")
            if message == "model request quota exhausted":
                from scholarship.models.contracts import ProviderFailure

                raise ProviderFailure("model_request_quota_exhausted") from None
            raise

    def __exit__(self, *exc):
        for client in self.clients.values():
            client.close()
        self.clients.clear()


class GrantedGateway(AbstractContextManager):
    """A grant is mandatory; reuse pools without granting arbitrary inference."""

    def __init__(self, owner, grant, selection):
        self.owner, self.grant, self.selection = owner, grant, selection
        self.transports = {}

    def invoke(self, intent, role, operation, arguments):
        provider = self.selection.roles[role].provider
        if provider not in self.transports:
            self.transports[provider] = network_transport(provider)
        return LiveExecutor(self.owner, self.grant, self.selection).execute(
            intent, role, operation, arguments, inner=self.transports[provider]
        )

    def __exit__(self, *exc):
        for transport in self.transports.values():
            transport.close()
        self.transports.clear()


class ReuseRetrievalGateway(AbstractContextManager):
    """Reuse exact successful retrieval receipts; only the new answer can send."""

    def __init__(self, owner, grant, selection, predecessor, source):
        self.owner, self.selection, self.predecessor = owner, selection, predecessor
        with tenant_transaction(owner) as conn:
            old = (
                conn.execute(sa.text("SELECT * FROM model_runtime.grants WHERE id=:id"), {"id": predecessor})
                .mappings()
                .one()
            )
            new = (
                conn.execute(sa.text("SELECT * FROM model_runtime.grants WHERE id=:id"), {"id": grant}).mappings().one()
            )
        expected_scope = {"source": source, "reuse_grant_id": str(predecessor)}
        if (
            old["project_id"] != new["project_id"]
            or old["manifest_json"].get("data_scope") != source
            or new["manifest_json"].get("data_scope") != expected_scope
        ):
            raise ContractError("retrieval_reuse_scope_mismatch")
        self.previous_selection = restore_snapshot(old["manifest_json"]["snapshot"])
        for role in ("embedding", "rerank"):
            if (
                self.previous_selection.roles[role] != selection.roles[role]
                or self.previous_selection.bindings[role] != selection.bindings[role]
            ):
                raise ContractError("retrieval_reuse_model_changed")
        self.answer = GrantedGateway(owner, grant, selection)

    def invoke(self, intent, role, operation, arguments):
        if role == "chat":
            return self.answer.invoke(intent, role, operation, arguments)
        if role not in {"embedding", "rerank"}:
            raise ContractError("retrieval_reuse_role_not_allowed")
        with tenant_transaction(self.owner) as conn:
            row = (
                conn.execute(
                    sa.text("SELECT * FROM model_runtime.calls WHERE grant_id=:id AND intent=:intent"),
                    {"id": self.predecessor, "intent": intent},
                )
                .mappings()
                .one()
            )
        if (
            row["state"] != "succeeded"
            or row["simulated"]
            or row["request_sha256"] != request_hash(self.previous_selection, role, operation, arguments)
            or row["config_json"] != self.previous_selection.snapshot()
        ):
            raise ContractError("retrieval_receipt_not_reusable")
        return row["receipt_json"]  # read-only: no transport, quota reservation or retry

    def __exit__(self, *exc):
        self.answer.__exit__(*exc)
