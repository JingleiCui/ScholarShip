"""Check public documentation, including links to accidentally private material."""

from pathlib import Path
from urllib.parse import unquote
import json
import re
import subprocess

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "var/reports/documentation-check.json"


def main():
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT, text=True
    ).split("\0")
    errors = []
    links = 0
    for name in sorted(set(names)):
        if not name.endswith(".md"):
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        text = path.read_text()
        targets = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text)
        targets += re.findall(r'(?:src|href)="([^"]+)"', text)
        for target in targets:
            target = target.strip("<>").split("#")[0]
            if not target or re.match(r"^[a-z]+://", target):
                continue
            links += 1
            destination = (path.parent / unquote(target)).resolve()
            try:
                relative = str(destination.relative_to(ROOT))
            except ValueError:
                errors.append({"file": name, "outside_repository": target})
                continue
            ignored = subprocess.run(["git", "check-ignore", "-q", relative], cwd=ROOT).returncode == 0
            if ignored or not destination.exists():
                errors.append({"file": name, "private_or_missing": target})
    result = {"passed": not errors, "local_links_checked": links, "errors": errors}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
