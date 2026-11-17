"""Bind cloud figure calls to explicitly approved image blocks."""

from pathlib import Path
from alembic import op

revision = "0014_figure_scope"
down_revision = "0013_storage_manifest"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().connection.driver_connection.execute(
        (Path(__file__).parents[1] / "sql/0014_figure_scope.sql").read_text()
    )


def downgrade():
    raise RuntimeError("Use a forward migration for storage integrity access.")
