"""Diagnose configured routes without credentials, inference or file uploads."""

import json
import time

import httpx

from scholarship.config import PROJECT_ROOT
from scholarship.models.live import network_transport

# USER CONFIGURATION: requests deliberately omit every API key and model input.
TIMEOUT_SECONDS = 15
REPORT = PROJECT_ROOT / "var/reports/model-network.json"
CHECKS = [
    ("chatanywhere", "GET", "https://api.chatanywhere.tech/v1/models", None),
    ("openrouter", "POST", "https://openrouter.ai/api/v1/embeddings", {}),
]


def main():
    results = []
    for provider, method, url, body in CHECKS:
        started = time.monotonic()
        row = {"provider": provider, "authenticated": False, "inference": False}
        try:
            with httpx.Client(
                transport=network_transport(provider), trust_env=False, timeout=TIMEOUT_SECONDS, follow_redirects=False
            ) as client:
                response = client.request(method, url, json=body)
            row.update(http_status=response.status_code, reached_http_server=True)
        except httpx.TransportError as exc:
            row.update(reached_http_server=False, error_type=type(exc).__name__)
        row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        results.append(row)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results))


if __name__ == "__main__":
    main()
