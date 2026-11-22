"""Independent local invocation receipts; never enables cloud policies."""

from pathlib import Path
from alembic import op

revision = "0015_local_inference"
down_revision = "0014_figure_scope"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().connection.driver_connection.execute(
        (Path(__file__).parents[1] / "sql/0015_local_inference.sql").read_text()
    )


def downgrade():
    raise RuntimeError("Use a forward migration; preserve invocation evidence.")
