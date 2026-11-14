"""Protocol-bound simulation profiles, preserving v1 compatibility and scope."""

from pathlib import Path
from alembic import op

revision = "0008_adapter_profiles"
down_revision = "0007_bounded_live"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().connection.driver_connection.execute(
        (Path(__file__).parents[1] / "sql/0008_adapter_profiles.sql").read_text()
    )


def downgrade():
    raise RuntimeError("Do not erase adapter-bound profile history.")
