from __future__ import annotations

import re

from cleandata.report.build import round_floats, to_json
from cleandata.report.html import flow_segments, number, stylesheet
from support import ROOT

TOKEN = re.compile(r"--([a-z0-9-]+):\s*([^;]+);")


def test_floats_are_rounded_to_six_significant_digits() -> None:
    assert round_floats({"a": [1 / 3, 123456789.0, float("nan")], "b": True}) == {
        "a": [0.333333, 123457000.0, None],
        "b": True,
    }
    assert to_json({"x": 2 / 3}) == '{\n  "x": 0.666667\n}\n'


def test_number_formatting_and_flow_segments() -> None:
    assert (number(None), number(1234567), number(0.5)) == ("\N{EN DASH}", "1,234,567", "0.5")
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


def test_kit_token_values_equal_web_kit_tokens() -> None:
    kit = blocks((ROOT / "web" / "kit" / "tokens.css").read_text(encoding="utf-8"))
    ours = blocks(stylesheet())
    for selector, tokens in ours.items():
        colours = {name: value for name, value in tokens.items() if value.startswith("#")}
        assert colours
        for name, value in colours.items():
            assert kit[selector].get(name, "").upper() == value.upper(), (selector, name)
