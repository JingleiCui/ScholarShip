"""Read-only checks, without model calls or credential-bearing errors."""

import importlib.metadata
import warnings

import redis
import sqlalchemy as sa
from qdrant_client import QdrantClient

from scholarship.config import settings
from scholarship.infrastructure.database import engine
from scholarship.infrastructure.schema import REVISION


def qdrant_client():
    cfg = settings()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Api key is used with an insecure connection.*")
        return QdrantClient(
            url=cfg.qdrant_url, api_key=cfg.qdrant_api_key.get_secret_value(), timeout=5, check_compatibility=False
        )


def dependency_health():
    checks = {}
    try:
        with engine().connect() as conn:
            revision = conn.scalar(sa.text("SELECT version_num FROM scholar.alembic_version"))
            checks["postgresql"] = {
                "ready": revision == REVISION,
                "revision": revision,
                "version": conn.scalar(sa.text("SHOW server_version")),
            }
    except Exception as exc:
        checks["postgresql"] = {
            "ready": False,
            "error": "database_unavailable_or_unmigrated",
            "error_type": type(exc).__name__,
        }
    try:
        client = qdrant_client()
        try:
            version = client.info().version
            checks["qdrant"] = {"ready": version == "1.20.0", "version": version}
        finally:
            client.close()
    except Exception as exc:
        checks["qdrant"] = {"ready": False, "error": "vector_service_unavailable", "error_type": type(exc).__name__}
    try:
        with redis.Redis.from_url(settings().broker_url, socket_connect_timeout=3, socket_timeout=3) as client:
            checks["redis"] = {"ready": bool(client.ping()), "version": client.info("server")["redis_version"]}
    except Exception as exc:
        checks["redis"] = {"ready": False, "error": "broker_unavailable", "error_type": type(exc).__name__}
    return {"ready": all(c["ready"] for c in checks.values()), "services": checks}


def public_meta():
    cfg = settings()
    return {
        "name": "ScholarShip",
        "version": "0.1.0",
        "stage": "M1 · 文献与本地预览",
        "modules": [
            {"name": name, "status": "planned"}
            for name in ["文献管理", "论文阅读与翻译", "检索与推荐", "知识图谱", "写作与研究核验", "数据分析与绘图"]
        ],
        "packages": {p: importlib.metadata.version(p) for p in ["fastapi", "langgraph", "qdrant-client", "celery"]},
        "external_services": {
            "enabled": cfg.external_services_enabled,
            "deepseek_configured": bool(cfg.deepseek_api_key.get_secret_value()),
            "dashscope_configured": bool(cfg.dashscope_api_key.get_secret_value()),
            "mineru_configured": bool(cfg.mineru_api_key.get_secret_value()),
        },
        "budget": {"limit_cny": cfg.ai_budget_cny, "period_days": cfg.budget_period_days},
        "probe": "本地合成基础设施探针；不调用模型，不代表论文问答效果。",
    }
