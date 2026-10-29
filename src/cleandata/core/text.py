"""Stage 3, normalise: Unicode form, control and zero-width characters, whitespace, null tokens, case, map.

The steps run per string cell in this order: Unicode form; control characters (C0 and C1 except tab
and line feed) and zero-width characters removed; U+00A0, U+202F and U+2009 become spaces; trim;
whitespace runs, line breaks included, collapsed to one space; null tokens (compared after trimming,
case-insensitively) become null; case; ``map`` (keys and cell compared casefolded). Native Excel
values are left alone.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

import pandas as pd

from cleandata.core.frame import (
    META_ROW,
    META_SHEET,
    column,
    data_columns,
    row_label,
    with_column,
)
from cleandata.core.ledger import Events

if TYPE_CHECKING:
    from cleandata.core.frame import StageContext

CONTROL_RE: Final = re.compile(
    "[\x00-\x08\x0b-\x1f\x80-\x9f"
    "\N{ZERO WIDTH SPACE}-\N{ZERO WIDTH JOINER}\N{WORD JOINER}\N{ZERO WIDTH NO-BREAK SPACE}]"
)
SPACES: Final = str.maketrans(
    {"\N{NO-BREAK SPACE}": " ", "\N{NARROW NO-BREAK SPACE}": " ", "\N{THIN SPACE}": " "}
)
WHITESPACE_RUN: Final = re.compile(r"\s+")
SPECIAL_WHITESPACE: Final = re.compile(
    r"^\s|\s$|\s{2}|[\N{NO-BREAK SPACE}\N{NARROW NO-BREAK SPACE}\N{THIN SPACE}\t\n\r]"
)


UnicodeForm = Literal["NFC", "NFD", "NFKC", "NFKD", "none"]


@dataclass(frozen=True)
class TextSettings:
    unicode: UnicodeForm = "NFC"
    trim: bool = True
    collapse_whitespace: bool = True
    remove_control: bool = True
    case: str = "keep"


@dataclass(frozen=True)
class TextPlan:
    """Everything stage 3 needs for one column. Null tokens are trimmed and casefolded."""

    settings: TextSettings = TextSettings()
    null_tokens: frozenset[str] = frozenset({""})
    mapping: dict[str, str] = field(default_factory=dict)
    redact: bool = False


def fold_key(text: str) -> str:
    """How ``map`` keys and cells are compared: casefolded, then NFC."""
    return unicodedata.normalize("NFC", text.casefold())


def clean_text(value: str, settings: TextSettings) -> str:
    """Unicode form, control characters, special spaces, trim and collapse."""
    if settings.unicode != "none":
        value = unicodedata.normalize(settings.unicode, value)
    if settings.remove_control:
        value = CONTROL_RE.sub("", value)
    value = value.translate(SPACES)
    if settings.trim:
        value = value.strip()
    if settings.collapse_whitespace:
        value = WHITESPACE_RUN.sub(" ", value)
    return value


def apply_case(value: str, case: str) -> str:
    if case == "lower":
        return value.lower()
    if case == "upper":
        return value.upper()
    if case == "title":
        return value.title()
    return value


def normalise_cell(value: object, plan: TextPlan) -> object:
    """The full stage-3 treatment of one cell; returns None for a null token."""
    if not isinstance(value, str):
        return value
    text = clean_text(value, plan.settings)
    if text.strip().casefold() in plan.null_tokens:
        return None
    text = apply_case(text, plan.settings.case)
    if plan.mapping:
        text = plan.mapping.get(fold_key(text), text)
    return text


INVISIBLE: Final = re.compile(
    "[\x00-\x1f\x7f-\x9f\N{NO-BREAK SPACE}\N{SOFT HYPHEN}\N{THIN SPACE}"
    "\N{ZERO WIDTH SPACE}-\N{RIGHT-TO-LEFT MARK}\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}"
    "\N{NARROW NO-BREAK SPACE}\N{WORD JOINER}\N{ZERO WIDTH NO-BREAK SPACE}]"
)
BACKSLASH: Final = chr(92)
ESCAPES: Final = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def display(text: str) -> str:
    """Example text for logs and reports: line breaks and tabs as ``\\n``, ``\\r`` and ``\\t``, every other
    invisible character as a backslash, ``u`` and four hex digits (a no-break space reads ``u00a0``).
    """
    return INVISIBLE.sub(
        lambda match: ESCAPES.get(match.group(), f"{BACKSLASH}u{ord(match.group()):04x}"), text
    )


def redact_value(text: str) -> str:
    """Keep the first character, then ``***``, then for e-mail-like values the domain."""
    if not text:
        return text
    at = text.find("@")
    return text[0] + "***" + (text[at:] if at > 0 else "")


def has_control(text: str) -> bool:
    return CONTROL_RE.search(text) is not None


def has_whitespace_issue(text: str) -> bool:
    return SPECIAL_WHITESPACE.search(text) is not None


def is_nfc(text: str) -> bool:
    return unicodedata.is_normalized("NFC", text)


def normalise_stage(frame: pd.DataFrame, ctx: StageContext) -> tuple[pd.DataFrame, Events]:
    events = Events("normalise")
    rows = column(frame, META_ROW)
    sheets = column(frame, META_SHEET)
    for name in data_columns(frame):
        column_plan = ctx.plans.get(name)
        plan = column_plan.text if column_plan is not None else ctx.default_text
        values = column(frame, name)
        result = [normalise_cell(value, plan) for value in values]
        if column_plan is not None:
            tally = events.column(name)
            for before, after, row, sheet in zip(values, result, rows, sheets, strict=True):
                if before is None or before == after or not isinstance(before, str):
                    continue
                if after is None:
                    tally.text_nulled += 1
                    if before.strip() == "":
                        continue
                else:
                    tally.text_changed += 1
                if len(tally.text_examples) < ctx.limit:
                    shown = redact_value(before) if plan.redact else display(before)
                    if after is None:
                        tally.text_examples.append(f'{row_label(row, sheet)} "{shown}" -> null')
                    else:
                        out = redact_value(str(after)) if plan.redact else display(str(after))
                        tally.text_examples.append(f'{row_label(row, sheet)} "{shown}" -> "{out}"')
        frame = with_column(frame, name, result)
    return frame, events
