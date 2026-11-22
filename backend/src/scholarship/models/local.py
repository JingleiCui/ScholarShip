"""Durable loopback-only invocation, independent of all cloud grants/budgets."""

import httpx
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from scholarship.adapters.providers import ExternalDisabled
from scholarship.infrastructure.database import tenant_transaction
from scholarship.models.catalog import binding_snapshot, provider_definition, credential
from scholarship.models.production import ProductionGateway, AdmissionTransport
from scholarship.models.routing import require_local
from scholarship.models.contracts import ProviderFailure


class LocalAdmissionTransport(AdmissionTransport):
    def handle_request(self, request):
        if self.used:
            raise ExternalDisabled("local_one_wire_per_intent")
        require_local(self.selection, (self.role,))
        spec = self.selection.roles[self.role]
        if binding_snapshot(spec, self.role) != self.selection.bindings[self.role]:
            raise ExternalDisabled("local_binding_changed")
        if (
            request.url.scheme != "http"
            or request.url.host != "127.0.0.1"
            or not str(request.url).startswith(spec.base_url + "/")
        ):
            raise ExternalDisabled("local_endpoint_required")
        definition = provider_definition(spec.provider)
        secret = credential(spec.provider) if definition.get("key_env") else ""
        if definition.get("key_env") and not secret:
            raise ExternalDisabled("local_credential_missing")
        maximum = len(request.content) + self.output_limit + 8192
        try:
            with tenant_transaction(self.owner) as conn:
                conn.execute(
                    sa.text("SELECT model_runtime.admit_local(:c,:s,:m)"),
                    {"c": self.call_id, "s": self.step_id, "m": maximum},
                )
        except DBAPIError:
            raise ExternalDisabled("local_admission_closed") from None
        self.used = True
        if secret:
            request.headers["Authorization"] = "Bearer " + secret
        else:
            request.headers.pop("Authorization", None)
        return self.inner.handle_request(request)


class LocalGateway(ProductionGateway):
    prepare_function = "prepare_local"
    transport_class = LocalAdmissionTransport
    local_execution = True

    def __init__(self, owner, project, run, selection, *, transport_factory=None):
        # No environment proxies, retries or redirects; only loopback endpoints.
        super().__init__(
            owner,
            project,
            run,
            selection,
            transport_factory=transport_factory or (lambda _: httpx.HTTPTransport(retries=0, trust_env=False)),
        )

    def invoke(self, intent, role, operation, arguments):
        require_local(self.selection, (role,))
        return super().invoke(intent, role, operation, arguments)

    def settle(self, cid, receipt):
        actual = receipt["usage"].get("total_tokens")
        if actual is None:
            raise ProviderFailure("local_usage_reconciliation_required", unknown=True)
        try:
            with tenant_transaction(self.owner) as conn:
                conn.execute(sa.text("SELECT model_runtime.settle_local(:id,:n)"), {"id": cid, "n": actual})
        except DBAPIError:
            raise ProviderFailure("local_settlement_reconciliation_required", unknown=True) from None
