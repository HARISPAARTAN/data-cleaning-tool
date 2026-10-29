"""Stage 4, types: numbers and integers with locale settings, booleans, dates and datetimes.

Parsing is strict. A number may carry a sign, a decimal separator and, when ``thousands`` is set,
correctly grouped thousands (``1.234.567``); an exponent is accepted only without grouping. A space or
no-break space counts as a group separator only inside such a group, so ``10 20`` is two numbers, not
1020, and a leading zero never starts a group (``0.500`` is ambiguous). Under German settings ``1.5`` is
invalid, never 15. Dates try the patterns in profile order with a full match
and count the first pattern that matched. Native Excel values pass where they fit.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import TYPE_CHECKING, Final

import pandas as pd

from cleandata.core.frame import (
    META_ROW,
    META_SHEET,
    column,
    data_columns,
    row_label,
    with_column,
)
from cleandata.core.ledger import Events, Notice
from cleandata.core.text import display, redact_value

if TYPE_CHECKING:
    from cleandata.core.frame import StageContext
    from cleandata.core.profile import ColumnPlan

COLUMN_TYPES: Final = ("string", "integer", "number", "boolean", "date", "datetime")
INT64_MIN: Final = -(2**63)
INT64_MAX: Final = 2**63 - 1
MIDNIGHT: Final = dt.time(0, 0)
GROUP_SPACES: Final = " \N{NO-BREAK SPACE}"
NOT_A_NUMBER: Final = "not a number"
AMBIGUOUS_SEPARATOR: Final = "ambiguous separator"


@dataclass(frozen=True)
class Invalid:
    """A cell that failed coercion; ``raw`` is the value before parsing."""

    raw: object
    reason: str


@dataclass(frozen=True)
class ParseSettings:
    """The effective locale of one column. True and false values are stored casefolded."""

    decimal: str = "."
    thousands: str | None = None
    date_formats: tuple[str, ...] = ("%Y-%m-%d",)
    datetime_formats: tuple[str, ...] = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")
    true_values: frozenset[str] = frozenset({"true", "yes", "y", "1"})
    false_values: frozenset[str] = frozenset({"false", "no", "n", "0"})


CANONICAL_SETTINGS: Final = ParseSettings(
    decimal=".",
    thousands=None,
    date_formats=("%Y-%m-%d",),
    datetime_formats=("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"),
    true_values=frozenset({"true"}),
    false_values=frozenset({"false"}),
)


def canonical_str(value: object) -> str:
    """The canonical text of a value, exactly as the CSV writer prints it; null is the empty string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, Invalid):
        return canonical_str(value.raw)
    return str(value)


def json_value(value: object) -> object:
    """Dates and datetimes as ISO strings for the JSON report; other typed values are JSON-safe already."""
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    return value


@lru_cache(maxsize=64)
def _number_patterns(
    decimal: str, thousands: str | None
) -> tuple[re.Pattern[str], re.Pattern[str] | None, re.Pattern[str] | None]:
    """The plain pattern, the grouped one and the one for a zero-led group (both None without grouping)."""
    d = re.escape(decimal)
    plain = re.compile(
        rf"(?P<sign>[+-]?)(?:(?P<int>[0-9]+)(?:{d}(?P<frac>[0-9]+))?|{d}(?P<frac2>[0-9]+))"
        r"(?P<exp>[eE][+-]?[0-9]+)?"
    )
    if thousands is None:
        return plain, None, None
    t = re.escape(thousands)
    spaces = re.escape(GROUP_SPACES)
    grouped = re.compile(
        rf"(?P<sign>[+-]?)(?P<int>[1-9][0-9]{{0,2}}(?:(?:{t}|[{spaces}])[0-9]{{3}})+)(?:{d}(?P<frac>[0-9]+))?"
    )
    zero_led = re.compile(rf"[+-]?0[0-9]{{0,2}}(?:{t}[0-9]{{3}})+(?:{d}[0-9]+)?")
    return plain, grouped, zero_led


def _read_number(text: str, settings: ParseSettings, strip: tuple[str, ...]) -> Decimal | str:
    """The value of a number written with the column's separators, or the reason it is not one."""
    text = _strip(text, strip).strip()
    if not text:
        return NOT_A_NUMBER
    plain, grouped, zero_led = _number_patterns(settings.decimal, settings.thousands)
    match = plain.fullmatch(text)
    if match is not None:
        integer = match.group("int") or "0"
        fraction = match.group("frac") or match.group("frac2") or ""
        exponent = match.group("exp") or ""
    else:
        match = None if grouped is None else grouped.fullmatch(text)
        if match is None:
            if zero_led is not None and zero_led.fullmatch(text) is not None:
                return AMBIGUOUS_SEPARATOR
            return NOT_A_NUMBER
        integer = re.sub("[^0-9]", "", match.group("int"))
        fraction = match.group("frac") or ""
        exponent = ""
    literal = f"{match.group('sign')}{integer}{'.' + fraction if fraction else ''}{exponent}"
    try:
        value = Decimal(literal)
    except InvalidOperation:
        return NOT_A_NUMBER
    if not value.is_finite() or value.adjusted() > 308:
        return NOT_A_NUMBER
    return value


def parse_decimal(text: str, settings: ParseSettings, strip: tuple[str, ...] = ()) -> Decimal | None:
    """Parse a number written with the column's separators; None when it is not one."""
    result = _read_number(text, settings, strip)
    return None if isinstance(result, str) else result


def _strip(text: str, strip: tuple[str, ...]) -> str:
    for part in strip:
        text = text.replace(part, "")
    return text


def to_number(value: object, settings: ParseSettings, strip: tuple[str, ...] = ()) -> float | Invalid:
    if isinstance(value, bool):
        return Invalid(value, "a boolean is not a number")
    if isinstance(value, int):
        if abs(value) > 2**1023:
            return Invalid(value, "out of the floating-point range")
        return float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else Invalid(value, "not a finite number")
    if isinstance(value, str):
        parsed = _read_number(value, settings, strip)
        if isinstance(parsed, str):
            return Invalid(value, parsed)
        return float(parsed)
    return Invalid(value, f"a {type(value).__name__} is not a number")


def to_integer(value: object, settings: ParseSettings, strip: tuple[str, ...] = ()) -> int | Invalid:
    if isinstance(value, bool):
        return Invalid(value, "a boolean is not an integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return Invalid(value, "not a whole number")
        number = int(value)
    elif isinstance(value, str):
        parsed = _read_number(value, settings, strip)
        if isinstance(parsed, str):
            return Invalid(value, parsed)
        if parsed != parsed.to_integral_value():
            return Invalid(value, "not a whole number")
        number = int(parsed)
    else:
        return Invalid(value, f"a {type(value).__name__} is not an integer")
    if not INT64_MIN <= number <= INT64_MAX:
        return Invalid(value, "out of the 64-bit integer range")
    return number


def to_boolean(value: object, settings: ParseSettings, strip: tuple[str, ...] = ()) -> bool | Invalid:
    if isinstance(value, bool):
        return value
    if isinstance(value, dt.date | dt.time):
        return Invalid(value, f"a {type(value).__name__} is not a boolean")
    key = _strip(canonical_str(value), strip).strip().casefold()
    if key in settings.true_values:
        return True
    if key in settings.false_values:
        return False
    return Invalid(value, "not a listed true or false value")


def to_date(
    value: object, settings: ParseSettings, strip: tuple[str, ...] = ()
) -> tuple[dt.date | Invalid, str | None]:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None and value.time() == MIDNIGHT:
            return value.date(), "native"
        return Invalid(value, "a date and time is not a date"), None
    if isinstance(value, dt.date):
        return value, "native"
    if not isinstance(value, str):
        return Invalid(value, f"a {type(value).__name__} is not a date"), None
    text = _strip(value, strip)
    for pattern in settings.date_formats:
        try:
            parsed = dt.datetime.strptime(text, pattern)
        except ValueError:
            continue
        if parsed.tzinfo is None and parsed.time() == MIDNIGHT:
            return parsed.date(), pattern
    return Invalid(value, "not a date (" + ", ".join(settings.date_formats) + ")"), None


def to_datetime(
    value: object, settings: ParseSettings, strip: tuple[str, ...] = ()
) -> tuple[dt.datetime | Invalid, str | None]:
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            return Invalid(value, "time zones are not supported"), None
        return value, "native"
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day), "native"
    if not isinstance(value, str):
        return Invalid(value, f"a {type(value).__name__} is not a datetime"), None
    text = _strip(value, strip)
    for pattern in settings.datetime_formats:
        try:
            parsed = dt.datetime.strptime(text, pattern)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            return parsed, pattern
    return Invalid(value, "not a datetime (" + ", ".join(settings.datetime_formats) + ")"), None


def coerce_value(
    value: object, ctype: str, settings: ParseSettings, strip: tuple[str, ...] = ()
) -> tuple[object, str | None]:
    """Coerce one non-null cell; returns the typed value (or ``Invalid``) and the date pattern used."""
    if ctype == "string":
        return _strip(canonical_str(value), strip), None
    if ctype == "integer":
        return to_integer(value, settings, strip), None
    if ctype == "number":
        return to_number(value, settings, strip), None
    if ctype == "boolean":
        return to_boolean(value, settings, strip), None
    if ctype == "date":
        return to_date(value, settings, strip)
    if ctype == "datetime":
        return to_datetime(value, settings, strip)
    raise ValueError(f"unknown column type {ctype!r}")


def coerce_literal(value: object, ctype: str) -> object:
    """Coerce a scalar written in the profile (min, max, allowed, constant) to a column type."""
    if value is None:
        raise ValueError("a value is required")
    if ctype == "string":
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            raise ValueError(f"{value!r} is not a string; quote it in the YAML")
        return canonical_str(value)
    if ctype == "date":
        if isinstance(value, dt.datetime):
            if value.time() != MIDNIGHT:
                raise ValueError(f"{value!r} has a time; a date column needs a date")
            return value.date()
        if isinstance(value, dt.date):
            return value
        if isinstance(value, str):
            return dt.date.fromisoformat(value)
        raise ValueError(f"{value!r} is not an ISO date")
    if ctype == "datetime":
        if isinstance(value, dt.datetime):
            return value
        if isinstance(value, dt.date):
            return dt.datetime(value.year, value.month, value.day)
        if isinstance(value, str):
            return dt.datetime.fromisoformat(value)
        raise ValueError(f"{value!r} is not an ISO datetime")
    settings = ParseSettings() if ctype == "boolean" else CANONICAL_SETTINGS
    result, _ = coerce_value(value, ctype, settings)
    if isinstance(result, Invalid):
        raise ValueError(f"{value!r} is not a valid {ctype}: {result.reason}")
    return result


def describe(plan: ColumnPlan) -> str:
    """The type with its parse settings, as the explain log prints it."""
    settings = plan.settings
    if plan.type in ("integer", "number"):
        thousands = "none" if settings.thousands is None else f'"{settings.thousands}"'
        return f'{plan.type}(decimal="{settings.decimal}", thousands={thousands})'
    if plan.type == "date":
        return "date(" + " | ".join(settings.date_formats) + ")"
    if plan.type == "datetime":
        return "datetime(" + " | ".join(settings.datetime_formats) + ")"
    return plan.type


def types_stage(frame: pd.DataFrame, ctx: StageContext) -> tuple[pd.DataFrame, Events]:
    """Coerce every profile column; invalid cells become ``Invalid`` marks (or null with on_invalid null)."""
    events = Events("types")
    rows = column(frame, META_ROW)
    sheets = column(frame, META_SHEET)
    nulled_total = 0
    for name in data_columns(frame):
        values = column(frame, name)
        plan = ctx.plans.get(name)
        if plan is None:
            frame = with_column(frame, name, [None if v is None else canonical_str(v) for v in values])
            continue
        tally = events.column(name)
        result: list[object] = []
        for value, row, sheet in zip(values, rows, sheets, strict=True):
            if value is None:
                tally.null += 1
                result.append(None)
                continue
            typed, pattern = coerce_value(value, plan.type, plan.settings, plan.strip)
            shown = redact_value(canonical_str(value)) if plan.redact else display(canonical_str(value))
            if isinstance(typed, Invalid):
                tally.invalid += 1
                if len(tally.invalid_examples) < ctx.limit:
                    tally.invalid_examples.append(f'{row_label(row, sheet)} "{shown}"')
                if plan.on_invalid == "null":
                    tally.nulled += 1
                    result.append(None)
                else:
                    result.append(typed)
                continue
            tally.parsed += 1
            if pattern is not None:
                tally.patterns[pattern] += 1
            text = canonical_str(typed)
            if text != canonical_str(value) and len(tally.type_examples) < ctx.limit:
                after = redact_value(text) if plan.redact else text
                tally.type_examples.append(f'{row_label(row, sheet)} "{shown}" -> {after}')
            result.append(typed)
        nulled_total += tally.nulled
        frame = with_column(frame, name, result)
    if nulled_total:
        events.notices.append(
            Notice(
                "types.nulled_invalid",
                "invalid cells set to null by on_invalid: null",
                nulled_total,
            )
        )
    return frame, events
