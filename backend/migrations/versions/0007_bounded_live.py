"""One-shot manifest-scoped live grants, no global inference enablement."""

from pathlib import Path
from alembic import op

revision = "0007_bounded_live"
down_revision = "0006_model_runtime"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().connection.driver_connection.execute(
        (Path(__file__).parents[1] / "sql/0007_bounded_live.sql").read_text()
    )


def downgrade():
    raise RuntimeError("Do not erase live authorization or request counters.")
