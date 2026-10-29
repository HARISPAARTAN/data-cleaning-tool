from __future__ import annotations

from cleandata.core.coerce import types_stage
from cleandata.core.dedupe import dedupe_stage, row_digest
from cleandata.core.frame import CarryState, column
from cleandata.core.text import normalise_stage
from support import context, frame_of, profile_of

COLUMNS = {"id": {"type": "string"}, "name": {"type": "string"}, "done": {"type": "integer"}}


def run(rows: list[list[object]], state: CarryState | None = None, **dedupe: object):
    profile = profile_of(COLUMNS, dedupe=dedupe)
    raw = frame_of(list(COLUMNS), rows)
    ctx = context(profile, raw, state=state)
    frame, _ = normalise_stage(raw, ctx)
    frame, _ = types_stage(frame, ctx)
    return dedupe_stage(frame, ctx)


def test_digest_tells_null_from_empty_string_and_types_apart() -> None:
    assert row_digest([None]) != row_digest([""])
    assert row_digest(["1"]) == row_digest([1])
    assert len(row_digest(["a"])) == 16


def test_exact_duplicates_after_normalisation_keep_the_first() -> None:
    frame, events = run(
        [
            ["a", "Ann  Lee", "1"],
            ["a", " Ann Lee", "1"],
            ["a", "Ann\N{NO-BREAK SPACE}Lee", "01"],
            ["b", "Bo", "2"],
        ]
    )
    assert column(frame, "_source_row") == [2, 5]
    assert [(r.source_row, r.rule, r.detail) for r in events.rejects] == [
        (3, "dedupe.exact", "duplicate of r2"),
        (4, "dedupe.exact", "duplicate of r2"),
    ]


def test_key_keep_first_and_null_keys() -> None:
    frame, events = run(
        [["a", "x", "1"], ["a", "y", "2"], ["", "z", "3"], ["", "w", "4"]],
        exact=False,
        keys=[{"columns": ["id"]}],
    )
    assert column(frame, "_source_row") == [2, 4, 5]
    assert [r.detail for r in events.rejects] == ["duplicate of r2"]
    assert events.rules[0].checked == 2


def test_key_keep_last_by_order_by_with_ties_and_nulls_last() -> None:
    rows = [["a", "first", "5"], ["a", "second", "9"], ["a", "third", "9"], ["a", "fourth", ""]]
    frame, events = run(rows, exact=False, keys=[{"columns": ["id"], "keep": "last", "order_by": "done"}])
    assert column(frame, "name") == ["fourth"]
    assert [r.source_row for r in events.rejects] == [2, 3, 4]
    assert events.rules[0].examples[0] == "r2 superseded by r5"
    frame, _ = run(rows, exact=False, keys=[{"columns": ["id"], "keep": "first", "order_by": "done"}])
    assert column(frame, "name") == ["first"]
    frame, _ = run(rows[:3], exact=False, keys=[{"columns": ["id"], "keep": "last", "order_by": "done"}])
    assert column(frame, "name") == ["third"]


def test_output_keeps_source_order() -> None:
    rows = [["b", "1", "1"], ["a", "2", "1"], ["b", "3", "2"]]
    frame, _ = run(rows, exact=False, keys=[{"columns": ["id"], "keep": "last"}])
    assert column(frame, "_source_row") == [3, 4]


def test_streaming_state_finds_duplicates_across_chunks() -> None:
    state = CarryState()
    run([["a", "x", "1"]], state=state, keys=[{"columns": ["id"]}])
    frame, events = run([["a", "x", "1"], ["a", "y", "2"]], state=state, keys=[{"columns": ["id"]}])
    assert column(frame, "name") == []
    assert [r.rule for r in events.rejects] == ["dedupe.exact", "dedupe.key.1"]
