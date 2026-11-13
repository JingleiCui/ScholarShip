from contextlib import contextmanager
from functools import lru_cache
from uuid import UUID

import sqlalchemy as sa

from scholarship.config import settings


@lru_cache
def engine(role: str = "runtime") -> sa.Engine:
    return sa.create_engine(
        settings().dsn(role, sqlalchemy=True),
        hide_parameters=True,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "application_name": f"scholarship_{role}"},
    )


@contextmanager
def tenant_transaction(owner_id: UUID | str):
    owner = str(UUID(str(owner_id)))
    with engine().begin() as connection:
        connection.execute(sa.text("SELECT set_config('app.owner_id', :owner, true)"), {"owner": owner})
        yield connection
