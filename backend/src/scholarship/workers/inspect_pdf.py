"""Bounded subprocess PDF inspection; emits only validated metadata JSON."""

import json
import sys
from pypdf import PdfReader
from scholarship.domains.identifiers import normalize_identifiers


def main():
    pdf = PdfReader(sys.argv[1])
    if pdf.is_encrypted:
        print(json.dumps({"error": "encrypted_pdf_unsupported"}))
        return
    print(
        json.dumps(
            {
                "pages": len(pdf.pages),
                "title": str((pdf.metadata or {}).get("/Title", ""))[:2000].strip(),
                "identifiers": normalize_identifiers(
                    (pdf.pages[0].extract_text() or "")[:50000] + " " + (sys.argv[2] if len(sys.argv) > 2 else "")
                )
                if 0 < len(pdf.pages) <= 2000
                else [],
            }
        )
    )


if __name__ == "__main__":
    main()
