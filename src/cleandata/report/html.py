"""Render a report dictionary (``clean`` or ``profile``) to one self-contained HTML file.

The CSS is inlined from ``report.css`` and the only script is the few lines that apply the stored
theme; the page makes no request to any host and reads fine without JavaScript.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Mapping
from typing import Any, Final

import jinja2

FLOW_WIDTH: Final = 1000
FLOW_PARTS: Final = (
    ("out", "kept"),
    ("read", "rejected at read"),
    ("validate", "rejected by validation"),
    ("missing", "dropped for missing values"),
    ("duplicates", "duplicates"),
    ("empty", "empty records"),
)


def number(value: object) -> str:
    """Thousands with a comma, floats with up to 6 significant digits, null as an en dash."""
    if value is None:
        return "\N{EN DASH}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        text = f"{value:,.6g}"
        # Large values are rounded to 6 significant digits upstream, so plain digits lose nothing;
        # tiny values keep the exponent, since plain digits would show them as 0.
        return f"{value:,.0f}" if "e" in text and abs(value) >= 1 else text
    return str(value)


def percent(value: object) -> str:
    if not isinstance(value, int | float):
        return "\N{EN DASH}"
    return f"{value * 100:.1f} %"


def flow_segments(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Stacked-bar segments in a 1000-unit wide SVG; zero-sized parts are left out."""
    rows = report["rows"]
    stages = report["rejects"]["by_stage"]
    counts = {
        "out": rows["out"],
        "read": stages["read"],
        "validate": stages["validate"],
        "missing": stages["missing"],
        "duplicates": rows["duplicates"],
        "empty": rows["empty"],
    }
    total = rows["read"] or 1
    segments: list[dict[str, Any]] = []
    x = 0.0
    for key, label in FLOW_PARTS:
        count = counts[key]
        if count <= 0:
            continue
        width = FLOW_WIDTH * count / total
        segments.append(
            {"key": key, "label": label, "count": count, "x": round(x, 2), "width": round(width, 2)}
        )
        x += width
    return segments


def _environment() -> jinja2.Environment:
    environment = jinja2.Environment(
        loader=jinja2.PackageLoader("cleandata.report", "templates"),
        autoescape=jinja2.select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )
    environment.filters["number"] = number
    environment.filters["percent"] = percent
    return environment


def stylesheet() -> str:
    return importlib.resources.files("cleandata.report").joinpath("report.css").read_text(encoding="utf-8")


def render_html(report: Mapping[str, Any]) -> str:
    template = _environment().get_template("report.html.j2")
    segments = flow_segments(report) if report["kind"] == "clean" else []
    return template.render(report=report, css=stylesheet(), segments=segments)
