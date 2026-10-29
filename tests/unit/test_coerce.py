from __future__ import annotations

import datetime as dt

import pytest

from cleandata.core.coerce import (
    Invalid,
    ParseSettings,
    canonical_str,
    coerce_literal,
    to_boolean,
    to_date,
    to_datetime,
    to_integer,
    to_number,
    types_stage,
)
from cleandata.core.frame import column
from support import context, frame_of, profile_of

GERMAN = ParseSettings(decimal=",", thousands=".")
ENGLISH = ParseSettings(decimal=".", thousands=",")
PLAIN = ParseSettings()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("45.000,00", 45000.0),
        ("38.500", 38500.0),
        ("1.234.567,5", 1234567.5),
        ("12,5", 12.5),
        ("-3,25", -3.25),
        ("+7", 7.0),
        (",5", 0.5),
        ("1 234,5", 1234.5),
        ("1\N{NO-BREAK SPACE}234,5", 1234.5),
        ("1 234 567,89", 1234567.89),
        ("-1 234,5", -1234.5),
        ("  45.000,00\t", 45000.0),
        ("0", 0.0),
        ("0,5", 0.5),
        ("0,500", 0.5),
        ("1.000", 1000.0),
    ],
)
def test_german_numbers(text: str, expected: float) -> None:
    assert to_number(text, GERMAN) == expected


@pytest.mark.parametrize("text", ["1.5", "1.23", "12.34.567", "1,2,3", "", "abc", "1.234e5", "--1", "1,"])
def test_german_rejects(text: str) -> None:
    assert isinstance(to_number(text, GERMAN), Invalid)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234.5", 1234.5),
        ("1234.5", 1234.5),
        ("1.5e3", 1500.0),
        ("-0.25", -0.25),
        ("1,234,567", 1234567.0),
        ("1 234.5", 1234.5),
        ("1\N{NO-BREAK SPACE}234 567.89", 1234567.89),
        ("0", 0.0),
        ("0.5", 0.5),
        ("0.500", 0.5),
    ],
)
def test_english_numbers(text: str, expected: float) -> None:
    assert to_number(text, ENGLISH) == expected


@pytest.mark.parametrize("text", ["1,23", "12,34", "1,234e3", "1.2.3"])
def test_english_rejects(text: str) -> None:
    assert isinstance(to_number(text, ENGLISH), Invalid)


@pytest.mark.parametrize("settings", [GERMAN, ENGLISH, PLAIN], ids=["german", "english", "plain"])
@pytest.mark.parametrize(
    "text",
    [
        "10 20",
        "1 5",
        "12 34 56",
        "2025 03",
        "-1 5",
        "1 2345",
        "1 23",
        "1 234 56",
        "1\N{NO-BREAK SPACE}5",
        "1\t234",
        "1 \N{NO-BREAK SPACE}234",
        "- 5",
        "1 e5",
    ],
)
def test_merged_numbers_are_invalid_under_any_settings(text: str, settings: ParseSettings) -> None:
    for convert in (to_number, to_integer):
        result = convert(text, settings)
        assert isinstance(result, Invalid)
        assert result.reason == "not a number"


def test_space_group_needs_grouping_settings() -> None:
    assert isinstance(to_number("1 234", PLAIN), Invalid)
    assert to_number("1 234", GERMAN) == 1234.0
    assert to_number("1 234", ENGLISH) == 1234.0
    assert to_integer("1 234 567", GERMAN) == 1234567


@pytest.mark.parametrize(
    ("text", "settings"),
    [
        ("0.500", GERMAN),
        ("00.500", GERMAN),
        ("-0.500", GERMAN),
        ("0.500,5", GERMAN),
        ("012.345", GERMAN),
        ("0,500", ENGLISH),
        ("00,500", ENGLISH),
        ("+0,500.5", ENGLISH),
        ("0,123,456", ENGLISH),
    ],
)
def test_leading_zero_group_is_ambiguous(text: str, settings: ParseSettings) -> None:
    for convert in (to_number, to_integer):
        result = convert(text, settings)
        assert isinstance(result, Invalid)
        assert result.reason == "ambiguous separator"


@pytest.mark.parametrize(
    ("text", "settings"), [("0,5", ENGLISH), ("0.5", GERMAN), ("0 5", GERMAN), ("0 500", ENGLISH)]
)
def test_other_zero_led_forms_are_not_numbers(text: str, settings: ParseSettings) -> None:
    result = to_number(text, settings)
    assert isinstance(result, Invalid)
    assert result.reason == "not a number"


def test_without_thousands_grouping_is_invalid() -> None:
    assert isinstance(to_number("1,234", PLAIN), Invalid)
    assert to_number("1e-3", PLAIN) == 0.001


def test_strip_removes_substrings_before_parsing() -> None:
    assert to_number("£1,250.00", ENGLISH, ("£",)) == 1250.0
    assert to_number("1.250,00 €", GERMAN, ("€",)) == 1250.0
    assert isinstance(to_number("£1,250.00", ENGLISH), Invalid)


def test_native_numbers_pass_and_booleans_do_not() -> None:
    assert to_number(3, GERMAN) == 3.0
    assert to_number(2.5, GERMAN) == 2.5
    assert isinstance(to_number(True, GERMAN), Invalid)
    assert isinstance(to_number(float("nan"), GERMAN), Invalid)


@pytest.mark.parametrize(
    ("value", "expected"), [("45.000,00", 45000), ("38.500", 38500), ("-12", -12), (7.0, 7), (9, 9)]
)
def test_integers(value: object, expected: int) -> None:
    assert to_integer(value, GERMAN) == expected


@pytest.mark.parametrize("value", ["12,5", "1.5", 2.5, True, "9" * 20])
def test_integer_rejects(value: object) -> None:
    assert isinstance(to_integer(value, GERMAN), Invalid)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("yes", True), (" Y ", True), ("1", True), ("TRUE", True), ("no", False), ("0", False), (False, False)],
)
def test_booleans(value: object, expected: bool) -> None:
    assert to_boolean(value, PLAIN) is expected


def test_boolean_rejects_unlisted_values() -> None:
    assert isinstance(to_boolean("ja", PLAIN), Invalid)
    custom = ParseSettings(true_values=frozenset({"ja"}), false_values=frozenset({"nein"}))
    assert to_boolean("Ja", custom) is True


PATTERNS = [
    ("%Y-%m-%d", "2025-03-21"),
    ("%d.%m.%Y", "21.03.2025"),
    ("%d/%m/%Y", "21/03/2025"),
    ("%m/%d/%Y", "03/21/2025"),
    ("%d-%m-%Y", "21-03-2025"),
    ("%Y%m%d", "20250321"),
    ("%d.%m.%y", "21.03.25"),
]


@pytest.mark.parametrize(("pattern", "text"), PATTERNS)
def test_every_date_pattern(pattern: str, text: str) -> None:
    settings = ParseSettings(date_formats=(pattern,))
    assert to_date(text, settings) == (dt.date(2025, 3, 21), pattern)


def test_first_matching_pattern_is_counted() -> None:
    settings = ParseSettings(date_formats=("%d/%m/%Y", "%m/%d/%Y"))
    assert to_date("05/04/2025", settings) == (dt.date(2025, 4, 5), "%d/%m/%Y")
    assert to_date("12/31/2025", settings) == (dt.date(2025, 12, 31), "%m/%d/%Y")


@pytest.mark.parametrize("text", ["31.02.2025", "2025-13-01", "21.3.2025x", "", "21.03.2025 10:00"])
def test_impossible_or_partial_dates_are_invalid(text: str) -> None:
    value, pattern = to_date(text, ParseSettings(date_formats=("%d.%m.%Y",)))
    assert isinstance(value, Invalid)
    assert pattern is None


def test_native_datetimes_in_date_columns() -> None:
    assert to_date(dt.datetime(2026, 3, 8), PLAIN) == (dt.date(2026, 3, 8), "native")
    value, _ = to_date(dt.datetime(2026, 3, 8, 9, 30), PLAIN)
    assert isinstance(value, Invalid)
    assert to_date(dt.date(2026, 3, 8), PLAIN) == (dt.date(2026, 3, 8), "native")


def test_datetimes() -> None:
    settings = ParseSettings(datetime_formats=("%d.%m.%Y %H:%M", "%Y-%m-%dT%H:%M:%S.%f"))
    assert to_datetime("22.03.2025 08:01", settings) == (dt.datetime(2025, 3, 22, 8, 1), "%d.%m.%Y %H:%M")
    assert to_datetime("2025-03-22T08:01:02.500000", settings)[0] == dt.datetime(2025, 3, 22, 8, 1, 2, 500000)
    assert to_datetime(dt.date(2025, 3, 22), settings) == (dt.datetime(2025, 3, 22), "native")
    aware = dt.datetime(2025, 3, 22, tzinfo=dt.UTC)
    assert isinstance(to_datetime(aware, settings)[0], Invalid)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (True, "true"),
        (45000, "45000"),
        (45000.0, "45000.0"),
        (0.1, "0.1"),
        (dt.date(2025, 3, 21), "2025-03-21"),
        (dt.datetime(2025, 3, 22, 8, 1), "2025-03-22T08:01:00"),
        ("text", "text"),
    ],
)
def test_canonical_strings(value: object, expected: str) -> None:
    assert canonical_str(value) == expected


def test_literals_from_the_profile() -> None:
    assert coerce_literal(99, "integer") == 99
    assert coerce_literal("2025-01-01", "date") == dt.date(2025, 1, 1)
    assert coerce_literal(dt.date(2025, 1, 1), "date") == dt.date(2025, 1, 1)
    assert coerce_literal(12, "string") == "12"
    with pytest.raises(ValueError, match="quote it"):
        coerce_literal(True, "string")
    with pytest.raises(ValueError, match="not a valid integer"):
        coerce_literal("1.5", "integer")


def test_stage_marks_invalid_cells_and_nulls_them_on_request() -> None:
    profile = profile_of(
        {
            "income": {"type": "number"},
            "score": {"type": "integer", "on_invalid": "null"},
            "when": {"type": "date"},
        },
        input={"locale": {"decimal": ",", "thousands": ".", "date_formats": ["%d.%m.%Y"]}},
    )
    frame = frame_of(
        ["income", "score", "when"],
        [["45.000,00", "5", "21.03.2025"], ["1.5", "x", "31.02.2025"], [None, None, None]],
    )
    result, events = types_stage(frame, context(profile, frame))
    assert column(result, "income")[0] == 45000.0
    assert isinstance(column(result, "income")[1], Invalid)
    assert column(result, "score") == [5, None, None]
    assert isinstance(column(result, "when")[1], Invalid)
    income = events.columns["income"]
    assert (income.parsed, income.null, income.invalid) == (1, 1, 1)
    assert income.type_examples == ['r2 "45.000,00" -> 45000.0']
    assert events.columns["score"].nulled == 1
    assert events.columns["when"].patterns["%d.%m.%Y"] == 1
    assert [notice.code for notice in events.notices] == ["types.nulled_invalid"]
