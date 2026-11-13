from alembic import context
from scholarship.infrastructure.schema import metadata

target_metadata = metadata


def run_migrations_online():
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("Run bootstrap.py or the migration entrypoint with an explicit migration connection")
    context.configure(
        connection=connection, target_metadata=target_metadata, include_schemas=True, version_table_schema="scholar"
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
