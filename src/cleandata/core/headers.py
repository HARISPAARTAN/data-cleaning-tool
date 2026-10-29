"""Stage 2, structure: empty records, header normalisation, rename and drop, the sheet column.

``snake_case`` applies NFKC; ``ä`` ae, ``ö`` oe, ``ü`` ue, ``ß`` ss (plus a few letters NFKD cannot
split, such as ``ø``), other accents stripped; ``€`` eur, ``£`` gbp, ``$`` usd, ``%`` pct, ``#`` no,
``&`` and; camelCase and acronym boundaries split; lower case; runs of other characters become ``_``,
trimmed; empty becomes ``column_<position>``; a leading digit gets ``c_``; repeats get ``_2``, ``_3``
left to right, skipping taken names. It is idempotent on its own output.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import pandas as pd

from cleandata.core.frame import (
    META_COLUMNS,
    META_SHEET,
    column,
    new_frame,
    ordinals,
    positional,
    row_count,
)
from cleandata.core.ledger import Events, Notice
from cleandata.errors import InputError, ProfileError

if TYPE_CHECKING:
    from cleandata.core.profile import Profile

LETTERS: Final = str.maketrans(
    {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "Ä": "Ae",
        "Ö": "Oe",
        "Ü": "Ue",
        "ß": "ss",
        "\N{LATIN CAPITAL LETTER SHARP S}": "SS",
        "ø": "o",
        "Ø": "O",
        "æ": "ae",
        "Æ": "Ae",
        "œ": "oe",
        "Œ": "Oe",
        "ł": "l",
        "Ł": "L",
        "đ": "d",
        "Đ": "D",
    }
)
SYMBOLS: Final = (
    ("€", " eur "),
    ("£", " gbp "),
    ("$", " usd "),
    ("%", " pct "),
    ("#", " no "),
    ("&", " and "),
)
ACRONYM: Final = re.compile(r"([A-Z]+)([A-Z][a-z])")
CAMEL: Final = re.compile(r"([a-z0-9])([A-Z])")
OTHER: Final = re.compile(r"[^a-z0-9]+")


def snake_case(raw: str, position: int) -> str:
    """Normalise one header; ``position`` is 1-based and names an empty header."""
    text = unicodedata.normalize("NFKC", raw).translate(LETTERS)
    for symbol, word in SYMBOLS:
        text = text.replace(symbol, word)
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    text = CAMEL.sub(r"\1_\2", ACRONYM.sub(r"\1_\2", text)).lower()
    text = OTHER.sub("_", text).strip("_")
    if not text:
        return f"column_{position}"
    if text[0].isdigit():
        text = "c_" + text
    return text


def keep_name(raw: str, position: int) -> str:
    text = raw.strip()
    return text if text else f"column_{position}"


def dedupe_names(names: Sequence[str]) -> list[str]:
    """Give repeated names ``_2``, ``_3``, ... left to right, skipping names that are already taken."""
    first: dict[str, int] = {}
    for index, name in enumerate(names):
        first.setdefault(name, index)
    taken = set(first)
    result: list[str] = []
    for index, name in enumerate(names):
        if first[name] == index:
            result.append(name)
            continue
        suffix = 2
        while f"{name}_{suffix}" in taken:
            suffix += 1
        result.append(f"{name}_{suffix}")
        taken.add(result[-1])
    return result


def header_bases(raw: Sequence[str], mode: str = "snake_case") -> list[str]:
    """Each header normalised on its own, before repeats are numbered."""
    convert = snake_case if mode == "snake_case" else keep_name
    return [convert(name, position) for position, name in enumerate(raw, start=1)]


def normalise_headers(raw: Sequence[str], mode: str = "snake_case") -> list[str]:
    return dedupe_names(header_bases(raw, mode))


@dataclass(frozen=True)
class HeaderMapping:
    """One line of the report's header table. ``position`` is the 0-based raw column, if any."""

    position: int | None
    raw: str | None
    normalised: str | None
    final: str | None
    action: str


@dataclass(frozen=True)
class OutputColumn:
    name: str
    source: str
    position: int | None = None


@dataclass(frozen=True)
class HeaderPlan:
    mappings: tuple[HeaderMapping, ...]
    output: tuple[OutputColumn, ...]
    notices: tuple[Notice, ...]

    @property
    def columns(self) -> list[str]:
        return [item.name for item in self.output]


def plan_headers(
    raw: Sequence[str], profile: Profile, empty_positions: frozenset[int] = frozenset()
) -> HeaderPlan:
    """Decide what happens to every raw column; raises for collisions and absent required columns."""
    options = profile.headers
    normalised = normalise_headers(raw, options.normalise)
    wanted = profile.columns
    sheet = profile.input.sheet_column
    mappings: list[HeaderMapping] = []
    kept: dict[str, int] = {}
    unlisted: list[str] = []
    dropped_unlisted: list[str] = []
    dropped_empty: list[str] = []
    for position, (raw_name, norm) in enumerate(zip(raw, normalised, strict=True)):
        if norm in options.drop:
            mappings.append(HeaderMapping(position, raw_name, norm, None, "dropped"))
            continue
        final = options.rename.get(norm, norm)
        if position in empty_positions and profile.input.drop_empty_columns and final not in wanted:
            mappings.append(HeaderMapping(position, raw_name, norm, None, "dropped_empty"))
            dropped_empty.append(norm)
            continue
        if final in wanted:
            action = "renamed" if final != norm else "kept"
        elif profile.keep_unlisted:
            if final.startswith("_"):
                raise ProfileError(
                    f"headers: input column {position + 1} ({raw_name!r}) would be kept as {final!r}, "
                    "but names starting with '_' are reserved for internal columns; "
                    "list it in headers.drop or give it a name in headers.rename"
                )
            action = "kept_unlisted"
        else:
            mappings.append(HeaderMapping(position, raw_name, norm, None, "dropped_unlisted"))
            dropped_unlisted.append(norm)
            continue
        if final in kept or final == sheet:
            other = "the sheet column" if final == sheet else f"column {kept[final] + 1}"
            raise ProfileError(
                f"headers: input column {position + 1} ({raw_name!r}) and {other} both become {final!r}; "
                "rename one of them or list one in headers.drop"
            )
        kept[final] = position
        if action == "kept_unlisted":
            unlisted.append(final)
        mappings.append(HeaderMapping(position, raw_name, norm, final, action))
    missing = [
        name for name, rule in wanted.items() if name not in kept and name != sheet and not rule.optional
    ]
    if missing:
        names = ", ".join(sorted(set(normalised)))
        raise InputError(
            "the input has no column "
            + ", ".join(repr(name) for name in missing)
            + f" (normalised headers: {names})"
        )
    output: list[OutputColumn] = []
    for name in wanted:
        if name == sheet:
            output.append(OutputColumn(name, "sheet"))
        elif name in kept:
            output.append(OutputColumn(name, "data", kept[name]))
        else:
            output.append(OutputColumn(name, "absent"))
            mappings.append(HeaderMapping(None, None, None, name, "absent_optional"))
    output.extend(OutputColumn(name, "data", kept[name]) for name in unlisted)
    if sheet is not None and sheet not in wanted:
        output.append(OutputColumn(sheet, "sheet"))
    if sheet is not None:
        mappings.append(HeaderMapping(None, None, None, sheet, "sheet_column"))
    notices: list[Notice] = []
    if dropped_unlisted:
        notices.append(
            Notice(
                "structure.unlisted_dropped",
                "unlisted input columns dropped (list them in headers.drop to silence this): "
                + ", ".join(dropped_unlisted),
                len(dropped_unlisted),
            )
        )
    if dropped_empty:
        notices.append(
            Notice(
                "structure.empty_columns_dropped",
                "empty columns dropped: " + ", ".join(dropped_empty),
                len(dropped_empty),
                "info",
            )
        )
    repeats = [
        norm
        for norm, base in zip(normalised, header_bases(raw, options.normalise), strict=True)
        if norm != base
    ]
    if repeats:
        notices.append(
            Notice(
                "structure.repeated_headers",
                "repeated headers numbered: " + ", ".join(repeats),
                len(repeats),
                "info",
            )
        )
    absent = [item.name for item in output if item.source == "absent"]
    if absent:
        notices.append(
            Notice(
                "structure.optional_absent",
                "optional columns absent, filled with null: " + ", ".join(absent),
                len(absent),
                "info",
            )
        )
    return HeaderPlan(tuple(mappings), tuple(output), tuple(notices))


def is_empty_cell(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def empty_positions(frame: pd.DataFrame, width: int) -> frozenset[int]:
    """Raw columns without a single non-empty cell (for ``drop_empty_columns``)."""
    return frozenset(
        position
        for position in range(width)
        if all(is_empty_cell(v) for v in column(frame, positional(position)))
    )


def structure_stage(
    frame: pd.DataFrame, plan: HeaderPlan, width: int, drop_empty_rows: bool
) -> tuple[pd.DataFrame, Events]:
    """Drop empty records, then build the frame with final column names in output order."""
    events = Events("structure")
    raw_columns = [column(frame, positional(position)) for position in range(width)]
    count = row_count(frame)
    keep = [True] * count
    if drop_empty_rows:
        for row in range(count):
            if all(is_empty_cell(values[row]) for values in raw_columns):
                keep[row] = False
        events.counts["empty"] = keep.count(False)
    index = [ordinal for ordinal, flag in zip(ordinals(frame), keep, strict=True) if flag]
    sheets = [value for value, flag in zip(column(frame, META_SHEET), keep, strict=True) if flag]
    columns: list[list[object]] = []
    for item in plan.output:
        if item.source == "data" and item.position is not None:
            source = raw_columns[item.position]
            columns.append([value for value, flag in zip(source, keep, strict=True) if flag])
        elif item.source == "sheet":
            columns.append(list(sheets))
        else:
            columns.append([None] * len(index))
    meta = [
        [value for value, flag in zip(column(frame, name), keep, strict=True) if flag]
        for name in META_COLUMNS
    ]
    names = plan.columns + list(META_COLUMNS)
    rows = list(zip(*(columns + meta), strict=True)) if index else []
    return new_frame(names, rows, index), events
