"""Standalone, deterministic CSV chart renderer. Never evaluates user code."""

from io import BytesIO
import csv
import json
import hashlib
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
from matplotlib.figure import Figure
from matplotlib import font_manager
from matplotlib.text import Text

# USER CONFIGURATION — exported scripts replace CONFIG with their exact snapshot.
CONFIG = {
    "kind": "line",
    "x": "name",
    "y": ["value"],
    "missing": "reject",
    "units": {},
    "title": "CSV chart",
    "z": None,
}
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 10000
MAX_COLUMNS = 32
CHINESE_FONTS = [
    "Noto Sans CJK SC",
    "Noto Sans CJK JP",
    "Noto Sans CJK TC",
    "PingFang SC",
    "Microsoft YaHei",
    "Heiti SC",
]
KINDS = {"table", "line", "bar", "scatter", "radar", "radar3d"}


def render_chart(raw, config):
    if len(raw) > MAX_BYTES or config.get("kind") not in KINDS:
        raise ValueError("invalid_chart_kind_or_csv_limit")
    text = raw.decode("utf-8-sig")
    config = dict(config)
    needs_chinese = any("\u3400" <= c <= "\u9fff" for c in text + json.dumps(config, ensure_ascii=False))
    available = {f.name for f in font_manager.fontManager.ttflist}
    family = config.get("font_family") or (
        next((f for f in CHINESE_FONTS if f in available), None) if needs_chinese else "DejaVu Sans"
    )
    if not family or family not in available:
        raise ValueError("chart_font_unavailable")
    config["font_family"] = family
    headers = next(csv.reader(text.splitlines()), [])
    if (
        not headers
        or len(headers) > MAX_COLUMNS
        or len(set(headers)) != len(headers)
        or any(not h.strip() for h in headers)
    ):
        raise ValueError("invalid_or_duplicate_csv_columns")
    df = pd.read_csv(BytesIO(raw), encoding="utf-8-sig", nrows=MAX_ROWS + 1)
    if df.empty or len(df) > MAX_ROWS:
        raise ValueError("csv_row_limit")
    kind = config["kind"]
    x = config.get("x")
    ys = config.get("y", [])
    z = config.get("z")
    if (
        x not in df
        or not isinstance(ys, list)
        or not 1 <= len(ys) <= 8
        or len(set(ys)) != len(ys)
        or any(y not in df or y == x for y in ys)
    ):
        raise ValueError("invalid_chart_columns")
    if kind == "radar3d" and (z not in df or z == x or z in ys):
        raise ValueError("real_third_dimension_required")
    numeric = list(dict.fromkeys(ys + ([x] if kind == "scatter" else []) + ([z] if kind == "radar3d" else [])))
    if kind == "scatter" and len(ys) != 1:
        raise ValueError("scatter_requires_one_y")
    if kind in {"radar", "radar3d"} and (len(ys) < 3 or len(df) > 12):
        raise ValueError("radar_requires_3_features_max_12_rows")
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="raise")
    if config.get("missing", "reject") not in {"reject", "drop"}:
        raise ValueError("invalid_missing_policy")
    missing = int(df[[x, *numeric]].isna().any(axis=1).sum())
    if missing and config.get("missing", "reject") != "drop":
        raise ValueError("csv_missing_values_require_explicit_drop")
    if missing:
        df = df.dropna(subset=list(dict.fromkeys([x, *numeric])))
    if df.empty or not np.isfinite(df[numeric].to_numpy(dtype=float)).all():
        raise ValueError("nonfinite_or_empty_csv")
    if kind == "table" and len(df) > 100:
        raise ValueError("table_render_max_100_rows")
    fig = Figure(figsize=(10, 6), layout="constrained")
    units = config.get("units", {})
    if not isinstance(units, dict) or any(not isinstance(v, str) or len(v) > 80 for v in units.values()):
        raise ValueError("invalid_units")
    bounds = {}
    ax = fig.add_subplot(111, projection="3d" if kind == "radar3d" else "polar" if kind == "radar" else None)
    labels = df[x].astype(str).tolist()
    if kind == "table":
        ax.axis("off")
        ax.table(cellText=df[[x, *ys]].astype(str).values, colLabels=[x, *ys], loc="center")
    elif kind == "scatter":
        ax.scatter(df[x], df[ys[0]])
        ax.set_xlabel(f"{x} ({units.get(x, 'unspecified')})")
        ax.set_ylabel(f"{ys[0]} ({units.get(ys[0], 'unspecified')})")
    elif kind in {"line", "bar"}:
        positions = np.arange(len(df))
        for i, y in enumerate(ys):
            if kind == "line":
                ax.plot(positions, df[y], label=f"{y} ({units.get(y, 'unspecified')})")
            else:
                ax.bar(
                    positions + (i - (len(ys) - 1) / 2) * 0.8 / len(ys),
                    df[y],
                    width=0.8 / len(ys),
                    label=f"{y} ({units.get(y, 'unspecified')})",
                )
        ticks = np.linspace(0, len(df) - 1, min(15, len(df)), dtype=int)
        ax.set_xticks(ticks, [labels[i] for i in ticks], rotation=30)
        ax.legend()
        ax.set_xlabel(x)
        ax.set_ylabel("source values; see series units")
    else:
        angles = np.linspace(0, 2 * np.pi, len(ys), endpoint=False)
        angles = np.append(angles, angles[0])
        values = df[ys].astype(float).copy()
        for y in ys:
            lo, hi = float(values[y].min()), float(values[y].max())
            bounds[y] = [lo, hi]
            values[y] = 0 if hi == lo else (values[y] - lo) / (hi - lo)
        for idx, (_, row) in enumerate(values.iterrows()):
            radius = np.append(row.to_numpy(), row.iloc[0])
            if kind == "radar":
                ax.plot(angles, radius, label=labels[idx])
                ax.set_ylim(0, 1)
            else:
                ax.plot(
                    radius * np.cos(angles),
                    radius * np.sin(angles),
                    np.repeat(float(df.iloc[idx][z]), len(radius)),
                    label=labels[idx],
                )
        if kind == "radar":
            ax.set_xticks(angles[:-1], ys)
        else:
            ax.set_xlabel("normalized polar x")
            ax.set_ylabel("normalized polar y")
            ax.set_zlabel(f"{z} ({units.get(z, 'unspecified')})")
        ax.legend()
        ax.set_title("per-column min-max normalization; constant features = 0")
    fig.suptitle(str(config.get("title", "CSV chart"))[:160])
    output = BytesIO()
    for item in fig.findobj(match=Text):
        item.set_fontfamily(family)
    fig.savefig(output, format="png", dpi=160)
    summary = {y: {"min": float(df[y].min()), "max": float(df[y].max()), "mean": float(df[y].mean())} for y in ys}
    meta = {
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "font_family": family,
        "rows_used": len(df),
        "rows_dropped": missing,
        "units": units,
        "normalization_bounds": bounds,
        "third_dimension": z if kind == "radar3d" else None,
        "summary": summary,
        "config": config,
    }
    # A computed caption does not invent statistical significance.
    meta["caption"] = f"{kind}: {len(df)} source rows; dropped {missing}. " + "; ".join(
        f"{y}: min={s['min']:.6g}, max={s['max']:.6g}, mean={s['mean']:.6g}" for y, s in summary.items()
    )
    return output.getvalue(), meta


if __name__ == "__main__":
    from pathlib import Path

    png, metadata = render_chart(Path("input.csv").read_bytes(), CONFIG)
    Path("chart.png").write_bytes(png)
    Path("metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
