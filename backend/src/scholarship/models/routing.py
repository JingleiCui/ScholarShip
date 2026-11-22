"""Role-based routing policy shared by all real execution paths.

This validates configuration, not installation or permission to send data. The
the selected gateway must still admit the call in PostgreSQL before sending.
"""

from scholarship.adapters.providers import ExternalDisabled
from scholarship.config import settings
from scholarship.models.catalog import configuration, deployment


def require_execution(selection, roles):
    config = configuration()
    mode = config.get("INFERENCE_MODE", "auto")
    if mode not in {"auto", "local", "cloud"}:
        raise ValueError("invalid_inference_mode")
    # Cloud gates apply only to cloud deployments; LocalGateway has independent DB admission.
    for role in roles:
        spec = selection.roles[role]
        spec.validate(role, config=config)
        target = deployment(spec.provider, config)
        if target == "cloud" and not settings().external_services_enabled:
            raise ExternalDisabled("production_configuration_closed")
        if target == "simulated":
            raise ExternalDisabled("production_real_model_required")
        if mode != "auto" and target != mode:
            raise ExternalDisabled("production_deployment_mode_mismatch")
        flag = "ALLOW_LOCAL_INFERENCE" if target == "local" else "ALLOW_REAL_REQUESTS"
        selected_flag = selection.allow_local if target == "local" else selection.allow_cloud
        # Read file flags at every invocation; a queued snapshot cannot reopen a
        # gate closed after enqueue. In-flight network requests cannot be revoked.
        if not selected_flag or config.get(flag) is not True:
            raise ExternalDisabled("production_configuration_closed")


def require_local(selection, roles):
    config = configuration()
    if any(deployment(selection.roles[role].provider, config) != "local" for role in roles):
        raise ExternalDisabled("local_provider_required")
    require_execution(selection, roles)
