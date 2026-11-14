"""Business-facing invocation ports with explicit ownership and execution mode."""

from contextlib import AbstractContextManager
from typing import Protocol

from scholarship.models.adapters import ProviderClient
from scholarship.models.runtime import ModelExecutor
from scholarship.models.live import LiveExecutor
from scholarship.models.transports import network_transport


class ModelGateway(Protocol):
    selection: object

    def invoke(self, intent, role, operation, arguments) -> dict: ...


class SimulatedGateway(AbstractContextManager):
    """Mock-only contract gateway; an injected transport cannot open real HTTP."""

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
        return ModelExecutor(self.owner).execute(
            self.run, intent, self.selection, role, self.client(role), arguments, operation
        )

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
