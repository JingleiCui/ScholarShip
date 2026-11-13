"""Approved 41-table foundation and its tenant/security boundaries."""

from pathlib import Path
import json
import runpy

from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    catalog = json.loads((Path(__file__).parents[1] / "catalogs/0001.json").read_text())
    frozen = Path(__file__).parents[1] / "catalogs"
    runpy.run_path(str(frozen / "0001_schema.py"))["build_metadata"](catalog).create_all(op.get_bind())
    runpy.run_path(str(frozen / "0001_security.py"))["install_security"](op.get_bind(), catalog)


def downgrade():
    raise RuntimeError("Initial schema downgrade destroys research data. Restore a verified backup instead.")
