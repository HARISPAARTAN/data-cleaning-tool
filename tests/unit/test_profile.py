from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest

from cleandata.core.profile import load_profile, parse_profile, schema
from cleandata.errors import ProfileError
from support import PROFILES, ROOT, profile_of


def test_every_shipped_profile_validates() -> None:
    names = sorted(path.name for path in PROFILES.glob("*.yaml"))
    assert names == ["01_survey.yaml", "02_partner.yaml", "03_waves.yaml", "04_legacy.yaml", "05_large.yaml"]
    for path in PROFILES.glob("*.yaml"):
        profile, data = load_profile(path)
        assert profile.version == 1
        assert data == path.read_bytes()


def test_extra_keys_fail_with_their_path() -> None:
    text = "version: 1\nname: t\ncolumns:\n  age:\n    type: integer\n    mx: 99\n"
    with pytest.raises(ProfileError) as caught:
        parse_profile(text, "t.yaml")
    assert "columns.age.mx: Extra inputs are not permitted" in str(caught.value)


def test_yaml_errors_give_the_line() -> None:
    with pytest.raises(ProfileError, match="invalid YAML at line 3, column 1"):
        parse_profile("version: 1\nname: [\n", "t.yaml")


def test_yaml_11_booleans_must_be_quoted() -> None:
    text = "version: 1\nname: t\ncolumns:\n  opt_in:\n    type: string\n    allowed: [yes, no]\n"
    with pytest.raises(ProfileError, match="quote it"):
        parse_profile(text, "t.yaml")


@pytest.mark.parametrize(
    ("columns", "sections", "message"),
    [
        (
            {"a": {"type": "string"}},
            {"dedupe": {"keys": [{"columns": ["b"]}]}},
            "dedupe.keys.0.columns: column 'b'",
        ),
        ({"a": {"type": "string", "min": 1}}, {}, "only integer, number, date and datetime"),
        ({"a": {"type": "integer", "max": "x"}}, {}, "columns.a.max"),
        ({"a": {"type": "string", "missing": "median"}}, {}, "median needs an integer or number"),
        ({"a": {"type": "integer", "missing": {"strategy": "constant", "value": "x"}}}, {}, "missing.value"),
        (
            {"a": {"type": "string", "missing": {"strategy": "drop", "value": 1}}},
            {},
            "only the constant strategy",
        ),
        ({"a": {"type": "string", "regex": "("}}, {}, "does not compile"),
        ({"a": {"type": "string", "map": {"x": "y", "y": "z"}}}, {}, "a map must be idempotent"),
        ({"a": {"type": "date"}}, {"input": {"locale": {"date_formats": ["%d.%m"]}}}, "round-trip"),
        ({"a": {"type": "date"}}, {"input": {"locale": {"date_formats": ["daily"]}}}, "no % directive"),
        ({"a": {"type": "number"}}, {"input": {"locale": {"decimal": ",", "thousands": ","}}}, "must differ"),
        ({"a": {"type": "string"}}, {"headers": {"rename": {"x": "a", "y": "a"}}}, "more than one rename"),
        ({"a": {"type": "string"}}, {"headers": {"rename": {"x": "Bad Name"}}}, "not a snake_case name"),
        ({"_a": {"type": "string"}}, {}, "must not be empty or start with '_'"),
        (
            {"a": {"type": "string"}},
            {"dedupe": {"fuzzy": {"block_on": ["a"], "fields": [{"columns": ["a"]}], "cluster_column": "a"}}},
            "is already a column",
        ),
    ],
)
def test_cross_checks(columns: dict[str, object], sections: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        profile_of(columns, **sections)


def test_column_plans_resolve_defaults_and_overrides() -> None:
    profile = profile_of(
        {
            "income": {"type": "number", "locale": {"thousands": None}, "strip": ["€"]},
            "when": {"type": "date", "min": "2025-01-01"},
            "name": {"type": "string", "case": "title", "null_tokens": ["-"]},
        },
        input={"null_tokens": ["k.A."], "locale": {"decimal": ",", "thousands": "."}},
        report={"redact": ["name"]},
    )
    plans = profile.column_plans()
    assert (plans["income"].settings.decimal, plans["income"].settings.thousands) == (",", None)
    assert plans["income"].strip == ("€",)
    assert plans["when"].min == dt.date(2025, 1, 1)
    assert plans["name"].text.null_tokens == frozenset({"", "k.a.", "-"})
    assert plans["name"].text.settings.case == "title"
    assert plans["name"].redact


def test_canonical_profile_reads_canonical_csv() -> None:
    profile, _ = load_profile(PROFILES / "02_partner.yaml")
    canonical = profile.canonical()
    assert canonical.input.encoding == "utf-8"
    assert canonical.input.delimiter == ","
    assert canonical.headers.normalise == "keep"
    assert canonical.dedupe.fuzzy is None
    assert canonical.columns["dup_cluster"].type == "string"
    assert canonical.columns["dup_cluster"].optional
    assert canonical.text.unicode == "none"
    assert all(rule.map == {} and rule.strip == [] for rule in canonical.columns.values())


def test_schema_has_not_drifted() -> None:
    committed = json.loads((ROOT / "docs" / "profile.schema.json").read_text(encoding="utf-8"))
    assert committed == schema()


def test_example_profile_in_the_spec_shape() -> None:
    profile, _ = load_profile(PROFILES / "01_survey.yaml")
    assert profile.input.locale.decimal == ","
    assert [rule.columns for rule in profile.dedupe.keys] == [["respondent_id"]]
    assert profile.quality.max_reject_rate == 0.05
    assert (
        Path(PROFILES / "01_survey.yaml")
        .read_text(encoding="utf-8")
        .startswith("# yaml-language-server: $schema=../docs/profile.schema.json")
    )
