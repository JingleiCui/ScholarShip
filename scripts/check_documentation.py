"""Validate current documentation links and required private deliverables."""

from pathlib import Path
from urllib.parse import unquote
import json
import re

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    "README.md",
    "README.en.md",
    "docs/README.md",
    "docs/guides/user-manual.md",
    "docs/engineering/architecture-current.md",
    "docs/engineering/code-walkthrough.md",
    "docs/engineering/api-and-debugging.md",
    "docs/engineering/diagrams.md",
    "private/README.md",
]
REQUIRED = [
    "private/interview/core-questions.md",
    "private/interview/deep-dives.md",
    "private/interview/presentation-and-practice.md",
    "private/competition/technical-document.md",
    "private/competition/video-script.md",
    "private/competition/submission-checklist.md",
    "private/output/pdf/ScholarShip-technical-document.pdf",
]
REPORT = ROOT / "var/reports/documentation-check.json"


def main():
    errors = []
    links = 0
    for name in TARGETS:
        path = ROOT / name
        for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", path.read_text()):
            target = target.strip("<>").split("#")[0]
            if not target or re.match(r"^[a-z]+://", target):
                continue
            links += 1
            if not (path.parent / unquote(target)).exists():
                errors.append({"file": name, "missing": target})
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            errors.append({"missing": name})
    result = {
        "passed": not errors,
        "local_links_checked": links,
        "required_deliverables": len(REQUIRED),
        "errors": errors,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
