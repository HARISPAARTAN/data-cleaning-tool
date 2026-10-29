"""Stage 7, missing: ``drop`` rows go to the rejects; ``constant``, ``median`` and ``ffill`` fill nulls.

It runs after validation and de-duplication, so medians and forward fills come from the kept rows.
``median`` is ``statistics.median`` for number columns and ``statistics.median_low`` for integer
columns (always an observed value); a fully null column stays null with a warning. ``ffill`` takes
the previous non-null value in source order; leading nulls stay. ``drop`` runs before the fills.
"""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Any, cast

import pandas as pd

from cleandata.core.frame import (
    column,
    ordinals,
    raw_record,
    row_count,
    row_label,
    source_rows,
    source_sheets,
    take,
    with_column,
)
from cleandata.core.ledger import Events, Notice, Reject, RuleTally

if TYPE_CHECKING:
    from cleandata.core.frame import StageContext


def median_of(values: list[object], ctype: str) -> object:
    present = [cast(Any, value) for value in values if value is not None]
    if not present:
        return None
    if ctype == "integer":
        return statistics.median_low(present)
    return float(statistics.median(present))


def missing_stage(frame: pd.DataFrame, ctx: StageContext) -> tuple[pd.DataFrame, Events]:
    events = Events("missing")
    count = row_count(frame)
    rows = source_rows(frame)
    sheets = source_sheets(frame)
    drops = [name for name, plan in ctx.plans.items() if plan.missing == "drop"]
    failures: list[list[str]] = [[] for _ in range(count)]
    for name in drops:
        tally = RuleTally(f"{name}.missing", "missing", name, {"strategy": "drop"})
        for position, value in enumerate(column(frame, name)):
            tally.checked += 1
            if value is None:
                tally.failed += 1
                failures[position].append(tally.id)
                if len(tally.examples) < ctx.limit:
                    tally.examples.append(row_label(rows[position], sheets[position]))
        events.rules.append(tally)
    row_ordinals = ordinals(frame)
    for position, rules in enumerate(failures):
        if rules:
            ordinal = row_ordinals[position]
            detail = "; ".join(f"{rule}: empty" for rule in rules)
            events.rejects.append(
                Reject(
                    ordinal,
                    rows[position],
                    sheets[position],
                    "missing",
                    rules,
                    detail,
                    raw_record(ctx.raw, ordinal),
                )
            )
    if any(failures):
        frame = take(frame, [not rules for rules in failures])
    for name, plan in ctx.plans.items():
        if plan.missing not in ("constant", "median", "ffill"):
            continue
        values = column(frame, name)
        column_tally = events.column(name)
        if plan.missing == "constant":
            fill: object = plan.fill
            result = [fill if value is None else value for value in values]
        elif plan.missing == "median":
            fill = median_of(values, plan.type)
            if fill is None and values:
                events.notices.append(
                    Notice(
                        "missing.all_null",
                        f"{name}: every value is null, so there is no median to fill",
                        key=name,
                    )
                )
            result = [fill if value is None else value for value in values]
        else:
            last = ctx.state.ffill.get(name)
            result = []
            for value in values:
                if value is None:
                    result.append(last)
                else:
                    last = value
                    result.append(value)
            ctx.state.ffill[name] = last
            fill = None
        filled = sum(
            1 for before, after in zip(values, result, strict=True) if before is None and after is not None
        )
        column_tally.filled += filled
        if filled and column_tally.fill_value is None and plan.missing != "ffill":
            column_tally.fill_value = fill
        frame = with_column(frame, name, result)
    return frame, events
