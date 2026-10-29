"""Property tests (hypothesis): idempotence, stable columns, row accounting, round trips, chunked runs.

Settings come from the profile registered in tests/conftest.py: ``ci`` (derandomized, 200 examples,
no deadline) by default, ``HYPOTHESIS_PROFILE=dev`` for random runs. Generated profiles use the
missing strategies keep and drop only: a fill can turn two different rows into exact duplicates,
which the second pass would then remove, so fills are covered by unit tests instead. Fuzzy matching
has its own stability test in tests/unit/test_fuzzy.py.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from cleandata.core.coerce import canonical_str
from cleandata.core.headers import normalise_headers
from cleandata.core.pipeline import RunOptions, RunResult, run_clean
from cleandata.core.profile import Profile
from cleandata.core.writer import CsvSink, ParquetSink
from support import frame_of

HEADER = ["Respondent ID", "Name", "Amount", "Visits", "Joined", "Opt in", "Extra"]
NAMES = ["Anna Lee", "José Muñoz", "Renée Dubois", "Jürgen Weiß", "Bo"]
DATE_PATTERNS = ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"]
TRUE_FALSE = ["yes", "no", "1", "0", "TRUE", "false"]
TOKENS = ["k.A.", "n/a", "-"]


@dataclass
class Case:
    profile: dict[str, Any]
    rows: list[list[str]]


def spoil(text: str, how: int) -> str:
    """Whitespace, no-break spaces, zero-width spaces and NFD forms that normalisation must undo."""
    if how == 1:
        return f"  {text} "
    if how == 2:
        return text.replace(" ", "\N{NO-BREAK SPACE}")
    if how == 3:
        return unicodedata.normalize("NFD", text)
    if how == 4:
        return text.replace(" ", "  \N{ZERO WIDTH SPACE}")
    return text


def number_text(value: float, german: bool, grouped: bool) -> str:
    text = f"{value:,.2f}" if grouped else f"{value:.2f}"
    if german:
        text = text.replace(",", "_").replace(".", ",").replace("_", ".")
    return text


@st.composite
def cases(draw: Any, chunk_safe: bool = False) -> Case:
    german = draw(st.booleans())
    patterns = draw(st.lists(st.sampled_from(DATE_PATTERNS), min_size=1, max_size=3, unique=True))
    tokens = draw(st.lists(st.sampled_from(TOKENS), max_size=3, unique=True))
    keep_last = draw(st.booleans()) and not chunk_safe
    profile: dict[str, Any] = {
        "version": 1,
        "name": "generated",
        "input": {
            "delimiter": ";" if german else ",",
            "null_tokens": ["", *tokens],
            "locale": {
                "decimal": "," if german else ".",
                "thousands": "." if german else ",",
                "date_formats": patterns,
            },
        },
        "text": {
            "unicode": draw(st.sampled_from(["NFC", "NFKC"])),
            "trim": True,
            "collapse_whitespace": draw(st.booleans()),
        },
        "columns": {
            "respondent_id": {"type": "string", "case": "upper", "required": True},
            "name": {"type": "string", "case": draw(st.sampled_from(["keep", "lower", "upper", "title"]))},
            "amount": {
                "type": "number",
                "min": 0,
                "on_invalid": draw(st.sampled_from(["reject", "null"])),
            },
            "visits": {"type": "integer", "max": 50, "missing": draw(st.sampled_from(["keep", "drop"]))},
            "joined": {"type": "date"},
            "opt_in": {"type": "boolean"},
        },
        "keep_unlisted": draw(st.booleans()),
        "dedupe": {
            "exact": draw(st.booleans()),
            "keys": [{"columns": ["respondent_id"], "keep": "last", "order_by": "visits"}]
            if keep_last
            else [{"columns": ["respondent_id"]}],
        },
    }
    if not profile["keep_unlisted"]:
        profile["headers"] = {"drop": ["extra"]}
    count = draw(st.integers(min_value=0, max_value=25))
    rows: list[list[str]] = []
    for _ in range(count):
        if rows and draw(st.integers(min_value=0, max_value=4)) == 0:
            twin = list(draw(st.sampled_from(rows)))
            twin[1] = spoil(twin[1], draw(st.integers(min_value=0, max_value=4)))
            rows.append(twin)
            continue
        amount = draw(st.one_of(st.integers(min_value=-5, max_value=200_000), st.just(None)))
        day = dt.date(2020, 1, 1) + dt.timedelta(days=draw(st.integers(min_value=0, max_value=2000)))
        row = [
            draw(st.sampled_from(["", " ng-1", "NG-2", "ng-3 ", "NG-4", "NG-5"])),
            spoil(draw(st.sampled_from(NAMES)), draw(st.integers(min_value=0, max_value=4))),
            draw(st.sampled_from(tokens or [""]))
            if amount is None
            else number_text(amount / 4, german, draw(st.booleans())),
            draw(st.sampled_from(["", "3", "17", "51", "x"])),
            day.strftime(draw(st.sampled_from(patterns))),
            draw(st.sampled_from(TRUE_FALSE)),
            draw(st.sampled_from(["", "free text", "a;b,c", 'say "hi"'])),
        ]
        rows.append(row)
    return Case(profile, rows)


def write_case(case: Case, folder: Path) -> tuple[Path, Profile]:
    delimiter = case.profile["input"]["delimiter"]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(HEADER)
    writer.writerows(case.rows)
    source = folder / "input.csv"
    source.write_text(buffer.getvalue(), encoding="utf-8", newline="")
    return source, Profile.model_validate(case.profile)


def clean(source: Path, profile: Profile, folder: Path, name: str, **options: Any) -> RunResult:
    run = RunOptions(
        input=source,
        out=folder / f"{name}.csv",
        rejects=folder / f"{name}.rejects.csv",
        reports=(folder / f"{name}.json",),
        **options,
    )
    result = run_clean(profile, "generated.yaml", "0" * 64, run)
    ledger = result.ledger
    assert ledger.read == ledger.out + ledger.rejected + ledger.duplicates + ledger.empty
    return result


@given(cases())
def test_cleaning_is_idempotent_with_the_canonical_profile(case: Case) -> None:
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        source, profile = write_case(case, folder)
        first = clean(source, profile, folder, "first")
        second = clean(folder / "first.csv", profile.canonical(), folder, "second")
        assert (folder / "second.csv").read_bytes() == (folder / "first.csv").read_bytes()
        assert (second.ledger.rejected, second.ledger.duplicates) == (0, 0)
        assert second.ledger.read == first.ledger.out


@given(cases(), cases())
def test_output_and_rejects_columns_depend_only_on_header_and_profile(case: Case, other: Case) -> None:
    other.profile = case.profile
    headers = []
    for item in (case, other):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source, profile = write_case(item, folder)
            clean(source, profile, folder, "out")
            with (folder / "out.csv").open(encoding="utf-8", newline="") as handle:
                output = next(csv.reader(handle))
            with (folder / "out.rejects.csv").open(encoding="utf-8", newline="") as handle:
                rejects = next(csv.reader(handle))
            headers.append((output, rejects))
    assert headers[0] == headers[1]
    output, rejects = headers[0]
    assert rejects == ["_source_row", "_stage", "_rule", "_detail", *output]


@given(cases(chunk_safe=True), st.integers(min_value=1, max_value=50))
def test_chunked_equals_in_memory(case: Case, size: int) -> None:
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        source, profile = write_case(case, folder)
        memory = clean(source, profile, folder, "memory")
        chunked = clean(source, profile, folder, "chunked", chunked=True, chunk_size=size)
        assert chunked.summary == memory.summary
        for suffix in (".csv", ".rejects.csv"):
            assert (folder / f"chunked{suffix}").read_bytes() == (folder / f"memory{suffix}").read_bytes()


HEADER_TEXT = st.text(alphabet="abcXYZ äöüßé€%#&-_19()", max_size=12)


@given(st.lists(HEADER_TEXT, min_size=1, max_size=8))
def test_header_normalisation_is_idempotent_and_collision_free(raw: list[str]) -> None:
    names = normalise_headers(raw)
    assert len(set(names)) == len(names)
    assert normalise_headers(names) == names


CELL_TEXT = st.text(alphabet='ab ,;"\n\r\té\N{NO-BREAK SPACE}=', max_size=10)


@given(st.lists(st.lists(CELL_TEXT, min_size=3, max_size=3), max_size=10))
def test_csv_round_trip(rows: list[list[str]]) -> None:
    with tempfile.TemporaryDirectory() as name:
        target = Path(name) / "out.csv"
        sink = CsvSink(target, ["a", "b", "c"])
        sink.write(frame_of(["a", "b", "c"], rows))
        sink.commit()
        with target.open(encoding="utf-8", newline="") as handle:
            header, *back = list(csv.reader(handle))
    assert header == ["a", "b", "c"]
    assert back == [[canonical_str(value) for value in row] for row in rows]


@pytest.mark.needs_parquet
@given(
    st.lists(
        st.tuples(
            st.one_of(st.none(), CELL_TEXT),
            st.one_of(st.none(), st.integers(min_value=-(2**40), max_value=2**40)),
            st.one_of(st.none(), st.dates()),
            st.one_of(st.none(), st.booleans()),
        ),
        max_size=10,
    )
)
def test_parquet_round_trip(rows: list[tuple[Any, ...]]) -> None:
    import pyarrow.parquet as pq

    columns = ["s", "i", "d", "b"]
    types = {"s": "string", "i": "integer", "d": "date", "b": "boolean"}
    with tempfile.TemporaryDirectory() as name:
        target = Path(name) / "out.parquet"
        sink = ParquetSink(target, columns, types)
        sink.write(frame_of(columns, [list(row) for row in rows]))
        sink.commit()
        table = pq.read_table(target)
        back = list(zip(*(table.column(c).to_pylist() for c in columns), strict=True)) if rows else []
    assert back == [tuple(row) for row in rows]
