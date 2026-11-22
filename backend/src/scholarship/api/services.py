"""Read-only supplier/configuration and shared accounting status."""

from decimal import Decimal
from fastapi import APIRouter, Depends
import sqlalchemy as sa
from scholarship.api.auth import identity, Identity
from scholarship.config import settings
from scholarship.models.configuration import load_selection
from scholarship.infrastructure.database import tenant_transaction

router = APIRouter()


@router.get("/system/external-services")
def external_status(user: Identity = Depends(identity)):
    cfg = settings()
    local_enabled = load_selection().allow_local
    with tenant_transaction(user.user_id) as conn:
        pool = conn.execute(sa.text("SELECT * FROM billing.pools WHERE active")).mappings().first()
        counts = {
            r.state: r.n
            for r in conn.execute(sa.text("SELECT state,count(*) AS n FROM billing.allocations GROUP BY state"))
        }
        local_counts = {
            r.state: r.n
            for r in conn.execute(
                sa.text(
                    "SELECT c.state,count(*) AS n FROM model_runtime.calls c JOIN model_runtime.local_admissions a ON a.call_id=c.id GROUP BY c.state"
                )
            )
        }
        policies = [
            dict(r)
            for r in conn.execute(
                sa.text(
                    "SELECT role,enabled,request_limit,requests_used,ends_at, ends_at>now() AS unexpired FROM model_runtime.production_policies ORDER BY starts_at DESC LIMIT 20"
                )
            ).mappings()
        ]
    public_pool = None
    if pool:
        public_pool = {
            "currency": "CNY",
            "limit": str(pool["limit_amount"]),
            "spent": str(pool["spent_amount"]),
            "reserved": str(pool["reserved_amount"]),
            "available": str(max(Decimal(0), pool["limit_amount"] - pool["spent_amount"] - pool["reserved_amount"])),
            "blocked": pool["blocked"],
            "live_enabled": pool["live_enabled"],
            "period_end": pool["period_end"],
        }
    return {
        "mode": "scoped_cloud_approval"
        if policies and cfg.external_services_enabled
        else "local_models"
        if local_enabled
        else "offline_contracts_only",
        "local_transport_configured": local_enabled,
        "local_call_states": local_counts,
        "production_policies": policies,
        "live_transport_available": False,
        "bounded_validation_transport_available": True,
        "requested_external_flag": cfg.external_services_enabled,
        "role_eligible_if_live_authorized": user.role == "researcher",
        "shared_pool": public_pool,
        "own_call_states": counts,
        "credentials_present": {
            "deepseek": bool(cfg.deepseek_api_key.get_secret_value()),
            "dashscope": bool(cfg.dashscope_api_key.get_secret_value()),
            "mineru": bool(cfg.mineru_api_key.get_secret_value()),
            "chatanywhere": bool(cfg.chatanywhere_api_key.get_secret_value()),
            "openrouter": bool(cfg.openrouter_api_key.get_secret_value()),
        },
        "account_quotas_verified": False,
        "research_qa_available": False,
        "offline_demo_available": True,
    }
