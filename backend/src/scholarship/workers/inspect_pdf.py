"""Bounded subprocess PDF inspection; emits only validated metadata JSON."""

import json
import sys
from pypdf import PdfReader
from scholarship.domains.identifiers import normalize_identifiers
from scholarship.domains.document_identity import inspect_content


def main():
    pdf = PdfReader(sys.argv[1])
    if pdf.is_encrypted:
        print(json.dumps({"error": "encrypted_pdf_unsupported"}))
        return
    content = inspect_content(pdf) if 0 < len(pdf.pages) <= 2000 else {}
    first_page = content.pop("first_page", "")
    print(
        json.dumps(
            {
                "pages": len(pdf.pages),
                **content,
                "title": content.get("title", ""),
                "identifiers": normalize_identifiers(
                    first_page[:50000] + " " + (sys.argv[2] if len(sys.argv) > 2 else "")
                )
                if 0 < len(pdf.pages) <= 2000
                else [],
            }
        )
    )


if __name__ == "__main__":
    main()
