"""Exit codes, row accounting, tallies and the frame helpers every stage builds on."""

from __future__ import annotations

from collections import Counter

import pytest

from cleandata.core.frame import (
    META_ROW,
    META_SHEET,
    column,
    data_columns,
    new_frame,
    ordinals,
    row_label,
    take,
    with_column,
)
from cleandata.core.ledger import Events, Ledger, Notice, Reject, RuleTally, rule_column
from cleandata.errors import (
    EXIT_INPUT,
    EXIT_OUTPUT,
    EXIT_USAGE,
    InputError,
    InternalError,
    MissingExtraError,
    OutputError,
    ProfileError,
)


def reject(ordinal: int, stage: str, *rules: str) -> Reject:
    return Reject(ordinal, ordinal + 2, None, stage, list(rules), "detail")


def test_exit_codes_follow_the_kind_of_error() -> None:
    assert ProfileError("bad").exit_code == EXIT_USAGE
    assert InputError("missing").exit_code == EXIT_INPUT
    assert OutputError("locked").exit_code == EXIT_OUTPUT
    error = MissingExtraError("fuzzy", "Fuzzy de-duplication")
    assert (error.exit_code, error.extra) == (EXIT_USAGE, "fuzzy")
    assert '"uv sync --extra fuzzy"' in str(error)
    assert '"data-cleaning-tool[fuzzy]"' in str(error)


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("age.max", "age"),
        ("household_income.missing", "household_income"),
        ("start_date.type", "start_date"),
        ("read.malformed", None),
        ("dedupe.exact", None),
        ("dedupe.key.1", None),
        ("dedupe.fuzzy", None),
    ],
)
def test_rule_columns(rule: str, expected: str | None) -> None:
    assert rule_column(rule) == expected


def test_accounting_adds_up_across_chunks() -> None:
    ledger = Ledger(limit=2, preview_rows=5)
    first = Events("validate", counts=Counter(read=5, out=3, empty=1))
    first.rejects.append(reject(3, "validate", "age.max", "email.regex"))
    second = Events("dedupe", counts=Counter(read=5, out=3, empty=1))
    second.rejects.append(reject(7, "dedupe", "dedupe.exact"))
    ledger.absorb(second)
    ledger.absorb(first)
    assert (ledger.read, ledger.out, ledger.rejected, ledger.duplicates, ledger.empty) == (10, 6, 1, 1, 2)
    assert dict(ledger.by_stage) == {"dedupe": 1, "validate": 1}
    assert dict(ledger.by_rule) == {"dedupe.exact": 1, "age.max": 1}
    assert dict(ledger.by_column) == {"age": 1, "email": 1}
    assert [item.ordinal for item in ledger.preview] == [3, 7]
    ledger.check_accounting()
    assert ledger.reject_rate() == 1 / 8
    ledger.counts["out"] -= 1
    with pytest.raises(InternalError, match="row accounting does not add up: read 10 != out 5"):
        ledger.check_accounting()


def test_notices_add_up_and_info_is_not_a_warning() -> None:
    ledger = Ledger(limit=2, preview_rows=5)
    ledger.notice("structure.short_records", "4 short records were padded", 2)
    events = Events("read", notices=[Notice("structure.short_records", "ignored", 2)])
    events.notices.append(Notice("read.sheets", "2 sheets ignored", level="info"))
    ledger.absorb(events)
    assert ledger.notices["structure.short_records"].count == 4
    assert [notice.code for notice in ledger.warnings()] == ["structure.short_records"]


def test_tallies_keep_the_first_examples_only() -> None:
    tally = RuleTally("age.max", "max", "age", {"max": 99}, checked=10, failed=2, examples=["r4", "r9"])
    tally.merge(RuleTally("age.max", "max", "age", {"max": 99}, checked=5, failed=1, examples=["r20"]), 2)
    assert (tally.checked, tally.failed, tally.examples) == (15, 3, ["r4", "r9"])
    events = Events("types")
    events.column("age").invalid += 1
    events.column("age").invalid += 1
    assert events.columns["age"].invalid == 2


def test_frame_helpers_keep_object_dtype_and_ordinals() -> None:
    frame = new_frame(["a", "b", META_ROW, META_SHEET], [["1", None, 2, None], ["x", "y", 3, None]], [4, 9])
    assert [str(dtype) for dtype in frame.dtypes] == ["object"] * 4
    assert data_columns(frame) == ["a", "b"]
    assert ordinals(frame) == [4, 9]
    changed = with_column(frame, "b", ["p", "q"])
    assert column(changed, "b") == ["p", "q"] and column(frame, "b") == [None, "y"]
    appended = with_column(frame, "c", [True, False])
    assert data_columns(appended) == ["a", "b", "c"]
    assert ordinals(take(frame, [False, True])) == [9]


def test_row_labels() -> None:
    assert row_label(17, None) == "r17"
    assert row_label(17, "Wave 2") == "r17 (Wave 2)"
