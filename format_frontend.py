"""Format research-workspace sources with the project's pinned formatter."""

from pathlib import Path
import subprocess
from bootstrap import pnpm_args, tool_env

# USER CONFIGURATION — IDE Run is enough; no global package tools are used.
ROOT = Path(__file__).resolve().parent
CHECK_ONLY = False
FILES = [
    "src/main.tsx",
    "src/Library.tsx",
    "src/ModelConfiguration.tsx",
    "src/Research.tsx",
    "src/ServiceStatus.tsx",
    "src/SourceReader.tsx",
    "src/researchApi.ts",
    "src/Studio.tsx",
    "src/BilingualReader.tsx",
    "src/CitationGraph.tsx",
    "src/DiagramPreview.tsx",
    "src/ScientificText.tsx",
    "src/ScientificText.test.tsx",
    "src/styles.css",
]


def main():
    result = subprocess.run(
        pnpm_args("exec", "prettier", "--check" if CHECK_ONLY else "--write", *FILES),
        cwd=ROOT / "frontend",
        env=tool_env(),
        check=False,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
