from __future__ import annotations

from cleandata.core.coerce import types_stage
from cleandata.core.dedupe import dedupe_stage
from cleandata.core.frame import CarryState, column
from cleandata.core.missing import median_of, missing_stage
from cleandata.core.text import normalise_stage
from support import context, frame_of, profile_of


def run(
    columns: dict[str, dict[str, object]],
    rows: list[list[object]],
    state: CarryState | None = None,
    **sections: object,
):
    profile = profile_of(columns, **sections)
    raw = frame_of(list(columns), rows)
    ctx = context(profile, raw, state=state)
    frame, _ = normalise_stage(raw, ctx)
    frame, _ = types_stage(frame, ctx)
    frame, _ = dedupe_stage(frame, ctx)
    return missing_stage(frame, ctx)


def test_median_and_median_low() -> None:
    assert median_of([1.0, None, 2.0, 4.0, 10.0], "number") == 3.0
    assert median_of([1, None, 2, 4, 10], "integer") == 2
    assert median_of([None], "integer") is None


def test_median_fills_from_the_kept_rows() -> None:
    frame, events = run(
        {"x": {"type": "number", "missing": "median"}, "n": {"type": "integer", "missing": "median"}},
        [["1", "1"], ["", ""], ["3", "4"], ["8", "6"], ["", ""]],
        dedupe={"exact": False},
    )
    assert column(frame, "x") == [1.0, 3.0, 3.0, 8.0, 3.0]
    assert column(frame, "n") == [1, 4, 4, 6, 4]
    assert (events.columns["x"].filled, events.columns["x"].fill_value) == (2, 3.0)
    assert events.columns["n"].fill_value == 4


def test_constant_is_coerced_to_the_column_type() -> None:
    frame, events = run(
        {"points": {"type": "integer", "missing": {"strategy": "constant", "value": "0"}}}, [[""], ["5"]]
    )
    assert column(frame, "points") == [0, 5]
    assert events.columns["points"].fill_value == 0


def test_ffill_runs_after_dedupe_and_leading_nulls_stay() -> None:
    columns = {"id": {"type": "string"}, "region": {"type": "string", "missing": "ffill"}}
    frame, _ = run(columns, [["a", ""], ["b", "North"], ["b", "North"], ["c", ""], ["d", "South"], ["e", ""]])
    assert column(frame, "region") == [None, "North", "North", "South", "South"]


def test_ffill_carries_state_across_chunks() -> None:
    columns = {"id": {"type": "string"}, "region": {"type": "string", "missing": "ffill"}}
    state = CarryState()
    run(columns, [["a", "North"]], state=state)
    frame, _ = run(columns, [["b", ""]], state=state)
    assert column(frame, "region") == ["North"]


def test_drop_sends_rows_to_the_rejects() -> None:
    frame, events = run(
        {"id": {"type": "string"}, "email": {"type": "string", "missing": "drop"}},
        [["a", ""], ["b", "x@mail.example"]],
    )
    assert column(frame, "id") == ["b"]
    reject = events.rejects[0]
    assert (reject.stage, reject.rule, reject.detail) == ("missing", "email.missing", "email.missing: empty")


def test_all_null_column_warns() -> None:
    _, events = run({"x": {"type": "number", "missing": "median"}}, [[""], [""]])
    assert [notice.code for notice in events.notices] == ["missing.all_null"]
