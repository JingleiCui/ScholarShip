from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import dotenv_values
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Editable defaults. Process environment overrides .env, which overrides these.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("SCHOLAR_DATA_DIR", PROJECT_ROOT / "var"))
SOURCE_PDF_DIR = Path("/Users/cuijinglei/Documents/homework/比赛/科研小助手")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)
    scholar_data_dir: str = str(DATA_DIR)
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 15432
    postgres_db: str = "scholarship"
    runtime_password: SecretStr = SecretStr("")
    auth_password: SecretStr = SecretStr("")
    checkpoint_password: SecretStr = SecretStr("")
    redis_host: str = "127.0.0.1"
    redis_port: int = 16379
    redis_password: SecretStr = SecretStr("")
    qdrant_url: str = "http://127.0.0.1:16333"
    qdrant_api_key: SecretStr = SecretStr("")
    session_secret: SecretStr = SecretStr("")
    api_port: int = 18080
    frontend_port: int = 15173
    external_services_enabled: bool = False
    chatanywhere_api_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")
    deepseek_api_key: SecretStr = SecretStr("")
    dashscope_workspace_id: str = ""
    dashscope_api_key: SecretStr = SecretStr("")
    mineru_api_key: SecretStr = SecretStr("")
    ai_budget_cny: int = 100
    budget_period_days: int = 30
    cookie_secure: bool = False
    lease_seconds: int = 60
    celery_queue: str = "scholarship"
    stream_timeout_seconds: int = 120

    def dsn(self, role: str = "runtime", *, sqlalchemy: bool = False) -> str:
        passwords = {
            "runtime": self.runtime_password,
            "auth": self.auth_password,
            "checkpoint": self.checkpoint_password,
        }
        password = quote(passwords[role].get_secret_value(), safe="")
        scheme = "postgresql+psycopg" if sqlalchemy else "postgresql"
        return f"{scheme}://scholar_{role}:{password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def broker_url(self) -> str:
        password = quote(self.redis_password.get_secret_value(), safe="")
        return f"redis://:{password}@{self.redis_host}:{self.redis_port}/0"

    @property
    def allowed_origins(self) -> set[str]:
        return {f"http://127.0.0.1:{self.frontend_port}", f"http://127.0.0.1:{self.api_port}", "http://127.0.0.1:18081"}


def environment_values() -> dict[str, str]:
    values = {} if os.environ.get("SCHOLAR_ENV_ONLY") == "1" else dict(dotenv_values(PROJECT_ROOT / ".env"))
    return {k.lower(): v for k, v in {**values, **os.environ}.items() if v is not None}


@lru_cache
def settings() -> Settings:
    fields = Settings.model_fields
    return Settings(**{k: v for k, v in environment_values().items() if k in fields})
