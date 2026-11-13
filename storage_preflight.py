"""Read-only file migration check before binding one directory in both modes."""

from pathlib import Path
import hashlib
import sqlalchemy as sa
from scholarship.infrastructure.initialize import admin_engine

# USER CONFIGURATION: shared by local mode and Docker Compose.
SHARED_DATA_DIR = Path(__file__).resolve().parent / "var"
CHECK_HASHES = True


def verify_storage(db, data_dir=SHARED_DATA_DIR, check_hashes=CHECK_HASHES):
    root = (data_dir / "files").resolve()
    checked = missing = invalid = 0
    with db.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT storage_key,byte_size,sha256 FROM scholar.file_objects WHERE state='ready'")
        )
        for key, size, sha in rows:
            checked += 1
            path = (root / key).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                missing += 1
                continue
            if path.stat().st_size != size:
                invalid += 1
                continue
            if check_hashes:
                h = hashlib.sha256()
                with path.open("rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(chunk)
                if h.hexdigest() != sha:
                    invalid += 1
    return {"checked": checked, "missing": missing, "invalid": invalid, "passed": not (missing or invalid)}


def main():
    db = admin_engine()
    try:
        report = verify_storage(db)
        print(report)
        if not report["passed"]:
            raise SystemExit("File migration incomplete; deployment stopped. Existing volume is retained.")
    finally:
        db.dispose()


if __name__ == "__main__":
    main()
