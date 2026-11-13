"""Read-only supplier/configuration and shared accounting status."""

from decimal import Decimal
from fastapi import APIRouter, Depends
import sqlalchemy as sa
from scholarship.api.auth import identity, Identity
from scholarship.config import settings
from scholarship.infrastructure.database import tenant_transaction

router = APIRouter()


@router.get("/system/external-services")
def external_status(user: Identity = Depends(identity)):
    cfg = settings()
    with tenant_transaction(user.user_id) as conn:
        pool = conn.execute(sa.text("SELECT * FROM billing.pools WHERE active")).mappings().first()
        counts = {
            r.state: r.n
            for r in conn.execute(sa.text("SELECT state,count(*) AS n FROM billing.allocations GROUP BY state"))
        }
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
        "mode": "offline_contracts_only",
        "live_transport_available": False,
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
