from __future__ import annotations

import datetime as dt
import math
from typing import Any

import pandas as pd

from cleandata.core.ledger import ColumnTally, Ledger
from cleandata.core.stats import DISTINCT_CAP, TOP, AfterColumn, BeforeColumn, StatsCollector
from support import frame_of, profile_of

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


RAW_COLUMNS: dict[str, list[Any]] = {
    "id": ["1", "2", "3", "4"],
    "email": ["anna@panel.example", "anna@panel.example", "N/A", "ben@panel.example"],
    "age": ["41", "52", "33", None],
    "note": ["N/A", "k.A.", "k.A.", "fine"],
    "region": ["k.A.", "N/A", "north", "north"],
    "stray": ["x", "x", "x", "x"],
}
CLEANED_COLUMNS: dict[str, list[Any]] = {
    "region": [None, "N/A", "north", "north"],
    "note": ["N/A", None, None, "fine"],
    "cluster": ["c1", "c1", None, "c2"],
    "age": [41, 52, 33, None],
    "email": ["anna@panel.example", "anna@panel.example", None, "ben@panel.example"],
    "id": [1, 2, 3, 4],
}
AFTER_KEYS = ("count", "nulls", "distinct", "min", "max", "mean", "median", "top", "filled")


def frame_from(columns: dict[str, list[Any]]) -> pd.DataFrame:
    return frame_of(list(columns), [list(row) for row in zip(*columns.values(), strict=True)])


def pick(entry: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: entry[key] for key in keys}


def build_collector(in_memory: bool) -> StatsCollector:
    """Four profile columns (email has its own null token, email and age are redacted) plus one unlisted."""
    profile = profile_of(
        {
            "id": {"type": "integer"},
            "email": {"type": "string", "null_tokens": ["n/a"]},
            "age": {"type": "integer"},
            "note": {"type": "string"},
        },
        input={"null_tokens": ["k.A."]},
        report={"redact": ["email", "age"]},
    )
    plans = profile.column_plans()
    return StatsCollector([*plans, "region"], plans, profile.default_text_plan().null_tokens, in_memory)


def run_collector(in_memory: bool, filled: dict[str, int]) -> dict[str, Any]:
    collector = build_collector(in_memory)
    collector.update_before(frame_from(RAW_COLUMNS))
    collector.update_after(frame_from(CLEANED_COLUMNS))
    return collector.result(filled)


def test_collector_follows_the_plan() -> None:
    ledger = Ledger(limit=5, preview_rows=0)
    ledger.columns["email"] = ColumnTally(filled=2)
    ledger.columns["region"] = ColumnTally(filled=1)
    ledger.columns["cluster"] = ColumnTally(filled=3)
    result = run_collector(True, {name: tally.filled for name, tally in ledger.columns.items()})

    # plan order first (not the order of the frames), then the column that only exists after cleaning
    assert list(result) == ["id", "email", "age", "note", "region", "cluster"]
    assert "before" not in result["cluster"]
    assert all("before" in result[name] for name in ("id", "email", "age", "note", "region"))

    # null tokens come from the plan: email has "n/a" of its own, note and region only the shared "k.a."
    keys = ("non_empty", "null_like", "distinct", "top")
    assert pick(result["id"]["before"], *keys) == {
        "non_empty": 4,
        "null_like": 0,
        "distinct": 4,
        "top": [["1", 1], ["2", 1], ["3", 1], ["4", 1]],
    }
    assert pick(result["email"]["before"], *keys) == {
        "non_empty": 4,
        "null_like": 1,
        "distinct": 3,
        "top": [["a***@panel.example", 2], ["N***", 1], ["b***@panel.example", 1]],
    }
    assert pick(result["age"]["before"], *keys) == {
        "non_empty": 3,
        "null_like": 0,
        "distinct": 3,
        "top": [["3***", 1], ["4***", 1], ["5***", 1]],
    }
    assert pick(result["note"]["before"], *keys) == {
        "non_empty": 4,
        "null_like": 2,
        "distinct": 3,
        "top": [["k.A.", 2], ["N/A", 1], ["fine", 1]],
    }
    assert result["region"]["before"]["null_like"] == 1  # not in the plans: the default tokens
    assert result["region"]["before"]["top"] == [["north", 2], ["N/A", 1], ["k.A.", 1]]

    assert pick(result["id"]["after"], *AFTER_KEYS) == {
        "count": 4,
        "nulls": 0,
        "distinct": 4,
        "min": 1,
        "max": 4,
        "mean": 2.5,
        "median": 2.5,
        "top": None,
        "filled": 0,
    }
    # redacted string column: top values masked, filled still threaded through
    assert pick(result["email"]["after"], *AFTER_KEYS) == {
        "count": 3,
        "nulls": 1,
        "distinct": 2,
        "min": None,
        "max": None,
        "mean": None,
        "median": None,
        "top": [["a***@panel.example", 2], ["b***@panel.example", 1]],
        "filled": 2,
    }
    # redacted integer column: counts stay, every value-derived figure is hidden
    assert pick(result["age"]["after"], *AFTER_KEYS) == {
        "count": 3,
        "nulls": 1,
        "distinct": 3,
        "min": None,
        "max": None,
        "mean": None,
        "median": None,
        "top": None,
        "filled": 0,
    }
    assert pick(result["note"]["after"], "count", "nulls", "top", "filled") == {
        "count": 2,
        "nulls": 2,
        "top": [["N/A", 1], ["fine", 1]],
        "filled": 0,
    }
    assert pick(result["region"]["after"], "count", "nulls", "top", "filled") == {
        "count": 3,
        "nulls": 1,
        "top": [["north", 2], ["N/A", 1]],
        "filled": 1,
    }
    assert pick(result["cluster"]["after"], "count", "nulls", "distinct", "top", "filled") == {
        "count": 3,
        "nulls": 1,
        "distinct": 2,
        "top": [["c1", 2], ["c2", 1]],
        "filled": 3,
    }


def test_redaction_hides_numeric_aggregates() -> None:
    column = AfterColumn("integer", keep_values=True)
    column.update([41, 52, 33])
    shown = column.result(redact=False, filled=0)
    assert pick(shown, "min", "max", "mean", "median") == {"min": 33, "max": 52, "mean": 42.0, "median": 41.0}
    hidden = column.result(redact=True, filled=0)
    assert pick(hidden, "min", "max", "mean", "median", "top") == {
        "min": None,
        "max": None,
        "mean": None,
        "median": None,
        "top": None,
    }
    assert (hidden["count"], hidden["nulls"], hidden["distinct"]) == (3, 0, 3)
    dates = AfterColumn("date", keep_values=True)
    dates.update(DATES)
    assert pick(dates.result(redact=True, filled=0), "min", "max") == {"min": None, "max": None}


def test_collector_in_chunked_mode_leaves_out_top_and_median() -> None:
    result = run_collector(False, {})
    assert list(result) == ["id", "email", "age", "note", "region", "cluster"]
    for name, entry in result.items():
        assert entry["after"]["top"] is None, name
        assert entry["after"]["median"] is None, name
        if "before" in entry:
            assert entry["before"]["top"] is None, name
    # the streaming figures are still there, for the column that only exists after cleaning too
    assert result["id"]["after"]["mean"] == 2.5
    assert result["id"]["before"]["non_empty"] == 4
    assert result["cluster"]["after"]["count"] == 3


def test_before_chunked_equals_whole() -> None:
    values: list[object] = [
        " Leeds",
        "York",
        "k.A.",
        "bell\x07",
        "",
        None,
        "two  spaces",
        "York",
        7,
        2.5,
        "York",
    ]
    tokens = frozenset({"", "k.a."})
    whole = BeforeColumn(tokens, keep_values=True)
    whole.update(values)
    expected = whole.result(redact=False)
    assert expected["top"][0] == ["York", 3]
    for size in (1, 2, 3, len(values)):
        kept = BeforeColumn(tokens, keep_values=True)
        lean = BeforeColumn(tokens, keep_values=False)
        for start in range(0, len(values), size):
            kept.update(values[start : start + size])
            lean.update(values[start : start + size])
        assert kept.result(redact=False) == expected, size
        assert lean.result(redact=False) == {**expected, "top": None}, size


def test_top_is_limited_and_ties_break_by_text() -> None:
    values = ["f", "e", "d", "c", "b", "a", "z", "z", "y"]
    assert TOP == 5
    expected = [["z", 2], ["a", 1], ["b", 1], ["c", 1], ["d", 1]]
    before = BeforeColumn(frozenset({""}), keep_values=True)
    before.update(values)
    assert before.result(redact=False)["top"] == expected
    after = AfterColumn("string", keep_values=True)
    after.update(values)
    assert after.result(redact=False, filled=0)["top"] == expected


def test_collector_without_columns_takes_frames() -> None:
    assert StatsCollector([], {}, frozenset({""}), True).result({}) == {}
    for in_memory in (True, False):
        collector = StatsCollector([], {}, frozenset({""}), in_memory)
        collector.update_before(frame_of(["a"], [["x"]]))
        collector.update_after(frame_of(["a"], [["x"], ["y"]]))
        result = collector.result({"a": 4})
        assert list(result) == ["a"]
        assert "before" not in result["a"]
        after = result["a"]["after"]
        assert (after["count"], after["filled"]) == (2, 4)
        assert after["top"] == ([["x", 1], ["y", 1]] if in_memory else None)


def test_non_finite_floats_count_but_are_not_measured() -> None:
    values: list[object] = [1.0, math.nan, 2.0, math.inf, -math.inf]
    column = AfterColumn("number", keep_values=True)
    column.update(values)
    result = column.result(redact=False, filled=0)
    assert (result["count"], result["nulls"], result["distinct"]) == (5, 0, 5)
    assert pick(result, "min", "max", "mean", "median") == {
        "min": 1.0,
        "max": 2.0,
        "mean": 1.5,
        "median": 1.5,
    }
    for size in (1, 2, 5):
        streaming = streamed("number", values, size)
        assert pick(streaming, "count", "min", "max", "mean") == {
            "count": 5,
            "min": 1.0,
            "max": 2.0,
            "mean": 1.5,
        }
    leading = streamed("number", [math.nan, 1.0, 2.0], 1)
    assert (leading["count"], leading["mean"]) == (3, 1.5)
    only = streamed("number", [math.nan, math.inf], 2)
    assert pick(only, "count", "min", "max", "mean") == {"count": 2, "min": None, "max": None, "mean": None}
    # a sum that overflows has no usable mean, but must not raise
    assert streamed("number", [1e308, 1e308], 1)["mean"] is None


def test_mean_uses_a_compensated_sum() -> None:
    for values, expected in (([0.1] * 10, 0.1), ([0.1, 0.2, 0.3], 0.2), ([1e16, 1.0, -1e16], 1 / 3)):
        for size in (1, 3, len(values)):
            assert streamed("number", list(values), size)["mean"] == expected, (values, size)
    whole = AfterColumn("number", keep_values=True)
    whole.update([0.1] * 10)
    assert whole.result(redact=False, filled=0)["mean"] == 0.1
