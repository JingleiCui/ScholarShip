"""Read-only local model inventory. Never infer, pull weights or upload documents."""

import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from scholarship.models.catalog import configuration

# USER CONFIGURATION — edit here, then run in PyCharm with this project's .venv.
REPORT_PATH = Path(__file__).resolve().parent / "var/reports/local-models.json"
TIMEOUT_SECONDS = 3
MAX_RESPONSE_BYTES = 1024 * 1024


def inspect_ollama(base_url, expected, *, transport=None):
    endpoint = urlsplit(base_url)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname != "127.0.0.1"
        or not endpoint.port
        or endpoint.username
        or endpoint.password
        or endpoint.path not in ("", "/")
        or endpoint.query
        or endpoint.fragment
    ):
        return {"state": "invalid_local_endpoint", "missing": sorted(expected)}
    try:
        with httpx.Client(transport=transport, trust_env=False, follow_redirects=False, timeout=TIMEOUT_SECONDS) as c:
            with c.stream("GET", base_url.rstrip("/") + "/api/tags") as response:
                if response.status_code != 200:
                    return {"state": "http_error", "status": response.status_code, "missing": sorted(expected)}
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        return {"state": "response_too_large", "missing": sorted(expected)}
                payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise ValueError()
        installed = set()
        for model in payload["models"]:
            if not isinstance(model, dict) or not isinstance(model.get("name"), str):
                raise ValueError()
            installed.add(model["name"])
        missing = sorted(set(expected) - installed)
        return {
            "state": "models_missing" if missing else "models_present",
            "installed": sorted(installed),
            "missing": missing,
            "inference_verified": False,
        }
    except httpx.TransportError:
        return {"state": "unreachable", "missing": sorted(expected)}
    except (ValueError, UnicodeError):
        return {"state": "invalid_response", "missing": sorted(expected)}


def report():
    config = configuration()
    roles = config["PRESETS"]["local"]
    routes = {}
    pending = {}
    for role, name in roles.items():
        model = config["MODELS"][name]
        if model["provider"] == "ollama":
            routes.setdefault(model["base_url"], set()).add(model["model"])
        else:
            pending[role] = {"provider": model["provider"], "state": "service_contract_not_verified"}
    return {
        "ollama": [inspect_ollama(url, expected) for url, expected in routes.items()],
        "other_roles": pending,
        "business_dispatch": "production_gateway_requires_policy_and_gates",
        "ready_for_full_workflow": False,
        "inference_requests": 0,
        "uploads": 0,
    }


if __name__ == "__main__":
    result = report()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
