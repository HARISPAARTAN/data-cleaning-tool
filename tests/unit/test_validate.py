from __future__ import annotations

from cleandata.core.coerce import types_stage
from cleandata.core.frame import column
from cleandata.core.text import normalise_stage
from cleandata.core.validate import validate_stage
from support import context, frame_of, profile_of


def run(columns: dict[str, dict[str, object]], rows: list[list[object]], **sections: object):
    profile = profile_of(columns, **sections)
    raw = frame_of(list(columns), rows)
    ctx = context(profile, raw)
    frame, _ = normalise_stage(raw, ctx)
    frame, _ = types_stage(frame, ctx)
    return validate_stage(frame, ctx)


def test_required_fails_on_null_only() -> None:
    frame, events = run({"id": {"type": "string", "required": True}}, [["a"], [""], ["  "]])
    assert column(frame, "id") == ["a"]
    assert [(r.source_row, r.rule, r.detail) for r in events.rejects] == [
        (3, "id.required", "id.required: empty"),
        (4, "id.required", "id.required: empty"),
    ]
    tally = events.rules[0]
    assert (tally.id, tally.checked, tally.failed) == ("id.required", 3, 2)


def test_bounds_are_inclusive() -> None:
    frame, events = run(
        {"age": {"type": "integer", "min": 16, "max": 99}}, [["16"], ["99"], ["15"], ["100"], [""]]
    )
    assert column(frame, "age") == [16, 99, None]
    assert [r.rule for r in events.rejects] == ["age.min", "age.max"]
    assert events.rejects[1].detail == "age.max: 100 > 99"
    by_id = {t.id: t for t in events.rules}
    assert (by_id["age.min"].checked, by_id["age.min"].failed) == (4, 1)
    assert by_id["age.max"].examples == ['r5 "100"']


def test_regex_is_a_full_match_on_the_normalised_string() -> None:
    frame, events = run(
        {"id": {"type": "string", "regex": "NG-[0-9]{6}"}}, [[" NG-123456 "], ["NG-1234567"], ["xNG-123456"]]
    )
    assert column(frame, "id") == ["NG-123456"]
    assert [r.source_row for r in events.rejects] == [3, 4]


def test_allowed_for_strings_and_integers() -> None:
    frame, events = run(
        {
            "country": {"type": "string", "allowed": ["DE", "GB"]},
            "wave": {"type": "integer", "allowed": [1, 2]},
        },
        [["DE", "1"], ["FR", "2"], ["GB", "3"], ["GB", "02"]],
    )
    assert column(frame, "country") == ["DE", "GB"]
    assert column(frame, "wave") == [1, 2]
    assert [r.rule for r in events.rejects] == ["country.allowed", "wave.allowed"]


def test_type_failures_and_several_rules_give_one_reject_row() -> None:
    frame, events = run(
        {"age": {"type": "integer", "max": 99}, "id": {"type": "string", "required": True}},
        [["abc", ""], ["120", "x"]],
    )
    assert column(frame, "id") == []
    first, second = events.rejects
    assert first.rules == ["age.type", "id.required"]
    assert first.detail == 'age.type: "abc" not a number; id.required: empty'
    assert second.rules == ["age.max"]
    assert first.raw == {"age": "abc", "id": ""}


def test_string_columns_have_no_type_rule() -> None:
    _, events = run({"name": {"type": "string"}}, [["a"]])
    assert events.rules == []


def test_redacted_columns_hide_values_in_examples_and_details() -> None:
    _, events = run(
        {"email": {"type": "string", "regex": "[^@]+@[^@]+"}},
        [["anna.example"]],
        report={"redact": ["email"]},
    )
    assert events.rules[0].examples == ['r2 "a***"']
    assert events.rejects[0].detail.startswith('email.regex: "a***" does not match')
