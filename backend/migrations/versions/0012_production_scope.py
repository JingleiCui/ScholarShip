"""Dispatch research studio and scheduled metadata checks; no model authorization."""

from pathlib import Path
from alembic import op

revision = "0012_production_scope"
down_revision = "0011_studio_dispatch"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().connection.driver_connection.execute(
        (Path(__file__).parents[1] / "sql/0012_production_scope.sql").read_text()
    )


def downgrade():
    raise RuntimeError("Do not remove durable studio dispatch.")
