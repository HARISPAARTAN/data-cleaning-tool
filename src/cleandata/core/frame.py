"""Frame helpers and the stage context.

Every frame is a pandas DataFrame of object dtype: raw cells are ``str`` (CSV) or native Excel values,
typed cells are plain Python values (``str``, ``int``, ``float``, ``bool``, ``date``, ``datetime``) and
``None`` is null. The index is the record ordinal (0-based over all records after the header row), so a
row can always be found again in the raw snapshot. Keeping pandas behind these helpers keeps dtype
inference out of the pipeline: the same bytes come out with and without pyarrow installed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pandas as pd

if TYPE_CHECKING:
    from cleandata.core.profile import ColumnPlan, Profile
    from cleandata.core.text import TextPlan

META_ROW = "_source_row"
META_SHEET = "_source_sheet"
META_COLUMNS = (META_ROW, META_SHEET)


def positional(index: int) -> str:
    """The name of raw column ``index`` (0-based) before stage 2 names it."""
    return f"#{index}"


def _any(frame: pd.DataFrame) -> Any:
    return cast(Any, frame)


def new_frame(columns: Sequence[str], rows: Sequence[Sequence[object]], index: Sequence[int]) -> pd.DataFrame:
    """Build an object-dtype frame; every row must have one value per column."""
    frame: pd.DataFrame = pd.DataFrame(
        [list(row) for row in rows],
        columns=list(columns),
        index=list(index),
        dtype=object,
    )
    return frame


def columns_of(frame: pd.DataFrame) -> list[str]:
    return [str(name) for name in _any(frame).columns.tolist()]


def data_columns(frame: pd.DataFrame) -> list[str]:
    return [name for name in columns_of(frame) if name not in META_COLUMNS]


def row_count(frame: pd.DataFrame) -> int:
    return len(_any(frame).index)


def ordinals(frame: pd.DataFrame) -> list[int]:
    return [int(value) for value in _any(frame).index.tolist()]


def column(frame: pd.DataFrame, name: str) -> list[object]:
    values: list[object] = list(_any(frame)[name].tolist())
    return values


def source_rows(frame: pd.DataFrame) -> list[int]:
    return [int(cast(int, value)) for value in column(frame, META_ROW)]


def source_sheets(frame: pd.DataFrame) -> list[str | None]:
    return [None if value is None else str(value) for value in column(frame, META_SHEET)]


def with_column(frame: pd.DataFrame, name: str, values: Sequence[object]) -> pd.DataFrame:
    """Return a copy with ``name`` replaced in place, or appended when it is new."""
    result: pd.DataFrame = _any(frame).copy()
    _any(result)[name] = pd.Series(list(values), index=_any(frame).index, dtype=object)
    return result


def take(frame: pd.DataFrame, keep: Sequence[bool]) -> pd.DataFrame:
    """Keep the rows where ``keep`` is true, in their current order."""
    result: pd.DataFrame = _any(frame).loc[list(keep)].copy()
    return result


def select(frame: pd.DataFrame, names: Sequence[str]) -> pd.DataFrame:
    result: pd.DataFrame = _any(frame)[list(names)].copy()
    return result


def reorder(frame: pd.DataFrame, order: Sequence[int]) -> pd.DataFrame:
    """Rows in the given positional order."""
    result: pd.DataFrame = _any(frame).iloc[list(order)].copy()
    return result


def concat(frames: Sequence[pd.DataFrame], columns: Sequence[str]) -> pd.DataFrame:
    if not frames:
        return new_frame(columns, [], [])
    result: pd.DataFrame = pd.concat(list(frames)).astype(object)
    return result


def rows_of(frame: pd.DataFrame, names: Sequence[str]) -> list[tuple[object, ...]]:
    """The values of ``names`` row by row."""
    if not names:
        return [() for _ in range(row_count(frame))]
    return list(zip(*(column(frame, name) for name in names), strict=True))


def rows_at(frame: pd.DataFrame, wanted: Sequence[int]) -> pd.DataFrame:
    """The rows with the given ordinals, in that order."""
    result: pd.DataFrame = _any(frame).loc[list(wanted)].copy()
    return result


def raw_record(raw: pd.DataFrame, ordinal: int) -> dict[str, object]:
    """The raw values of one row of the snapshot taken after stage 2, without meta columns."""
    series = _any(raw).loc[ordinal]
    return {str(key): value for key, value in series.items() if key not in META_COLUMNS}


def row_label(source_row: object, sheet: object) -> str:
    """How reports and rejects refer to a row: ``r17``, or ``r17 (Wave 2)`` for workbook input."""
    label = f"r{source_row}"
    return f"{label} ({sheet})" if sheet else label


@dataclass
class CarryState:
    """State carried from chunk to chunk; an in-memory run is a single chunk with fresh state."""

    exact: dict[bytes, str] = field(default_factory=dict)
    keys: dict[str, dict[bytes, str]] = field(default_factory=dict)
    ffill: dict[str, object] = field(default_factory=dict)


@dataclass
class StageContext:
    profile: Profile
    plans: Mapping[str, ColumnPlan]
    default_text: TextPlan
    raw: pd.DataFrame
    state: CarryState
    in_memory: bool
    limit: int
