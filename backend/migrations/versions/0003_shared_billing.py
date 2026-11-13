"""Project-wide accounting gate, separate from the 41 research business tables."""

from pathlib import Path
from alembic import op

revision = "0003_shared_billing"
down_revision = "0002_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().connection.driver_connection.execute((Path(__file__).parents[1] / "sql/0003_billing.sql").read_text())


def downgrade():
    raise RuntimeError("Accounting downgrade would discard durable reservations; restore a verified backup.")
