"""Proof-backed real profiles and separately closed production admission."""

from pathlib import Path
from alembic import op

revision = "0010_profile_input_policy"
down_revision = "0009_live_business"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().connection.driver_connection.execute(
        (Path(__file__).parents[1] / "sql/0010_profile_input_policy.sql").read_text()
    )


def downgrade():
    raise RuntimeError("Do not erase real profile or production usage history.")
