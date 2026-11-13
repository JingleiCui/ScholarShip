"""SQLAlchemy Core metadata generated from the reviewed, versioned catalog."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

CATALOG_PATH = Path(__file__).with_name("schema_catalog.json")
REVISION = "0006_model_runtime"


def constraint_name(kind: str, table: str, detail: str) -> str:
    digest = hashlib.sha256(detail.encode()).hexdigest()[:12]
    return f"{kind}_{table}_{digest}"[:63]


def build_metadata(catalog: dict | None = None) -> sa.MetaData:
    catalog = catalog or json.loads(CATALOG_PATH.read_text())
    metadata = sa.MetaData(schema="scholar")
    types = {
        "uuid": UUID(as_uuid=True),
        "text": sa.Text(),
        "jsonb": JSONB(),
        "timestamptz": sa.DateTime(timezone=True),
        "date": sa.Date(),
        "integer": sa.Integer(),
        "bigint": sa.BigInteger(),
        "smallint": sa.SmallInteger(),
        "boolean": sa.Boolean(),
        "double precision": sa.Double(),
        "numeric(18,6)": sa.Numeric(18, 6),
        "varchar(64)": sa.String(64),
        "text[]": sa.ARRAY(sa.Text()),
    }
    for spec in catalog["tables"]:
        columns = [
            sa.Column(
                name,
                types[col["type"]],
                nullable=col["nullable"],
                primary_key=name in spec["primary_key"],
                server_default=sa.text(col["default"]) if "default" in col else None,
            )
            for name, col in spec["columns"].items()
        ]
        table = sa.Table(spec["name"], metadata, *columns)
        for keys in spec["unique_keys"]:
            if keys != spec["primary_key"]:
                table.append_constraint(
                    sa.UniqueConstraint(*keys, name=constraint_name("uq", spec["name"], ",".join(keys)))
                )
        for check in spec["checks"]:
            table.append_constraint(sa.CheckConstraint(check, name=constraint_name("ck", spec["name"], check)))
        for index in spec["indexes"]:
            sa.Index(
                constraint_name("ix", spec["name"], json.dumps(index, sort_keys=True)),
                *[table.c[x] for x in index["columns"]],
                postgresql_where=sa.text(index["where"]) if index.get("where") else None,
            )
    for spec in catalog["tables"]:
        for fk in spec["foreign_keys"]:
            name = constraint_name("fk", spec["name"], json.dumps(fk, sort_keys=True))
            metadata.tables[f"scholar.{spec['name']}"].append_constraint(
                sa.ForeignKeyConstraint(
                    fk["columns"],
                    [f"scholar.{fk['target_table']}.{x}" for x in fk["target_columns"]],
                    name=name,
                    ondelete="RESTRICT",
                    use_alter=True,
                )
            )
    return metadata


metadata = build_metadata()
tables = {name.removeprefix("scholar."): table for name, table in metadata.tables.items()}
