"""Explicit, repeatable administrator entry point; never imported by the API."""

from datetime import datetime, timedelta, timezone
import hashlib
import json

from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from dotenv import dotenv_values
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import URL

from scholarship.config import PROJECT_ROOT, settings
from scholarship.infrastructure.schema import tables
from scholarship.infrastructure.security import assert_catalog


def admin_engine(database=None):
    values = dotenv_values(PROJECT_ROOT / ".env")
    return sa.create_engine(
        URL.create(
            "postgresql+psycopg",
            username="scholar_owner",
            password=values["POSTGRES_PASSWORD"],
            host=values.get("POSTGRES_HOST", "127.0.0.1"),
            port=int(values.get("POSTGRES_PORT", 15432)),
            database=database or settings().postgres_db,
        ),
        hide_parameters=True,
    )


def migrate(engine):
    values = dotenv_values(PROJECT_ROOT / ".env")
    with engine.begin() as conn:
        for role, key in [
            ("runtime", "RUNTIME_PASSWORD"),
            ("auth", "AUTH_PASSWORD"),
            ("checkpoint", "CHECKPOINT_PASSWORD"),
        ]:
            name = f"scholar_{role}"
            if not conn.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": name}):
                conn.exec_driver_sql(f"CREATE ROLE {name} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE")
            # psycopg SQL literals, never interpolate a secret into logs.
            conn.connection.driver_connection.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(name), sql.Literal(values[key]))
            )
        conn.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS scholar")
        conn.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS agent")
        conn.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        cfg = Config(str(PROJECT_ROOT / "backend/migrations/alembic.ini"))
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
        conn.exec_driver_sql("GRANT SELECT ON scholar.alembic_version TO scholar_runtime")
        assert_catalog(conn)
    # Official saver setup uses CREATE INDEX CONCURRENTLY, therefore autocommit.
    url = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(
        url, autocommit=True, row_factory=dict_row, prepare_threshold=0, options="-c search_path=agent"
    ) as conn:
        PostgresSaver(conn).setup()
        conn.execute("REVOKE ALL ON SCHEMA agent FROM PUBLIC")
        conn.execute("GRANT USAGE ON SCHEMA agent TO scholar_checkpoint")
        conn.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA agent TO scholar_checkpoint")


def seed(engine):
    values = dotenv_values(PROJECT_ROOT / ".env")
    profile = json.loads((PROJECT_ROOT / "docs/data/qdrant-profile.json").read_text())
    fingerprint = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
    with engine.begin() as conn:
        profile_id = conn.scalar(
            sa.select(tables["index_profiles"].c.id).where(
                tables["index_profiles"].c.profile_key == profile["profile_key"]
            )
        )
        if profile_id is None:
            profile_id = conn.scalar(
                tables["index_profiles"]
                .insert()
                .values(
                    profile_key=profile["profile_key"],
                    provider="dashscope",
                    model_name="text-embedding-v4",
                    dimensions=1024,
                    metric="cosine",
                    chunker_revision="v1",
                    lexical_revision="v1",
                    config_json=profile,
                    fingerprint=fingerprint,
                )
                .returning(tables["index_profiles"].c.id)
            )
        for username, display, role, key in [
            ("researcher", "我的研究空间", "researcher", "INITIAL_USER_PASSWORD"),
            ("reviewer", "比赛评审空间", "reviewer", "INITIAL_REVIEWER_PASSWORD"),
        ]:
            uid = conn.scalar(sa.select(tables["users"].c.id).where(tables["users"].c.username == username))
            if uid is not None:
                continue  # Never reset an existing account or its password.
            uid = conn.scalar(
                insert(tables["users"])
                .values(
                    username=username, display_name=display, role=role, password_hash=PasswordHasher().hash(values[key])
                )
                .returning(tables["users"].c.id)
            )
            conn.execute(
                tables["projects"]
                .insert()
                .values(
                    owner_id=uid,
                    name="默认研究项目",
                    description="从一篇文献开始，构建可追溯的研究过程。",
                    preferences={},
                    active_profile_id=profile_id,
                )
            )
            start = datetime.now(timezone.utc)
            conn.execute(
                tables["budget_accounts"]
                .insert()
                .values(
                    owner_id=uid,
                    period_start=start,
                    period_end=start + timedelta(days=settings().budget_period_days),
                    category="ai",
                    currency="CNY",
                    limit_amount=settings().ai_budget_cny,
                    spent_amount=0,
                    reserved_amount=0,
                )
            )


def main():
    engine = admin_engine()
    migrate(engine)
    seed(engine)
    engine.dispose()
    print(
        json.dumps(
            {
                "migration": "0001_foundation",
                "business_tables": 41,
                "seed": "existing accounts preserved",
                "external_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
