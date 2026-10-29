from __future__ import annotations

import pytest

from cleandata.core.dedupe import dedupe_stage
from cleandata.core.frame import column
from cleandata.core.fuzzy import find_clusters, fold
from cleandata.core.profile import FuzzyRule
from support import context, frame_of, profile_of

pytestmark = pytest.mark.needs_fuzzy

COLUMNS = {"block": {"type": "string"}, "first": {"type": "string"}, "last": {"type": "string"}}


def rule(**overrides: object) -> FuzzyRule:
    base: dict[str, object] = {
        "block_on": ["block"],
        "fields": [{"columns": ["first"]}],
        "threshold": 90,
    }
    base.update(overrides)
    return FuzzyRule.model_validate(base)


def frame(rows: list[list[object]]):
    return frame_of(list(COLUMNS), rows)


def test_fold_is_casefolded_accent_free_and_collapsed() -> None:
    assert fold("  MÜLLER  Björn ") == "muller bjorn"


def test_a_pair_scoring_exactly_the_threshold_matches() -> None:
    rows = [["x", "johanneson", None], ["x", "johannesen", None]]
    found = find_clusters(frame(rows), [0, 1], rule())
    assert found.scores == {(0, 1): 90.0}
    assert found.clusters == [[0, 1]]
    assert find_clusters(frame(rows), [0, 1], rule(threshold=90.01)).clusters == []


def test_null_fields_are_left_out_and_weights_renormalised() -> None:
    fields = [{"columns": ["first"], "weight": 1}, {"columns": ["last"], "weight": 3}]
    rows = [["x", "margarethe", None], ["x", "margaretha", "Schmidt"]]
    found = find_clusters(frame(rows), [0, 1], rule(fields=fields))
    assert found.scores == {(0, 1): 90.0}
    rows = [["x", "margarethe", "Schmidt"], ["x", "margaretha", "Schmidt"]]
    found = find_clusters(frame(rows), [0, 1], rule(fields=fields))
    assert found.scores == {(0, 1): 97.5}


def test_no_comparable_field_means_no_match() -> None:
    rows = [["x", None, None], ["x", None, None]]
    assert find_clusters(frame(rows), [0, 1], rule()).clusters == []


def test_blocking_and_null_block_values() -> None:
    rows = [
        ["x", "christophe", None],
        ["y", "christophe", None],
        [None, "christophe", None],
        [None, "christophe", None],
    ]
    found = find_clusters(frame(rows), [0, 1, 2, 3], rule())
    assert found.clusters == []
    assert found.blocks == 2


def test_oversized_blocks_are_skipped() -> None:
    rows = [["x", "christophe", None], ["x", "christophe", None], ["x", "christophe", None]]
    found = find_clusters(frame(rows), [0, 1, 2], rule(max_block_size=2))
    assert found.clusters == []
    assert found.skipped == [("x", 3)]


def test_matches_join_transitively_with_stable_ids() -> None:
    rows = [
        ["x", "christophe", None],
        ["y", "anna", None],
        ["x", "christophs", None],
        ["y", "anna", None],
        ["x", "christoph", None],
    ]
    found = find_clusters(frame(rows), [0, 1, 2, 3, 4], rule())
    assert found.clusters == [[0, 2, 4], [1, 3]]


def run(rows: list[list[object]], action: str):
    profile = profile_of(
        COLUMNS,
        dedupe={
            "exact": False,
            "fuzzy": {"block_on": ["block"], "fields": [{"columns": ["first"]}], "action": action},
        },
    )
    data = frame(rows)
    return dedupe_stage(data, context(profile, data))


def test_flag_keeps_every_row_and_writes_cluster_ids() -> None:
    rows = [["y", "anna", None], ["x", "christophe", None], ["x", "christophs", None], ["y", "anna", None]]
    result, events = run(rows, "flag")
    assert column(result, "dup_cluster") == ["f0001", "f0002", "f0002", "f0001"]
    assert events.rejects == []
    assert [cluster.id for cluster in events.clusters] == ["f0001", "f0002"]
    assert events.clusters[1].pairs == [("r3", "r4", 90.0)]


def test_remove_sends_later_members_to_the_rejects() -> None:
    rows = [["x", "christophe", None], ["x", "christophs", None]]
    result, events = run(rows, "remove")
    assert column(result, "_source_row") == [2]
    assert [(r.rule, r.detail) for r in events.rejects] == [("dedupe.fuzzy", "matches r2, score 90.0")]
