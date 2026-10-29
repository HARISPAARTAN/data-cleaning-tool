from __future__ import annotations

import dataclasses
import json
import re
from typing import Any

import pytest

from cleandata.core.headers import HeaderPlan
from cleandata.core.ledger import Ledger
from cleandata.core.reader import SourceInfo
from cleandata.report.build import ReportInput, build_report, round_floats, to_json
from cleandata.report.html import flow_segments, number, render_html, stylesheet
from support import ROOT, profile_of

TOKEN = re.compile(r"--([a-z0-9-]+):\s*([^;]+);")
LIGHT_MEDIA = "@media (prefers-color-scheme: light)"
LIGHT_SELECTOR = ':root[data-theme="light"]'
AFTER = {
    "count": 1,
    "nulls": 0,
    "distinct": 1,
    "distinct_capped": False,
    "min": None,
    "max": None,
    "mean": None,
    "median": None,
    "top": None,
    "filled": 0,
}


def minimal_report(
    rows: int = 0, rejected: int = 0, columns: dict[str, Any] | None = None, preview: list[Any] | None = None
) -> dict[str, Any]:
    """A hand-built ``clean`` report with exactly the keys the template reads."""
    return {
        "schema_version": 1,
        "kind": "clean",
        "title": "Minimal",
        "tool": {"name": "clean-data", "version": "0", "python": "3.11", "packages": {}},
        "run": {
            "started_at": "2026-10-29T00:00:00Z",
            "duration_s": None,
            "mode": "in_memory",
            "chunk_size": None,
            "dry_run": False,
            "strict": False,
            "status": "ok",
            "exit_code": 0,
        },
        "input": {"name": "in.csv", "sha256": "0" * 64, "kind": "csv"},
        "profile": {"name": "minimal", "file": "minimal.yaml", "sha256": "1" * 64},
        "headers": [],
        "rows": {
            "read": rows,
            "empty": 0,
            "rejected": rejected,
            "duplicates": 0,
            "out": rows - rejected,
            "reject_rate": 0.0,
            "max_reject_rate": None,
            "by_rule": {},
        },
        "columns": columns or {},
        "rules": [],
        "dedupe": {"exact": None, "keys": [], "fuzzy": None},
        "rejects": {
            "file": None,
            "rows": rejected,
            "by_stage": {"read": 0, "validate": rejected, "dedupe": 0, "missing": 0},
            "by_rule": {},
            "by_column": {},
            "preview": preview or [],
        },
        "warnings": [],
        "notes": [],
        "explain": None,
        "banner": None,
        "footer": None,
    }


def report_input(**changes: Any) -> ReportInput:
    """A zero-row ``ReportInput`` built from the real classes, for one column ``id``."""
    profile = profile_of({"id": {"type": "string"}})
    data = ReportInput(
        profile=profile,
        profile_name="test.yaml",
        profile_sha256="0" * 64,
        plans=profile.column_plans(),
        info=SourceInfo("in.csv", 10, "1" * 64, "csv", encoding="utf-8", delimiter=",", header_row=1),
        header_plan=HeaderPlan((), (), ()),
        ledger=Ledger(5, 5),
        stats={"id": {"before": None, "after": AFTER}},
        changed={},
        columns=["id"],
        data_columns=["id"],
        dry_run=False,
        strict=False,
        chunked=False,
        chunk_size=None,
        status="ok",
        exit_code=0,
        started_at="2026-10-29T00:00:00Z",
        duration=None,
        rejects_file=None,
        explain=None,
    )
    return dataclasses.replace(data, **changes)


def test_floats_are_rounded_to_six_significant_digits() -> None:
    assert round_floats({"a": [1 / 3, 123456789.0, float("nan")], "b": True}) == {
        "a": [0.333333, 123457000.0, None],
        "b": True,
    }
    assert to_json({"x": 2 / 3}) == '{\n  "x": 0.666667\n}\n'


def test_number_formatting_and_flow_segments() -> None:
    assert (number(None), number(1234567), number(0.5)) == ("\N{EN DASH}", "1,234,567", "0.5")
    assert (number(1234567.0), number(1e6), number(-2.5e9)) == ("1,234,567", "1,000,000", "-2,500,000,000")
    assert (number(1234.5), number(1.5e-07)) == ("1,234.5", "1.5e-07")
    fake = {
        "rows": {"read": 10, "out": 5, "duplicates": 2, "empty": 1},
        "rejects": {"by_stage": {"read": 0, "validate": 2, "missing": 0}},
    }
    segments = flow_segments(fake)
    assert [(s["key"], s["x"], s["width"]) for s in segments] == [
        ("out", 0.0, 500.0),
        ("validate", 500.0, 200.0),
        ("duplicates", 700.0, 200.0),
        ("empty", 900.0, 100.0),
    ]


def blocks(css: str) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for selector in (":root", ':root[data-theme="light"]'):
        match = re.search(rf"^{re.escape(selector)}\s*\{{(.*?)^\}}", css, re.MULTILINE | re.DOTALL)
        assert match is not None, selector
        found[selector] = dict(TOKEN.findall(match.group(1)))
    return found


def media_light_block(css: str) -> dict[str, str]:
    """The tokens of ``@media (prefers-color-scheme: light) { :root:not([data-theme="dark"]) { ... } }``."""
    match = re.search(
        rf'{re.escape(LIGHT_MEDIA)}\s*\{{\s*:root:not\(\[data-theme="dark"\]\)\s*\{{(.*?)\}}', css, re.DOTALL
    )
    assert match is not None, LIGHT_MEDIA
    return dict(TOKEN.findall(match.group(1)))


def test_kit_token_values_equal_web_kit_tokens() -> None:
    kit = blocks((ROOT / "web" / "kit" / "tokens.css").read_text(encoding="utf-8"))
    ours = blocks(stylesheet())
    # The kit switches theme in script; the report also follows the system setting, in the kit's light.
    ours[LIGHT_MEDIA] = media_light_block(stylesheet())
    expected = {**kit, LIGHT_MEDIA: kit[LIGHT_SELECTOR]}
    for selector, tokens in ours.items():
        colours = {name: value for name, value in tokens.items() if value.startswith("#")}
        assert colours
        for name, value in colours.items():
            assert expected[selector].get(name, "").upper() == value.upper(), (selector, name)


def test_names_and_cells_are_escaped_in_the_html() -> None:
    name = "<script>alert(1)</script>"
    preview = [
        {
            "source_row": 3,
            "stage": "validate",
            "rule": "x.max",
            "detail": "too big",
            "values": {name: 'a & "b"'},
        }
    ]
    columns = {
        name: {
            "type": "string",
            "source": "profile",
            "before": None,
            "after": AFTER,
            "coercion": None,
            "missing": None,
            "changed_cells": 0,
        }
    }
    html = render_html(minimal_report(rows=2, rejected=1, columns=columns, preview=preview))
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&amp;" in html
    assert "a &amp; &#34;b&#34;" in html
    assert "<script>alert(1)" not in html
    assert html.count("<script>") == 1  # only the stored-theme script
    assert "<svg" in html


def test_a_zero_row_run_renders_without_the_flow_chart() -> None:
    html = render_html(minimal_report())
    assert '<h2 id="flow">Row flow</h2>' in html
    assert "<svg" not in html
    assert 'class="legend"' not in html


def test_to_json_is_deterministic() -> None:
    report = minimal_report(rows=3)
    report["columns"] = {"x": {"mean": 2 / 3}}
    first, second = to_json(report), to_json(report)
    assert first == second
    assert json.loads(first)["columns"]["x"]["mean"] == 0.666667


def test_a_dry_run_is_marked_once() -> None:
    report = build_report(report_input(dry_run=True))
    assert report["title"] == "test"
    html = render_html(report)
    assert html.count("Dry run") == 1
    assert re.search(r"<h1>test\s*<span", html)


def test_a_column_without_statistics_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="no statistics for column 'id'"):
        build_report(report_input(stats={}))
