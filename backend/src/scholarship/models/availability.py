"""Read-only loopback readiness; installation never proves response quality."""

import httpx
from scholarship.models.catalog import deployment


def local_readiness(selection):
    states, tags = {}, {}
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=1) as client:
        for role, spec in selection.roles.items():
            if deployment(spec.provider) != "local":
                continue
            protocol = selection.bindings[role]["protocol"]
            try:
                if protocol in {"ollama_chat", "ollama_embedding"}:
                    if spec.base_url not in tags:
                        response = client.get(spec.base_url + "/api/tags")
                        response.raise_for_status()
                        tags[spec.base_url] = {m["name"] for m in response.json()["models"]}
                    states[role] = spec.model in tags[spec.base_url]
                elif protocol == "rerank":
                    response = client.get(spec.base_url + "/health")
                    response.raise_for_status()
                    data = response.json()
                    states[role] = data.get("model") == spec.model and data.get("weights_present") is True
                else:
                    states[role] = False
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                states[role] = False
    return states
