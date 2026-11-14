"""HTTP concerns only: explicit routes, verified TLS, bounded JSON and errors."""

import json
import time
from urllib.parse import urlsplit
import httpx
from scholarship.adapters.providers import ContractError
from scholarship.models.contracts import ProviderFailure
from scholarship.models.catalog import configuration

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def request_json(client, url, body, *, authenticated):
    started = time.monotonic()
    headers = {"Authorization": "Bearer contract-test-key"} if authenticated else {}
    try:
        with client.stream("POST", url, json=body, headers=headers) as r:
            if r.status_code != 200:
                code = {
                    401: "authentication_failed",
                    403: "permission_denied",
                    404: "model_or_endpoint_unavailable",
                    429: "rate_limited",
                }.get(r.status_code, "provider_http_error")
                delay = r.headers.get("retry-after", "")
                raise ProviderFailure(
                    code,
                    r.status_code,
                    unknown=r.status_code >= 500,
                    retry_after=min(int(delay), 3600) if delay.isdigit() else None,
                )
            raw = bytearray()
            for block in r.iter_bytes():
                raw.extend(block)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ContractError("provider_response_too_large")
            d = json.loads(raw)
            if not isinstance(d, dict) or d.get("error"):
                raise ContractError("invalid_provider_response")
            return d, int((time.monotonic() - started) * 1000)
    except httpx.TransportError as exc:
        # Keep only a closed diagnostic category, never exception text/URLs/headers.
        kind = next(
            (
                name
                for cls, name in (
                    (httpx.ConnectTimeout, "connect_timeout"),
                    (httpx.ConnectError, "connect_error"),
                    (httpx.ReadTimeout, "read_timeout"),
                    (httpx.WriteTimeout, "write_timeout"),
                    (httpx.PoolTimeout, "pool_timeout"),
                    (httpx.ProxyError, "proxy_error"),
                    (httpx.RemoteProtocolError, "remote_protocol_error"),
                )
                if isinstance(exc, cls)
            ),
            "transport_error",
        )
        raise ProviderFailure("provider_transport_outcome_unknown", unknown=True, transport_kind=kind) from None
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("invalid_provider_json") from None


def network_transport(provider):
    """Explicit local proxy only; never silently inherit environment proxies."""
    routes = configuration().get("LIVE_PROXY_URLS", {})
    if not isinstance(routes, dict):
        raise ValueError("invalid_proxy_routes")
    proxy = routes.get(provider)
    if proxy is not None:
        try:
            u = urlsplit(proxy)
            if (
                u.scheme != "http"
                or u.hostname not in {"127.0.0.1", "localhost"}
                or not u.port
                or u.username
                or u.password
                or u.path not in {"", "/"}
                or u.query
                or u.fragment
            ):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("invalid_local_proxy") from None
    return httpx.HTTPTransport(retries=0, trust_env=False, proxy=proxy)
