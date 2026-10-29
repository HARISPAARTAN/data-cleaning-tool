from __future__ import annotations

import pytest

from cleandata.core.frame import column
from cleandata.core.text import (
    TextPlan,
    TextSettings,
    clean_text,
    display,
    fold_key,
    normalise_cell,
    normalise_stage,
    redact_value,
)
from support import context, frame_of, profile_of

BS = chr(92)  # a backslash, for the expected escape texts

DEFAULT = TextPlan()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Leeds  ", "Leeds"),
        ("New\N{NO-BREAK SPACE}York", "New York"),
        ("10\N{NARROW NO-BREAK SPACE}000", "10 000"),
        ("a\N{THIN SPACE}b", "a b"),
        ("ze\N{ZERO WIDTH SPACE}ro", "zero"),
        ("word\N{WORD JOINER}joiner", "wordjoiner"),
        ("\N{ZERO WIDTH NO-BREAK SPACE}bom", "bom"),
        ("bell\x07", "bell"),
        ("c1\x85control", "c1control"),
        ("two  spaces", "two spaces"),
        ("line\nbreak", "line break"),
        ("crlf\r\nline", "crlf line"),
        ("tab\there", "tab here"),
        ("Cafe\N{COMBINING ACUTE ACCENT}", "Café"),
    ],
)
def test_clean_text_defaults(raw: str, expected: str) -> None:
    assert clean_text(raw, TextSettings()) == expected


def test_nfkc_folds_compatibility_characters_and_nfc_does_not() -> None:
    assert clean_text("\N{LATIN SMALL LIGATURE FI}le ①", TextSettings(unicode="NFKC")) == "file 1"
    assert clean_text("\N{LATIN SMALL LIGATURE FI}le", TextSettings()) == "\N{LATIN SMALL LIGATURE FI}le"


def test_switches_turn_steps_off() -> None:
    settings = TextSettings(unicode="none", trim=False, collapse_whitespace=False, remove_control=False)
    assert clean_text(" a\tb\x07 ", settings) == " a\tb\x07 "
    assert clean_text("Cafe\N{COMBINING ACUTE ACCENT}", settings) == "Cafe\N{COMBINING ACUTE ACCENT}"


def test_tab_and_line_feed_survive_when_collapse_is_off() -> None:
    settings = TextSettings(collapse_whitespace=False)
    assert clean_text("first\tsecond\nthird\x0b", settings) == "first\tsecond\nthird"


@pytest.mark.parametrize(
    ("case", "expected"), [("lower", "anna"), ("upper", "ANNA"), ("title", "Anna"), ("keep", "aNNa")]
)
def test_case(case: str, expected: str) -> None:
    plan = TextPlan(settings=TextSettings(case=case))
    assert normalise_cell(" aNNa ", plan) == expected


def test_null_tokens_compare_trimmed_and_casefolded() -> None:
    plan = TextPlan(null_tokens=frozenset({"", "k.a.", "n/a"}))
    assert normalise_cell(" K.A. ", plan) is None
    assert normalise_cell("N/A", plan) is None
    assert normalise_cell("\N{NO-BREAK SPACE}", plan) is None
    assert normalise_cell("n/a later", plan) == "n/a later"


def test_map_is_casefolded_and_applied_after_case() -> None:
    plan = TextPlan(mapping={fold_key("Deutschland"): "DE", fold_key("Straße"): "street"})
    assert normalise_cell(" DEUTSCHLAND ", plan) == "DE"
    assert normalise_cell("STRASSE", plan) == "street"
    assert normalise_cell("Germany", plan) == "Germany"


def test_native_values_are_left_alone() -> None:
    assert normalise_cell(12, DEFAULT) == 12
    assert normalise_cell(None, DEFAULT) is None


def test_display_shows_invisible_characters_as_escapes() -> None:
    assert display("a\N{NO-BREAK SPACE}b\N{ZERO WIDTH SPACE}c\td\ne") == f"a{BS}u00a0b{BS}u200bc{BS}td{BS}ne"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("anna@panel.example", "a***@panel.example"), ("Anna", "A***"), ("", "")],
)
def test_redaction(value: str, expected: str) -> None:
    assert redact_value(value) == expected


def test_stage_counts_changes_and_keeps_examples_with_row_labels() -> None:
    profile = profile_of(
        {"city": {"type": "string"}, "comment": {"type": "string"}}, input={"null_tokens": ["k.A."]}
    )
    frame = frame_of(
        ["city", "comment"], [["  Leeds", "ok"], ["York", "k.A."], ["Hull", "fine\N{NO-BREAK SPACE}here"]]
    )
    result, events = normalise_stage(frame, context(profile, frame))
    assert column(result, "city") == ["Leeds", "York", "Hull"]
    assert column(result, "comment") == ["ok", None, "fine here"]
    assert events.columns["city"].text_changed == 1
    assert events.columns["comment"].text_nulled == 1
    assert events.columns["comment"].text_examples == [
        'r3 "k.A." -> null',
        f'r4 "fine{BS}u00a0here" -> "fine here"',
    ]
