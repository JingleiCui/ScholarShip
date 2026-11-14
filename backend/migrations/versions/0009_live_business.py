"""Proof-backed real profiles and separately closed production admission."""

from pathlib import Path
from alembic import op

revision = "0009_live_business"
down_revision = "0008_adapter_profiles"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().connection.driver_connection.execute(
        (Path(__file__).parents[1] / "sql/0009_live_business.sql").read_text()
    )


def downgrade():
    raise RuntimeError("Do not erase real profile or production usage history.")
