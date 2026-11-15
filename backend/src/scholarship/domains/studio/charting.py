from scholarship.domains.studio.artifacts import write_member
from io import BytesIO
import json
from pathlib import Path
import zipfile
from scholarship.domains.studio import chart_renderer


def chart_bundle(raw, config):
    png, metadata = chart_renderer.render_chart(raw, config)
    source = Path(chart_renderer.__file__).read_text()
    import ast

    assignment = next(
        n
        for n in ast.parse(source).body
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CONFIG" for t in n.targets)
    )
    lines = source.splitlines(keepends=True)
    a = sum(len(line) for line in lines[: assignment.lineno - 1])
    b = sum(len(line) for line in lines[: assignment.end_lineno]) - 1
    script = source[:a] + "CONFIG = " + repr(metadata["config"]) + source[b:]
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        write_member(z, "input.csv", raw)
        write_member(z, "chart.png", png)
        write_member(z, "plot.py", script)
        write_member(z, "metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        write_member(
            z,
            "README.txt",
            "Run plot.py with ScholarShip Python 3.12 environment and locked pandas/numpy/matplotlib dependencies. Install the font family pinned in CONFIG (Chinese: Noto Sans CJK SC). No model code is executed. Units marked unspecified require author confirmation.\n",
        )
    return out.getvalue(), metadata
