from __future__ import annotations

import datetime as dt

from cleandata.core.stats import DISTINCT_CAP, AfterColumn, BeforeColumn

NUMBERS = [3.5, None, 1.25, 10.0, None, 7.0, 2.0]
DATES = [dt.date(2025, 3, 1), None, dt.date(2024, 1, 9), dt.date(2026, 2, 2)]


def streamed(ctype: str, values: list[object], size: int) -> dict[str, object]:
    column = AfterColumn(ctype, keep_values=False)
    for start in range(0, len(values), size):
        column.update(values[start : start + size])
    return column.result(redact=False, filled=0)


def test_streaming_equals_in_memory_for_count_nulls_min_max_mean() -> None:
    for ctype, values in (("number", NUMBERS), ("date", DATES)):
        whole = AfterColumn(ctype, keep_values=True)
        whole.update(values)
        expected = whole.result(redact=False, filled=0)
        for size in (1, 2, 3):
            result = streamed(ctype, values, size)
            for key in ("count", "nulls", "distinct", "min", "max", "mean"):
                assert result[key] == expected[key], (ctype, size, key)
            assert result["median"] is None
            assert result["top"] is None


def test_after_values() -> None:
    column = AfterColumn("number", keep_values=True)
    column.update(NUMBERS)
    result = column.result(redact=False, filled=2)
    assert (result["count"], result["nulls"], result["min"], result["max"]) == (5, 2, 1.25, 10.0)
    assert result["mean"] == 4.75
    assert result["median"] == 3.5
    assert result["filled"] == 2
    dates = AfterColumn("date", keep_values=True)
    dates.update(DATES)
    assert dates.result(redact=False, filled=0)["min"] == "2024-01-09"


def test_top_values_and_redaction() -> None:
    column = AfterColumn("string", keep_values=True)
    column.update(["DE", "DE", "GB", "anna@panel.example", None])
    assert column.result(redact=False, filled=0)["top"] == [["DE", 2], ["GB", 1], ["anna@panel.example", 1]]
    redacted = column.result(redact=True, filled=0)
    assert redacted["top"][2] == ["a***@panel.example", 1]
    assert redacted["min"] is None


def test_distinct_is_capped() -> None:
    column = AfterColumn("integer", keep_values=False)
    column.update(range(DISTINCT_CAP + 5))
    result = column.result(redact=False, filled=0)
    assert (result["distinct"], result["distinct_capped"]) == (DISTINCT_CAP, True)


def test_before_counts_raw_issues() -> None:
    column = BeforeColumn(frozenset({"", "k.a."}), keep_values=True)
    column.update(
        [" Leeds", "York", "k.A.", "Cafe\N{COMBINING ACUTE ACCENT}", "bell\x07", "", None, "two  spaces"]
    )
    result = column.result(redact=False)
    assert result["non_empty"] == 6
    assert result["null_like"] == 1
    assert result["whitespace"] == 2
    assert result["non_nfc"] == 1
    assert result["control"] == 1
    assert (result["min_length"], result["max_length"]) == (4, 11)
