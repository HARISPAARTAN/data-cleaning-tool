"""Stage 6, dedupe: exact duplicates, then the key rules in order, then (extra) fuzzy matching.

Exact: equal canonical serialisation over all output columns, the first occurrence stays. Key: equal
canonical values in all key columns; a null in any key column never makes a duplicate; with
``order_by`` each group is ordered by it (ascending, nulls last, stable) before the first or last row
stays. Both use BLAKE2b-128 digests of the canonical strings, in memory and chunked alike, so the two
drivers remove the same rows.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

import pandas as pd

from cleandata.core.coerce import canonical_str
from cleandata.core.frame import (
    column,
    data_columns,
    ordinals,
    raw_record,
    row_count,
    row_label,
    rows_of,
    source_rows,
    source_sheets,
    take,
    with_column,
)
from cleandata.core.fuzzy import find_clusters
from cleandata.core.ledger import Cluster, ClusterMember, Events, Notice, Reject, RuleTally

if TYPE_CHECKING:
    from cleandata.core.frame import StageContext
    from cleandata.core.profile import KeyRule


def row_digest(values: Sequence[object]) -> bytes:
    """BLAKE2b-128 over the canonical strings, null kept distinct from the empty string."""
    payload = json.dumps(
        [None if value is None else canonical_str(value) for value in values],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.blake2b(payload.encode("utf-8", "surrogatepass"), digest_size=16).digest()


def describe_key(rule: KeyRule) -> str:
    text = f"[{', '.join(rule.columns)}] keep {rule.keep}"
    return text + (f" by {rule.order_by}" if rule.order_by else "")


def _sort_key(value: object) -> tuple[bool, object]:
    return (value is None, 0 if value is None else value)


def dedupe_stage(frame: pd.DataFrame, ctx: StageContext) -> tuple[pd.DataFrame, Events]:
    events = Events("dedupe")
    options = ctx.profile.dedupe
    count = row_count(frame)
    rows = source_rows(frame)
    sheets = source_sheets(frame)
    labels = [row_label(row, sheet) for row, sheet in zip(rows, sheets, strict=True)]
    keep = [True] * count
    removed: list[tuple[int, str, str]] = []

    def drop(position: int, tally: RuleTally, winner: int | str) -> None:
        kept = labels[winner] if isinstance(winner, int) else winner
        keep[position] = False
        tally.failed += 1
        removed.append((position, tally.id, f"duplicate of {kept}"))
        if len(tally.examples) < ctx.limit:
            later = isinstance(winner, int) and winner > position
            verb = "superseded by" if later else "duplicate of"
            tally.examples.append(f"{labels[position]} {verb} {kept}")

    if options.exact:
        tally = RuleTally("dedupe.exact", "exact", None, {"columns": "all output columns"})
        for position, values in enumerate(rows_of(frame, data_columns(frame))):
            tally.checked += 1
            digest = row_digest(values)
            first = ctx.state.exact.get(digest)
            if first is None:
                ctx.state.exact[digest] = labels[position]
            else:
                drop(position, tally, first)
        events.rules.append(tally)

    for number, rule in enumerate(options.keys, start=1):
        rule_id = f"dedupe.key.{number}"
        tally = RuleTally(
            rule_id,
            "key",
            None,
            {"columns": list(rule.columns), "keep": rule.keep, "order_by": rule.order_by},
        )
        key_values = rows_of(frame, rule.columns)
        alive = [position for position in range(count) if keep[position]]
        if rule.keep == "first" and rule.order_by is None:
            seen = ctx.state.keys.setdefault(rule_id, {})
            for position in alive:
                values = key_values[position]
                if any(value is None for value in values):
                    continue
                tally.checked += 1
                digest = row_digest(values)
                first = seen.get(digest)
                if first is None:
                    seen[digest] = labels[position]
                else:
                    drop(position, tally, first)
        else:
            groups: dict[bytes, list[int]] = {}
            for position in alive:
                values = key_values[position]
                if any(value is None for value in values):
                    continue
                tally.checked += 1
                groups.setdefault(row_digest(values), []).append(position)
            order = column(frame, rule.order_by) if rule.order_by else None
            for members in groups.values():
                if len(members) < 2:
                    continue
                ordered = members if order is None else sorted(members, key=lambda p: _sort_key(order[p]))
                winner = ordered[0] if rule.keep == "first" else ordered[-1]
                for position in members:
                    if position != winner:
                        drop(position, tally, winner)
        events.rules.append(tally)

    cluster_values: list[object] | None = None
    fuzzy = options.fuzzy
    if fuzzy is not None:
        tally = RuleTally(
            "dedupe.fuzzy",
            "fuzzy",
            None,
            {"block_on": list(fuzzy.block_on), "threshold": fuzzy.threshold, "action": fuzzy.action},
        )
        alive = [position for position in range(count) if keep[position]]
        tally.checked = len(alive)
        found = find_clusters(frame, alive, fuzzy)
        events.counts["fuzzy.blocks"] += found.blocks
        events.counts["fuzzy.blocks_compared"] += found.compared
        for key, size in found.skipped:
            events.notices.append(
                Notice(
                    "dedupe.fuzzy_block_skipped",
                    f"fuzzy blocks above max_block_size ({fuzzy.max_block_size}) skipped, e.g. [{key}] "
                    f"with {size} rows",
                )
            )
        cluster_values = [None] * count
        for number, members in enumerate(found.clusters, start=1):
            cluster_id = f"f{number:04d}"
            pairs = [
                (labels[a], labels[b], round(score, 6))
                for (a, b), score in sorted(found.scores.items())
                if a in members and b in members
            ]
            events.clusters.append(
                Cluster(
                    cluster_id,
                    [ClusterMember(labels[p], rows[p], sheets[p]) for p in members],
                    pairs,
                )
            )
            events.counts["fuzzy.clustered_rows"] += len(members)
            for position in members:
                cluster_values[position] = cluster_id
            if fuzzy.action == "remove":
                for position in members[1:]:
                    partner, score = found.partner(position)
                    keep[position] = False
                    tally.failed += 1
                    removed.append(
                        (position, "dedupe.fuzzy", f"matches {labels[partner]}, score {score:.1f}")
                    )
                    if len(tally.examples) < ctx.limit:
                        tally.examples.append(
                            f"{labels[position]} matches {labels[partner]}, score {score:.1f}"
                        )
            elif len(tally.examples) < ctx.limit:
                tally.examples.append(f"{cluster_id}: " + ", ".join(labels[p] for p in members))
        events.rules.append(tally)

    row_ordinals = ordinals(frame)
    for position, rule_id, detail in sorted(removed):
        ordinal = row_ordinals[position]
        events.rejects.append(
            Reject(
                ordinal,
                rows[position],
                sheets[position],
                "dedupe",
                [rule_id],
                detail,
                raw_record(ctx.raw, ordinal),
            )
        )
    result = take(frame, keep)
    if fuzzy is not None and fuzzy.action == "flag" and cluster_values is not None:
        kept_values = [value for value, flag in zip(cluster_values, keep, strict=True) if flag]
        result = with_column(result, fuzzy.cluster_column, kept_values)
    return result, events
