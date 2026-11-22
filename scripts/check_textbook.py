"""Read-only regression of the existing private 541-page text-layer source."""

from pathlib import Path
from uuid import UUID
import hashlib
import json
import time
from pypdf import PdfReader
import sqlalchemy as sa
from scholarship.infrastructure.database import tenant_transaction
from scholarship.infrastructure.schema import tables as T
from scholarship.api.library import stored_file

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "var/reports/live-business-import.json"
HISTORICAL = ROOT / "var/reports/textbook-structure.json"
REPORT = ROOT / "var/reports/textbook-readonly-regression.json"
EXPECTED_PAGES = 541


def main():
    owner = UUID(json.loads(REFERENCE.read_text())["owner_id"])
    version = UUID(json.loads(HISTORICAL.read_text())["source_version_id"])
    with tenant_transaction(owner) as conn:
        path = stored_file(conn, version)
        expected = conn.scalar(
            sa.select(T["file_objects"].c.sha256)
            .join(T["paper_versions"], T["paper_versions"].c.file_id == T["file_objects"].c.id)
            .where(T["paper_versions"].c.id == version)
        )
    start = time.monotonic()
    with path.open("rb") as file:
        assert hashlib.file_digest(file, "sha256").hexdigest() == expected
    reader = PdfReader(path)
    assert len(reader.pages) == EXPECTED_PAGES
    empty = []
    total = 0
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        total += len(text)
        if not text.strip():
            empty.append(i)
    result = {
        "passed": True,
        "pages": len(reader.pages),
        "empty_text_pages": empty,
        "text_characters": total,
        "seconds": round(time.monotonic() - start, 2),
        "sha256_verified": True,
        "ocr_performed": False,
        "model_requests": 0,
        "uploads": 0,
        "historical_parse_unchanged": True,
        "text_completeness_verified": False,
        "limits": "Text-layer regression only; guarded repeated Form XObjects may be skipped by pypdf.",
    }
    REPORT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
