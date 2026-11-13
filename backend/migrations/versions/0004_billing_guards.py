"""Reject expired admission windows and non-CNY price snapshots."""

from pathlib import Path
from alembic import op

revision = "0004_billing_guards"
down_revision = "0003_shared_billing"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().connection.driver_connection.execute(
        (Path(__file__).parents[1] / "sql/0004_billing_guards.sql").read_text()
    )


def downgrade():
    raise RuntimeError("Do not remove accounting safeguards from active calls.")
