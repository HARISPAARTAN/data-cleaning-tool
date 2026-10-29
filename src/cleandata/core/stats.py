"""Column statistics before (raw strings after stage 2) and after (the frame that is written).

Both are streaming accumulators: count, nulls, min, max and mean come out exactly the same whether
the rows arrive in one frame or in chunks, because values are added in source order. Distinct values
are exact up to 10,000. Medians and top values need every value, so chunked runs leave them out.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Final, cast

import pandas as pd

from cleandata.core.coerce import canonical_str, json_value
from cleandata.core.frame import column, data_columns
from cleandata.core.text import has_control, has_whitespace_issue, is_nfc, redact_value

if TYPE_CHECKING:
    from cleandata.core.profile import ColumnPlan

DISTINCT_CAP: Final = 10_000
TOP: Final = 5


def _top(counter: Counter[str], redact: bool) -> list[list[object]]:
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:TOP]
    return [[redact_value(value) if redact else value, count] for value, count in ranked]


class _Distinct:
    def __init__(self) -> None:
        self.values: set[str] = set()
        self.capped = False

    def add(self, value: str) -> None:
        if self.capped:
            return
        self.values.add(value)
        if len(self.values) > DISTINCT_CAP:
            self.capped = True
            self.values = set()

    def result(self) -> dict[str, object]:
        return {"distinct": DISTINCT_CAP if self.capped else len(self.values), "distinct_capped": self.capped}


class BeforeColumn:
    """Raw cells: CSV strings or native Excel values, null tokens not yet applied."""

    def __init__(self, null_tokens: frozenset[str], keep_values: bool) -> None:
        self.null_tokens = null_tokens
        self.cells = 0
        self.non_empty = 0
        self.null_like = 0
        self.distinct = _Distinct()
        self.counter: Counter[str] | None = Counter() if keep_values else None
        self.min_length: int | None = None
        self.max_length: int | None = None
        self.whitespace = 0
        self.non_nfc = 0
        self.control = 0

    def update(self, values: Iterable[object]) -> None:
        for value in values:
            self.cells += 1
            if value is None:
                continue
            text = canonical_str(value)
            if text.strip() == "":
                continue
            self.non_empty += 1
            if text.strip().casefold() in self.null_tokens:
                self.null_like += 1
            self.distinct.add(text)
            if self.counter is not None:
                self.counter[text] += 1
            length = len(text)
            self.min_length = length if self.min_length is None else min(self.min_length, length)
            self.max_length = length if self.max_length is None else max(self.max_length, length)
            if isinstance(value, str):
                self.whitespace += has_whitespace_issue(value)
                self.non_nfc += not is_nfc(value)
                self.control += has_control(value)

    def result(self, redact: bool) -> dict[str, object]:
        return {
            "non_empty": self.non_empty,
            "null_like": self.null_like,
            **self.distinct.result(),
            "top": None if self.counter is None else _top(self.counter, redact),
            "min_length": self.min_length,
            "max_length": self.max_length,
            "whitespace": self.whitespace,
            "non_nfc": self.non_nfc,
            "control": self.control,
        }


class AfterColumn:
    def __init__(self, ctype: str | None, keep_values: bool) -> None:
        self.ctype = ctype
        self.count = 0
        self.nulls = 0
        self.distinct = _Distinct()
        self.minimum: object = None
        self.maximum: object = None
        self.total = 0.0
        self.numbers: list[float] | None = [] if keep_values else None
        self.counter: Counter[str] | None = Counter() if keep_values else None

    @property
    def ordered(self) -> bool:
        return self.ctype in ("integer", "number", "date", "datetime")

    @property
    def numeric(self) -> bool:
        return self.ctype in ("integer", "number")

    def update(self, values: Iterable[object]) -> None:
        for value in values:
            if value is None:
                self.nulls += 1
                continue
            self.count += 1
            self.distinct.add(canonical_str(value))
            if self.ordered:
                current = cast(Any, value)
                if self.minimum is None or current < self.minimum:
                    self.minimum = value
                if self.maximum is None or current > self.maximum:
                    self.maximum = value
            if self.numeric:
                number = float(cast(Any, value))
                self.total += number
                if self.numbers is not None:
                    self.numbers.append(number)
            elif self.counter is not None and not self.ordered:
                self.counter[canonical_str(value)] += 1

    def result(self, redact: bool, filled: int) -> dict[str, object]:
        mean = self.total / self.count if self.numeric and self.count else None
        median = statistics.median(self.numbers) if self.numeric and self.numbers else None
        top = None
        if self.counter is not None and not self.ordered:
            top = _top(self.counter, redact)
        hide = redact and not self.numeric
        return {
            "count": self.count,
            "nulls": self.nulls,
            **self.distinct.result(),
            "min": None if hide else json_value(self.minimum),
            "max": None if hide else json_value(self.maximum),
            "mean": mean,
            "median": median,
            "top": top,
            "filled": filled,
        }


class StatsCollector:
    """Per-column accumulators for the columns of the output frame."""

    def __init__(
        self,
        columns: list[str],
        plans: Mapping[str, ColumnPlan],
        default_tokens: frozenset[str],
        in_memory: bool,
    ) -> None:
        self.columns = columns
        self.plans = plans
        self.before = {
            name: BeforeColumn(plans[name].text.null_tokens if name in plans else default_tokens, in_memory)
            for name in columns
        }
        self.after = {
            name: AfterColumn(plans[name].type if name in plans else None, in_memory) for name in columns
        }

    def update_before(self, raw: pd.DataFrame) -> None:
        for name in data_columns(raw):
            if name in self.before:
                self.before[name].update(column(raw, name))

    def update_after(self, frame: pd.DataFrame) -> None:
        for name in data_columns(frame):
            if name not in self.after:
                self.after[name] = AfterColumn(None, self.after[self.columns[0]].counter is not None)
            self.after[name].update(column(frame, name))

    def result(self, filled: Mapping[str, int]) -> dict[str, dict[str, object]]:
        output: dict[str, dict[str, object]] = {}
        for name in list(self.after):
            redact = name in self.plans and self.plans[name].redact
            entry: dict[str, object] = {}
            if name in self.before:
                entry["before"] = self.before[name].result(redact)
            entry["after"] = self.after[name].result(redact, filled.get(name, 0))
            output[name] = entry
        return output
