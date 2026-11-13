"""Integration fixtures own an isolated, disposable PostgreSQL database."""

import os
import shutil
import signal
import subprocess
import time
from uuid import uuid4

import pytest
import sqlalchemy as sa
from psycopg import sql
from fastapi.testclient import TestClient

from scholarship.config import PROJECT_ROOT, settings
from scholarship.infrastructure.database import engine
from scholarship.infrastructure.initialize import admin_engine, migrate, seed
from scholarship.infrastructure.schema import tables
from scholarship.main import create_app

# USER CONFIGURATION: never run tests against the normal application database.
TEST_DATABASE_PREFIX = "ss_test_"
WORKER_START_TIMEOUT = 20


@pytest.fixture(scope="session", autouse=True)
def database():
    # Provider credentials must never enter integration-test processes.
    provider_keys = [
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_WORKSPACE_ID",
        "MINERU_API_KEY",
        "CHATANYWHERE_API_KEY",
        "OPENROUTER_API_KEY",
        "EXTERNAL_SERVICES_ENABLED",
        "LANGSMITH_TRACING",
    ]
    previous_provider_environment = {key: os.environ.get(key) for key in provider_keys}
    for key in provider_keys:
        os.environ[key] = "false" if key in {"EXTERNAL_SERVICES_ENABLED", "LANGSMITH_TRACING"} else ""
    name = TEST_DATABASE_PREFIX + uuid4().hex
    admin = admin_engine("postgres")
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.connection.driver_connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    from scholarship.api import library

    previous_files = library.FILE_ROOT
    previous_data_env = os.environ.get("SCHOLAR_DATA_DIR")
    test_data = PROJECT_ROOT / "var/tests" / name
    os.environ["SCHOLAR_DATA_DIR"] = str(test_data)
    library.FILE_ROOT = test_data / "files"
    previous = os.environ.get("POSTGRES_DB")
    os.environ["POSTGRES_DB"] = name
    settings.cache_clear()
    db = admin_engine(name)
    try:
        migrate(db)
        migrate(db)  # repeat setup is part of the contract
        seed(db)
        yield db
    finally:
        # Background fixture has stopped before this session fixture tears down.
        # Reconcile exact collection mappings before dropping the only ownership manifest.
        from scholarship.infrastructure.health import qdrant_client

        with db.connect() as conn:
            mapped = conn.execute(
                sa.select(
                    tables["index_generations"].c.collection_name, tables["index_generations"].c.project_id
                ).distinct()
            ).all()
        vector_client = qdrant_client()
        try:
            for collection, project in mapped:
                if collection.startswith("ss_p_" + project.hex + "_") and vector_client.collection_exists(collection):
                    vector_client.delete_collection(collection)
        finally:
            vector_client.close()
        for role in ("runtime", "auth", "checkpoint"):
            engine(role).dispose()
        engine.cache_clear()
        db.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.connection.driver_connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )
        admin.dispose()
        if previous is None:
            os.environ.pop("POSTGRES_DB", None)
        else:
            os.environ["POSTGRES_DB"] = previous
        library.FILE_ROOT = previous_files
        if previous_data_env is None:
            os.environ.pop("SCHOLAR_DATA_DIR", None)
        else:
            os.environ["SCHOLAR_DATA_DIR"] = previous_data_env
        if test_data.parent == PROJECT_ROOT / "var/tests" and test_data.name.startswith(TEST_DATABASE_PREFIX):
            shutil.rmtree(test_data, ignore_errors=True)
        for key, previous_value in previous_provider_environment.items():
            if previous_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous_value
        settings.cache_clear()


@pytest.fixture(scope="session")
def accounts(database):
    with database.connect() as conn:
        users = conn.execute(sa.select(tables["users"])).mappings().all()
        projects = conn.execute(sa.select(tables["projects"])).mappings().all()
    return {
        u["username"]: {"user": dict(u), "project": dict(next(p for p in projects if p["owner_id"] == u["id"]))}
        for u in users
    }


@pytest.fixture
def client():
    with TestClient(create_app(), raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def signed_client(client):
    from dotenv import dotenv_values

    values = dotenv_values(PROJECT_ROOT / ".env")
    client.headers["Origin"] = "http://127.0.0.1:15173"
    response = client.post(
        "/api/v1/auth/login", json={"username": "researcher", "password": values["INITIAL_USER_PASSWORD"]}
    )
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = client.cookies["ss_csrf"]
    return client


@pytest.fixture(scope="session")
def background(database):
    from dev import application_env

    python = str(PROJECT_ROOT / ".venv/bin/python")
    log_path = PROJECT_ROOT / "var/logs/test-worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as log:
        processes = []
        try:
            for args in (
                [
                    python,
                    "-m",
                    "celery",
                    "-A",
                    "scholarship.workers.celery_app:app",
                    "worker",
                    "--pool=solo",
                    "--concurrency=1",
                    "--loglevel=WARNING",
                    "--without-gossip",
                    "--without-mingle",
                    "-Q",
                    "scholarship_test",
                    "-n",
                    "ss_test@%h",
                ],
                [
                    python,
                    "-m",
                    "celery",
                    "-A",
                    "scholarship.workers.celery_app:app",
                    "beat",
                    "--loglevel=WARNING",
                    "--schedule",
                    str(PROJECT_ROOT / "var/test-celerybeat"),
                ],
            ):
                # Separate queue: a development worker must never consume test IDs.
                env = {**application_env(), "CELERY_QUEUE": "scholarship_test"}
                processes.append(
                    subprocess.Popen(args, stdout=log, stderr=log, cwd=PROJECT_ROOT, env=env, start_new_session=True)
                )
            time.sleep(2)
            assert all(p.poll() is None for p in processes)
            yield processes
        finally:
            for p in processes:
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)
            for p in processes:
                try:
                    p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
