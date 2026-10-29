from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from cleandata.core.frame import column, positional
from cleandata.core.reader import ReadOptions, open_source
from cleandata.errors import InputError
from make_data import write_xlsx


def write(tmp_path: Path, text: str, name: str = "input.csv", encoding: str = "utf-8") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def read_all(path: Path, **options: object):
    source = open_source(path, ReadOptions(**options))  # type: ignore[arg-type]
    chunks = list(source.chunks(None))
    assert len(chunks) == 1
    return source, chunks[0]


def test_record_numbers_follow_physical_lines_with_multiline_fields(tmp_path: Path) -> None:
    path = write(tmp_path, 'id,note\r\na,"two\r\nlines"\r\nb,plain\r\n')
    source, chunk = read_all(path)
    assert source.headers == ["id", "note"]
    assert column(chunk.frame, "_source_row") == [2, 4]
    assert column(chunk.frame, positional(1)) == ["two\r\nlines", "plain"]
    assert chunk.read == 2


def test_short_records_are_padded_and_longer_ones_need_empty_extras(tmp_path: Path) -> None:
    path = write(tmp_path, "a,b,c\n1,2\n1,2,3,,\n1,2,3,4\n")
    source, chunk = read_all(path)
    assert column(chunk.frame, positional(2)) == [None, "3"]
    assert source.info.short_records == 1
    assert [(r.source_row, r.rule, r.detail) for r in chunk.rejects] == [(4, "read.malformed", "1,2,3,4")]
    assert source.info.malformed == 1
    assert chunk.read == 3


def test_blank_records_become_empty_strings_for_the_structure_stage(tmp_path: Path) -> None:
    path = write(tmp_path, "a,b\n\n , \nx,y\n")
    _, chunk = read_all(path)
    assert column(chunk.frame, positional(0)) == ["", "", "x"]
    assert column(chunk.frame, "_source_row") == [2, 3, 4]


def test_header_offset_and_footer_rows(tmp_path: Path) -> None:
    path = write(tmp_path, "Export of 2026-03-01\n\nid;n\na;1\nb;2\nTotal;3\n")
    source, chunk = read_all(path, header_row=3, skip_footer_rows=1, delimiter=";")
    assert source.headers == ["id", "n"]
    assert column(chunk.frame, "_source_row") == [4, 5]
    assert source.info.footer_skipped == 1


def test_header_row_auto_and_missing_header(tmp_path: Path) -> None:
    path = write(tmp_path, "Export;\n;\nid;n\na;1\n")
    source, _ = read_all(path, header_row="auto")
    assert source.info.header_row == 3
    with pytest.raises(InputError, match="header row 9 not found"):
        open_source(path, ReadOptions(header_row=9))


def test_undecodable_bytes_give_the_offset(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_bytes(b"a,b\n" + b"x,y\n" * 10 + b"\xff,z\n")
    with pytest.raises(InputError, match="byte offset 44"):
        list(open_source(path, ReadOptions(encoding="utf-8")).chunks(None))


def test_chunks_carry_record_ordinals(tmp_path: Path) -> None:
    path = write(tmp_path, "a\n" + "".join(f"{i}\n" for i in range(5)))
    source = open_source(path, ReadOptions(delimiter=","))
    chunks = list(source.chunks(2))
    assert [chunk.read for chunk in chunks] == [2, 2, 1]
    assert [list(chunk.frame.index) for chunk in chunks] == [[0, 1], [2, 3], [4]]
    assert source.info.records == 5


def test_missing_and_unsupported_files(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="no such file"):
        open_source(tmp_path / "absent.csv", ReadOptions())
    with pytest.raises(InputError, match="not supported"):
        open_source(write(tmp_path, "x", "old.xls"), ReadOptions())


def workbook(tmp_path: Path) -> Path:
    header = [("s", "Respondent ID"), ("s", "Interview date"), ("s", "Score")]
    first = [
        [("s", "Wave 1 export")],
        [],
        header,
        [("s", "NG-1"), ("d", dt.date(2026, 3, 8)), ("n", 5)],
        [("s", "NG-2"), ("d", dt.date(2026, 3, 9)), ("f", ("B4*2", 7))],
        [("s", "Total"), None, ("n", 12)],
    ]
    second = [
        [("s", "Wave 2 export")],
        [],
        [*header, ("s", "Mode")],
        [("s", "NG-3"), ("s", "10.03.2026"), ("n", 4.5), ("s", "online")],
        [("s", "NG-4"), None, ("n", 3), None, ("s", "stray")],
        [("s", "Total"), None, ("n", 7.5)],
    ]
    lookup = [[("s", "Code"), ("s", "Region")], [("s", "N"), ("s", "North")]]
    path = tmp_path / "waves.xlsx"
    path.write_bytes(write_xlsx([("Wave 1", first), ("Wave 2", second), ("Lookup", lookup)]))
    return path


def test_workbook_native_values_header_offset_footer_and_union(tmp_path: Path) -> None:
    source, chunk = read_all(workbook(tmp_path), header_row=3, skip_footer_rows=1, sheets=("Wave *",))
    assert source.headers == ["Respondent ID", "Interview date", "Score", "Mode"]
    assert source.info.ignored_sheets == ["Lookup"]
    assert [(s.name, s.header_row, s.records) for s in source.info.sheets] == [
        ("Wave 1", 3, 2),
        ("Wave 2", 3, 2),
    ]
    assert column(chunk.frame, "_source_row") == [4, 5, 4]
    assert column(chunk.frame, "_source_sheet") == ["Wave 1", "Wave 1", "Wave 2"]
    assert column(chunk.frame, positional(1)) == [
        dt.datetime(2026, 3, 8),
        dt.datetime(2026, 3, 9),
        "10.03.2026",
    ]
    assert column(chunk.frame, positional(2)) == [5, 7, 4.5]
    assert column(chunk.frame, positional(3)) == [None, None, "online"]
    assert [(r.source_row, r.source_sheet, r.rule) for r in chunk.rejects] == [
        (5, "Wave 2", "read.malformed")
    ]
    assert source.info.footer_skipped == 2


def test_first_sheet_by_default_and_no_matching_sheet(tmp_path: Path) -> None:
    path = workbook(tmp_path)
    source, _ = read_all(path, header_row=3)
    assert [s.name for s in source.info.sheets] == ["Wave 1"]
    with pytest.raises(InputError, match="no sheet matches"):
        open_source(path, ReadOptions(sheets=("Results",)))


def test_workbook_header_auto(tmp_path: Path) -> None:
    source, _ = read_all(workbook(tmp_path), header_row="auto", sheets=("Wave 1",))
    assert source.info.sheets[0].header_row == 3
