"""Separate simulated provider receipts from monetary billing."""

from pathlib import Path
from alembic import op

revision = "0006_model_runtime"
down_revision = "0005_offline_profile"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().connection.driver_connection.execute(
        (Path(__file__).parents[1] / "sql/0006_model_runtime.sql").read_text()
    )


def downgrade():
    raise RuntimeError("Model configuration snapshots and receipts must be retained.")
