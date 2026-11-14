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


def download_with_dns_override(url, target, *, address, proxy=None, max_bytes=128 * 1024 * 1024, timeout=120):
    """Per-request DNS repair; keep original HTTPS SNI/certificate verification.

    Signed URL travels through stdin, never a process argument or error output.
    """
    import ipaddress
    import os
    from pathlib import Path
    import shutil
    import subprocess

    endpoint = urlsplit(url)
    ip = ipaddress.ip_address(address)
    if (
        endpoint.scheme != "https"
        or not ip.is_global
        or endpoint.port not in (None, 443)
        or endpoint.username
        or endpoint.password
        or endpoint.fragment
    ):
        raise ContractError("invalid_download_dns_override")
    executable = shutil.which("curl")
    if not executable:
        raise ContractError("curl_not_available")
    target = Path(target)
    command = [
        executable,
        "--disable",
        "--silent",
        "--fail",
        "--proto",
        "=https",
        "--max-time",
        str(timeout),
        "--max-filesize",
        str(max_bytes),
        "--output",
        str(target),
        "--config",
        "-",
    ]
    if proxy:
        route = urlsplit(proxy)
        if (
            route.scheme != "http"
            or route.hostname not in {"127.0.0.1", "localhost"}
            or not route.port
            or route.username
            or route.password
            or route.query
            or route.fragment
            or route.path not in {"", "/"}
        ):
            raise ContractError("invalid_download_proxy")
        command += ["--proxy", proxy, "--connect-to", endpoint.hostname + ":443:" + address + ":443"]
    else:
        command += ["--noproxy", "*", "--resolve", endpoint.hostname + ":443:" + address]
    result = subprocess.run(
        command,
        input="url = " + json.dumps(url) + "\n",
        capture_output=True,
        text=True,
        timeout=timeout + 5,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    if result.returncode:
        raise ProviderFailure("download_dns_transport_failed", unknown=True)
    if not target.exists() or target.stat().st_size > max_bytes:
        raise ContractError("download_size_limit")
