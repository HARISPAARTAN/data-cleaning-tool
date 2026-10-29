from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

import export_schema
from cleandata.core.encodings import require_text_encoding
from cleandata.core.profile import Locale, Profile, load_profile, parse_profile, schema
from cleandata.core.text import TextSettings
from cleandata.errors import ProfileError
from support import PROFILES, ROOT, profile_of

SCHEMA_FILE = ROOT / "docs" / "profile.schema.json"
HEAD = "version: 1\nname: t\ncolumns:\n"
JSON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def parse_dict(columns: dict[str, object], **sections: object) -> Profile:
    """``parse_profile`` on a profile written as JSON (which is YAML), so errors carry their paths."""
    document = {"version": 1, "name": "t", "columns": columns, **sections}
    return parse_profile(json.dumps(document), "t.yaml")


def conforms(value: object, node: dict[str, Any], root: dict[str, Any]) -> bool:
    """A minimal structural JSON Schema check: $ref, anyOf, type, enum, const, object and array shape."""
    if "$ref" in node:
        target: Any = root
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        if not conforms(value, target, root):
            return False
    if "anyOf" in node and not any(conforms(value, option, root) for option in node["anyOf"]):
        return False
    if "enum" in node and value not in node["enum"]:
        return False
    if "const" in node and value != node["const"]:
        return False
    kind = node.get("type")
    if kind is not None:
        if not isinstance(value, JSON_TYPES[kind]):
            return False
        if kind in ("integer", "number") and isinstance(value, bool):
            return False
    if isinstance(value, dict):
        properties = node.get("properties", {})
        if any(key not in value for key in node.get("required", [])):
            return False
        extra = node.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                if not conforms(item, properties[key], root):
                    return False
            elif extra is False:
                return False
            elif isinstance(extra, dict) and not conforms(item, extra, root):
                return False
    if isinstance(value, list) and "items" in node:
        return all(conforms(item, node["items"], root) for item in value)
    return True


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
        *[
            (
                {"a": {"type": "string"}},
                {
                    "dedupe": {
                        "fuzzy": {"block_on": ["a"], "fields": [{"columns": ["a"]}], "cluster_column": name}
                    }
                },
                f"dedupe.fuzzy.cluster_column: '{name}' must not start with '_'",
            )
            for name in ("_source_row", "_stage", "_rule", "_detail", "_mine")
        ],
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


def test_a_profile_that_is_not_utf8_is_a_profile_error() -> None:
    umlaut = "\N{LATIN SMALL LETTER A WITH DIAERESIS}"
    data = f"version: 1\nname: K{umlaut}se\ncolumns:\n  a:\n    type: string\n".encode("cp1252")
    with pytest.raises(ProfileError, match=r"t\.yaml: the profile must be UTF-8 text \(.+ at byte 18\)"):
        parse_profile(data, "t.yaml")


def test_a_utf8_byte_order_mark_is_accepted() -> None:
    data = b"\xef\xbb\xbf" + (HEAD + "  a:\n    type: string\n").encode("utf-8")
    assert list(parse_profile(data).columns) == ["a"]


@pytest.mark.parametrize(
    ("text", "key", "line", "column"),
    [
        (HEAD + "  a:\n    type: string\n  a:\n    type: integer\n", "a", 6, 3),
        (HEAD + "  a:\n    type: string\n    type: integer\n", "type", 6, 5),
        ("version: 1\nname: t\nname: u\ncolumns:\n  a:\n    type: string\n", "name", 3, 1),
        (HEAD + "  a:\n    type: string\n    map: {x: y, x: z}\n", "x", 6, 17),
    ],
)
def test_duplicate_yaml_keys_are_rejected_with_their_position(
    text: str, key: str, line: int, column: int
) -> None:
    with pytest.raises(ProfileError) as caught:
        parse_profile(text, "t.yaml")
    message = str(caught.value)
    assert f"duplicate key '{key}'" in message
    assert f"invalid YAML at line {line}, column {column}" in message


def test_merge_keys_and_anchors_still_work_and_may_be_overridden() -> None:
    text = (
        HEAD
        + "  a: &base\n    type: integer\n    min: 1\n    optional: true\n"
        + "  b:\n    <<: *base\n    optional: false\n    min: 2\n"
    )
    columns = parse_profile(text, "t.yaml").columns
    assert (columns["b"].type, columns["b"].optional, columns["b"].min) == ("integer", False, 2)
    assert (columns["a"].type, columns["a"].optional, columns["a"].min) == ("integer", True, 1)


def test_a_merged_anchor_with_its_own_overrides_is_not_a_duplicate() -> None:
    # `input` merges `loc` before `loc` itself is built, so its merged keys must not read as repeats
    text = (
        HEAD
        + '  a:\n    type: number\n    locale: &loc\n      <<: &seed {decimal: ",", thousands: "."}\n'
        + '      thousands: " "\n'
        + "input:\n  locale:\n    <<: *loc\n"
    )
    profile = parse_profile(text, "t.yaml")
    assert (profile.input.locale.decimal, profile.input.locale.thousands) == (",", " ")
    assert profile.effective_locale("a").thousands == " "


def test_the_schema_accepts_the_missing_shorthand() -> None:
    document = schema()
    missing = document["$defs"]["ColumnRule"]["properties"]["missing"]
    strategies = {"type": "string", "enum": ["keep", "drop", "constant", "median", "ffill"]}
    assert any({key: option.get(key) for key in strategies} == strategies for option in missing["anyOf"]), (
        missing
    )

    def with_missing(value: object) -> dict[str, object]:
        return {"version": 1, "name": "t", "columns": {"a": {"type": "number", "missing": value}}}

    for good in ("median", {"strategy": "median"}, {"strategy": "constant", "value": 0}):
        assert conforms(with_missing(good), document, document), good
    for bad in ("bogus", {"strategy": "bogus"}, {"strategy": "keep", "extra": 1}, 3):
        assert not conforms(with_missing(bad), document, document), bad


def test_the_schema_validates_every_shipped_profile() -> None:
    document = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    shorthand = 0
    for path in sorted(PROFILES.glob("*.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert conforms(loaded, document, document), path.name
        shorthand += sum(isinstance(rule.get("missing"), str) for rule in loaded["columns"].values())
    assert shorthand >= 1  # the survey profile writes `missing: median`
    typo = {"version": 1, "name": "t", "columns": {"a": {"type": "string", "mx": 1}}}
    assert not conforms(typo, document, document)


def test_default_text_plan_holds_the_empty_token_and_the_text_defaults() -> None:
    plain = profile_of(
        {"a": {"type": "string"}}, input={"null_tokens": []}, text={"case": "lower", "trim": False}
    )
    plan = plain.default_text_plan()
    assert "" in plan.null_tokens
    assert plan.null_tokens == frozenset({""})
    assert plan.settings == TextSettings(
        unicode="NFC", trim=False, collapse_whitespace=True, remove_control=True, case="lower"
    )
    padded = profile_of({"a": {"type": "string"}}, input={"null_tokens": [" K.A. "]})
    assert padded.default_text_plan().null_tokens == frozenset({"", "k.a."})


def test_locale_settings_trim_and_casefold_the_boolean_sets() -> None:
    settings = Locale(true_values=[" JA ", "Yes", "WAHR"], false_values=["Nein", " NO\t"]).settings()
    assert settings.true_values == frozenset({"ja", "yes", "wahr"})
    assert settings.false_values == frozenset({"nein", "no"})


def test_null_tokens_are_trimmed_and_casefolded() -> None:
    profile = profile_of(
        {"a": {"type": "string", "null_tokens": [" N/A\t", "-"]}, "b": {"type": "string"}},
        input={"null_tokens": [" K.A. "]},
    )
    plans = profile.column_plans()
    assert plans["a"].text.null_tokens == frozenset({"", "k.a.", "n/a", "-"})
    assert plans["b"].text.null_tokens == frozenset({"", "k.a."})


@pytest.mark.parametrize(
    ("base", "override"),
    [
        ({"decimal": ",", "thousands": "."}, {"decimal": "."}),
        ({"decimal": ",", "thousands": "."}, {"thousands": ","}),
        ({"decimal": "."}, {"thousands": "."}),
        ({"decimal": ".", "thousands": ","}, {"decimal": ",", "thousands": ","}),
    ],
)
def test_a_column_locale_cannot_make_the_separators_equal(
    base: dict[str, str], override: dict[str, str]
) -> None:
    with pytest.raises(ProfileError) as caught:
        parse_dict({"a": {"type": "number", "locale": override}}, input={"locale": base})
    assert "columns.a.locale: decimal and thousands separators must differ" in str(caught.value)


def test_equal_separators_are_rejected_on_the_locale_itself() -> None:
    with pytest.raises(ValidationError, match="decimal and thousands separators must differ"):
        Locale(decimal=",", thousands=",")
    with pytest.raises(ProfileError, match=r"input\.locale: decimal and thousands separators must differ"):
        parse_dict({"a": {"type": "number"}}, input={"locale": {"decimal": ",", "thousands": ","}})
    assert Locale(decimal=",", thousands=".").decimal == ","


@pytest.mark.parametrize(
    ("columns", "sections", "path"),
    [
        ({"a": {"type": "string", "unicode": "NFD"}}, {}, "columns.a.unicode"),
        ({"a": {"type": "string"}}, {"text": {"unicode": "NFD"}}, "text.unicode"),
    ],
)
def test_an_unknown_unicode_form_is_rejected(
    columns: dict[str, object], sections: dict[str, object], path: str
) -> None:
    with pytest.raises(ProfileError) as caught:
        parse_dict(columns, **sections)
    assert f"{path}: Input should be 'NFC', 'NFKC' or 'none'" in str(caught.value)


@pytest.mark.parametrize(
    ("columns", "sections", "message"),
    [
        (
            {"a": {"type": "boolean"}},
            {"input": {"locale": {"true_values": ["ja", "x"], "false_values": [" JA ", "nein"]}}},
            "input.locale: ['ja'] listed as both true and false",
        ),
        (
            {"a": {"type": "boolean", "locale": {"true_values": ["1", "wahr"], "false_values": ["Wahr"]}}},
            {},
            "columns.a.locale: ['wahr'] listed as both true and false",
        ),
        (
            {"a": {"type": "boolean", "locale": {"false_values": ["yes"]}}},
            {},
            "columns.a.locale: ['yes'] listed as both true and false",
        ),
    ],
)
def test_a_boolean_word_cannot_be_both_true_and_false(
    columns: dict[str, object], sections: dict[str, object], message: str
) -> None:
    with pytest.raises(ProfileError) as caught:
        parse_dict(columns, **sections)
    assert message in str(caught.value)


@pytest.mark.parametrize(
    ("sections", "message"),
    [
        (
            {"dedupe": {"keys": [{"columns": ["a"], "order_by": "zzz"}]}},
            "dedupe.keys.0.order_by: column 'zzz'",
        ),
        (
            {"dedupe": {"fuzzy": {"block_on": ["zzz"], "fields": [{"columns": ["a"]}]}}},
            "dedupe.fuzzy.block_on: column 'zzz'",
        ),
        (
            {"dedupe": {"fuzzy": {"block_on": ["a"], "fields": [{"columns": ["zzz"]}]}}},
            "dedupe.fuzzy.fields.0.columns: column 'zzz'",
        ),
        ({"output": {"sort_by": ["a", "zzz"]}}, "output.sort_by: column 'zzz'"),
        ({"report": {"redact": ["zzz"]}}, "report.redact: column 'zzz'"),
    ],
)
def test_unknown_column_references_are_rejected(sections: dict[str, object], message: str) -> None:
    with pytest.raises(ProfileError) as caught:
        parse_dict({"a": {"type": "string"}}, **sections)
    assert f"{message} is not in columns" in str(caught.value)


def test_the_sheet_column_counts_as_a_known_column() -> None:
    profile = profile_of(
        {"a": {"type": "string"}},
        input={"sheet_column": "source_sheet"},
        output={"sort_by": ["source_sheet"]},
        report={"redact": ["source_sheet"]},
    )
    assert profile.output.sort_by == ["source_sheet"]


@pytest.mark.parametrize(
    "rule",
    [
        {"type": "integer", "min": 5, "max": 1},
        {"type": "number", "min": "2.5", "max": 2},
        {"type": "date", "min": "2025-02-01", "max": "2025-01-01"},
        {"type": "datetime", "min": "2025-01-01T10:00:00", "max": "2025-01-01T09:00:00"},
    ],
)
def test_a_maximum_below_the_minimum_is_rejected(rule: dict[str, object]) -> None:
    with pytest.raises(ProfileError) as caught:
        parse_dict({"a": rule})
    assert "columns.a.max: must not be below min" in str(caught.value)


def test_bounds_that_cannot_be_compared_are_a_profile_error() -> None:
    rule = {"type": "datetime", "min": "2025-01-01T10:00:00+01:00", "max": "2025-01-02T10:00:00"}
    with pytest.raises(ProfileError, match=r"columns\.a\.max: cannot be compared with min"):
        parse_dict({"a": rule})


@pytest.mark.parametrize("key", ["min", "max"])
def test_an_aware_datetime_bound_is_a_profile_error(key: str) -> None:
    rule = {"type": "datetime", key: "2025-01-01T10:00:00Z"}
    with pytest.raises(ProfileError, match=rf"columns\.a\.{key}: must be a naive datetime"):
        parse_dict({"a": rule})


def test_a_naive_datetime_bound_validates() -> None:
    rule = {"type": "datetime", "min": "2025-01-01T10:00:00", "max": "2025-01-02T10:00:00"}
    plan = profile_of({"a": rule}).column_plans()["a"]
    assert (plan.min, plan.max) == (dt.datetime(2025, 1, 1, 10), dt.datetime(2025, 1, 2, 10))


def test_equal_bounds_are_allowed() -> None:
    plan = profile_of({"a": {"type": "integer", "min": 3, "max": 3}}).column_plans()["a"]
    assert (plan.min, plan.max) == (3, 3)


def test_the_schema_exporter_is_current_and_byte_identical(capsys: pytest.CaptureFixture[str]) -> None:
    assert export_schema.main(["--check"]) == 0
    assert "is current" in capsys.readouterr().err
    assert export_schema.render().encode("utf-8") == SCHEMA_FILE.read_bytes()


def test_the_schema_exporter_writes_and_flags_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "docs" / "profile.schema.json"
    monkeypatch.setattr(export_schema, "TARGET", target)
    assert export_schema.main(["--check"]) == 1
    assert export_schema.main([]) == 0
    assert target.read_bytes() == SCHEMA_FILE.read_bytes()
    assert export_schema.main(["--check"]) == 0
    target.write_text("{}\n", encoding="utf-8")
    assert export_schema.main(["--check"]) == 1
    assert "out of date" in capsys.readouterr().err


@pytest.mark.parametrize("codec", ["rot13", "zlib_codec"])
@pytest.mark.parametrize("path", ["input.encoding", "output.csv.encoding"])
def test_a_bytes_codec_is_refused_in_both_encoding_fields(codec: str, path: str) -> None:
    section, *inner, field = path.split(".")
    nested: dict[str, Any] = {field: codec}
    for key in reversed(inner):
        nested = {key: nested}
    with pytest.raises(ProfileError) as caught:
        parse_dict({"a": {"type": "string"}}, **{section: nested})
    assert f"{path}: " in str(caught.value)
    assert f"{codec!r} is not a Python text encoding" in str(caught.value)


def test_text_encodings_are_accepted_and_unknown_names_keep_their_message() -> None:
    for name in ("utf-8", "UTF-8", "latin-1", "cp1252", "utf-16", "utf-8-sig"):
        assert require_text_encoding(name) == name
    with pytest.raises(ValueError, match="unknown encoding 'klingon'"):
        require_text_encoding("klingon")
    with pytest.raises(ValueError, match="unknown encoding"):
        require_text_encoding("utf\x008")
    with pytest.raises(ValueError, match="is not a Python text encoding"):
        require_text_encoding("hex")
