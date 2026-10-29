"""Stage 5, validate: ``required``, ``min``, ``max``, ``regex``, ``allowed`` and the type marks.

A row with an invalid mark or a failed rule goes to the rejects once, with its first failing rule
in ``_rule`` and every failure in ``_detail``; the report counts every failure, so rule counts may
exceed the rejected rows. Rule ids are ``<column>.<kind>``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pandas as pd

from cleandata.core.coerce import Invalid, canonical_str
from cleandata.core.frame import (
    column,
    ordinals,
    raw_record,
    row_count,
    row_label,
    source_rows,
    source_sheets,
    take,
)
from cleandata.core.ledger import Events, Reject, RuleTally
from cleandata.core.text import display, redact_value

if TYPE_CHECKING:
    from cleandata.core.frame import StageContext
    from cleandata.core.profile import ColumnPlan

KINDS = ("type", "required", "min", "max", "regex", "allowed")


def parameters(plan: ColumnPlan, kind: str) -> dict[str, object]:
    if kind == "type":
        result: dict[str, object] = {"type": plan.type}
        if plan.type == "date":
            result["formats"] = list(plan.settings.date_formats)
        elif plan.type == "datetime":
            result["formats"] = list(plan.settings.datetime_formats)
        elif plan.type in ("integer", "number"):
            result["decimal"] = plan.settings.decimal
            result["thousands"] = plan.settings.thousands
        return result
    if kind == "min":
        return {"min": canonical_str(plan.min)}
    if kind == "max":
        return {"max": canonical_str(plan.max)}
    if kind == "regex":
        return {"regex": plan.regex.pattern if plan.regex is not None else None}
    if kind == "allowed":
        return {"allowed": [canonical_str(value) for value in plan.allowed or ()]}
    return {}


def rules_of(plan: ColumnPlan) -> list[str]:
    """The rule kinds of a column; string columns have no type rule because text never fails to parse."""
    kinds = [] if plan.type == "string" else ["type"]
    if plan.required:
        kinds.append("required")
    if plan.min is not None:
        kinds.append("min")
    if plan.max is not None:
        kinds.append("max")
    if plan.regex is not None:
        kinds.append("regex")
    if plan.allowed is not None:
        kinds.append("allowed")
    return kinds


def describe_rule(plan: ColumnPlan, kind: str) -> str:
    """The rule with its parameter, as the explain log prints it: ``age.max (<= 99)``."""
    if kind == "min":
        return f"{plan.name}.min (>= {canonical_str(plan.min)})"
    if kind == "max":
        return f"{plan.name}.max (<= {canonical_str(plan.max)})"
    if kind == "regex" and plan.regex is not None:
        return f"{plan.name}.regex ({plan.regex.pattern})"
    if kind == "allowed":
        return f"{plan.name}.allowed ({', '.join(canonical_str(v) for v in plan.allowed or ())})"
    if kind == "type":
        return f"{plan.name}.type ({plan.type})"
    return f"{plan.name}.{kind}"


def _check(plan: ColumnPlan, kind: str, value: object) -> str | None:
    """The failure text of one rule on one valid, non-null value, or None when it passes."""
    shown = redact_value(canonical_str(value)) if plan.redact else display(canonical_str(value))
    if kind == "min" and plan.min is not None and cast(Any, value) < plan.min:
        return f"{plan.name}.min: {shown} < {canonical_str(plan.min)}"
    if kind == "max" and plan.max is not None and cast(Any, value) > plan.max:
        return f"{plan.name}.max: {shown} > {canonical_str(plan.max)}"
    if kind == "regex" and plan.regex is not None and plan.regex.fullmatch(canonical_str(value)) is None:
        return f'{plan.name}.regex: "{shown}" does not match {plan.regex.pattern}'
    if kind == "allowed" and plan.allowed is not None and value not in plan.allowed:
        return f'{plan.name}.allowed: "{shown}" is not an allowed value'
    return None


def validate_stage(frame: pd.DataFrame, ctx: StageContext) -> tuple[pd.DataFrame, Events]:
    events = Events("validate")
    count = row_count(frame)
    rows = source_rows(frame)
    sheets = source_sheets(frame)
    failures: list[list[tuple[str, str]]] = [[] for _ in range(count)]
    for name, plan in ctx.plans.items():
        values = column(frame, name)
        raw_values = column(ctx.raw, name)
        for kind in rules_of(plan):
            tally = RuleTally(f"{name}.{kind}", kind, name, parameters(plan, kind))
            for position, value in enumerate(values):
                if kind == "type":
                    if value is None:
                        continue
                    tally.checked += 1
                    if not isinstance(value, Invalid):
                        continue
                    text = canonical_str(value.raw)
                    shown = redact_value(text) if plan.redact else display(text)
                    detail = f'{name}.type: "{shown}" {value.reason}'
                elif kind == "required":
                    tally.checked += 1
                    if value is not None:
                        continue
                    detail = f"{name}.required: empty"
                else:
                    if value is None or isinstance(value, Invalid):
                        continue
                    tally.checked += 1
                    problem = _check(plan, kind, value)
                    if problem is None:
                        continue
                    detail = problem
                tally.failed += 1
                failures[position].append((tally.id, detail))
                if len(tally.examples) < ctx.limit:
                    raw = canonical_str(raw_values[position])
                    shown_raw = redact_value(raw) if plan.redact else display(raw)
                    tally.examples.append(f'{row_label(rows[position], sheets[position])} "{shown_raw}"')
            events.rules.append(tally)
    keep = [not items for items in failures]
    for position, (ordinal, items) in enumerate(zip(ordinals(frame), failures, strict=True)):
        if items:
            events.rejects.append(
                Reject(
                    ordinal,
                    rows[position],
                    sheets[position],
                    "validate",
                    [rule for rule, _ in items],
                    "; ".join(detail for _, detail in items),
                    raw_record(ctx.raw, ordinal),
                )
            )
    return take(frame, keep), events
