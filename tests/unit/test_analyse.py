"""The analysis behind ``profile`` and ``init``: guesses, file notes and the skeleton."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import replace
from pathlib import Path

import openpyxl
import pytest

from cleandata import __version__
from cleandata.core.analyse import analyse_file, analysis_report, choose_guess, text_table
from cleandata.core.profile import parse_profile
from cleandata.errors import InputError
from cleandata.skeleton.init import build_skeleton
from support import FIXTURES


def guesses(path_name: str) -> dict[str, str | None]:
    analysis = analyse_file(FIXTURES / path_name)
    return {item.normalised: None if item.guess is None else item.guess.label() for item in analysis.columns}


def test_survey_export_locale_types_and_file_notes() -> None:
    analysis = analyse_file(FIXTURES / "01_survey_export_de.csv")
    info = analysis.info
    assert (info.encoding, info.encoding_method, info.delimiter, info.header_row) == (
        "utf-8-sig",
        "bom",
        ";",
        1,
    )
    assert (analysis.analysed, analysis.empty, analysis.duplicate_records) == (1026, 8, 8)
    assert analysis.duplicate_headers == ["Kommentar"]
    assert analysis.empty_columns == ["column_14"]
    assert analysis.locale == (",", ".")
    assert analysis.date_formats == ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"]
    found = guesses("01_survey_export_de.csv")
    assert found["alter"] == "integer(decimal=',', thousands='.')"
    assert found["ausgaben_letzter_monat_eur"] == "number(decimal=',', thousands='.')"
    assert found["abgeschlossen_am"] == "datetime(%d.%m.%Y %H:%M)"
    assert found["respondent_id"] is None
    alter = next(item for item in analysis.columns if item.normalised == "alter")
    assert alter.null_like == {"k.A.": 3}
    income = next(item for item in analysis.columns if item.normalised == "haushaltseinkommen_eur")
    assert any("English and German style" in note for note in income.notes)


def test_workbook_sheets_and_header_offset() -> None:
    analysis = analyse_file(FIXTURES / "03_wave_workbook.xlsx")
    assert analysis.all_sheets == [
        ("Read me", 1, 2),
        ("Wave 1", 4, 301),
        ("Wave 2", 4, 306),
        ("Wave 3", 4, 301),
        ("Lookup", 1, 12),
    ]
    assert analysis.selected_sheets == ["Wave 1", "Wave 2", "Wave 3"]
    assert analysis.info.ignored_sheets == ["Read me", "Lookup"]
    assert guesses("03_wave_workbook.xlsx")["interview_date"] == "date(%d.%m.%Y)"


def test_legacy_file_is_detected_with_a_warning() -> None:
    analysis = analyse_file(FIXTURES / "04_legacy_partner_latin1.txt")
    assert (analysis.info.encoding, analysis.info.encoding_method, analysis.info.delimiter) == (
        "latin-1",
        "detected",
        "\t",
    )
    assert len(analysis.warnings) == 1
    assert re.fullmatch(
        r"encoding latin-1 was detected \(confidence [01]\.\d+\); pin it with input.encoding",
        analysis.warnings[0],
    )
    assert (analysis.info.short_records, analysis.info.malformed) == (4, 2)
    assert guesses("04_legacy_partner_latin1.txt")["active"] == "boolean"


def test_no_candidate_means_no_guess() -> None:
    assert choose_guess([]) is None


def test_report_keys_and_records() -> None:
    report = analysis_report(analyse_file(FIXTURES / "02_partner_respondents.csv"))
    assert list(report) == [
        "schema_version",
        "kind",
        "title",
        "tool",
        "input",
        "records",
        "sheets",
        "columns",
        "file_notes",
        "warnings",
        "banner",
        "footer",
    ]
    assert report["kind"] == "profile"
    assert report["records"] == {
        "analysed": 2480,
        "empty": 0,
        "short": 0,
        "malformed": 0,
        "duplicates": 25,
        "stopped_early": False,
    }


def test_skeleton_validates_and_guesses_no_rules() -> None:
    text = build_skeleton(analyse_file(FIXTURES / "01_survey_export_de.csv"), "survey")
    assert text.startswith("# Profile skeleton from clean-data init ")
    assert '  null_tokens: ["", "k.A."]' in text
    assert '  drop: ["column_14"]  # empty in the analysed records' in text
    profile = parse_profile(text, "skeleton")
    assert profile.columns["alter"].type == "integer"
    assert profile.columns["startdatum"].type == "date"
    assert all(rule.required is False and rule.allowed is None for rule in profile.columns.values())


def workbook(path: Path, rows: list[list[object]]) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


@pytest.mark.parametrize("content", ["a,b,c\n", "a,b\n,\n,\n"])
def test_a_csv_without_usable_columns_is_an_input_error(content: str, tmp_path: Path) -> None:
    source = tmp_path / "empty.csv"
    source.write_text(content, encoding="utf-8")
    with pytest.raises(InputError, match="no usable columns found in empty.csv"):
        build_skeleton(analyse_file(source), "empty")


def test_a_header_only_workbook_is_an_input_error(tmp_path: Path) -> None:
    source = workbook(tmp_path / "header_only.xlsx", [["a", "b", "c"]])
    with pytest.raises(InputError, match="no usable columns found in header_only.xlsx"):
        build_skeleton(analyse_file(source), "header_only")


def test_an_explicit_header_row_is_kept_for_a_workbook_skeleton() -> None:
    automatic = analyse_file(FIXTURES / "03_wave_workbook.xlsx", sheets=["Wave 1"])
    assert "  header_row: 4\n" in build_skeleton(automatic, "waves")
    explicit = analyse_file(FIXTURES / "03_wave_workbook.xlsx", header_row=5, sheets=["Wave 1"])
    assert [sheet.header_row for sheet in explicit.info.sheets] == [5]
    assert "  header_row: 5\n" in build_skeleton(explicit, "waves")


def test_plain_english_decimals_vote_for_the_locale(tmp_path: Path) -> None:
    source = tmp_path / "mixed.csv"
    source.write_text(
        'english_a,english_b,german,whole\n1.5,0.25,"1,5",1\n2.5,7.75,"2,5",2\n3.25,1.5,"3,25",3\n',
        encoding="utf-8",
    )
    analysis = analyse_file(source)
    assert analysis.locale_votes == {"decimal '.', thousands none": 2, "decimal ',', thousands none": 1}
    assert analysis.locale == (".", None)
    guesses_by_name = {item.normalised: item.guess for item in analysis.columns}
    whole = guesses_by_name["whole"]
    assert whole is not None and whole.type == "integer"


@pytest.mark.parametrize(
    ("separator", "escaped"), [("\n", "\\n"), ("\r", "\\r"), ("\u0085", "\\u0085"), (" ", "\\u2028")]
)
def test_a_file_name_with_a_line_break_stays_inside_the_comment(separator: str, escaped: str) -> None:
    analysis = analyse_file(FIXTURES / "01_survey_export_de.csv")
    analysis.info = replace(analysis.info, name=f"export{separator}review: yes.csv")
    text = build_skeleton(analysis, "survey")
    comment = f"# Profile skeleton from clean-data init {__version__} for export{escaped}review: yes.csv."
    assert text.startswith(comment + "\n# Review every column.")
    description = parse_profile(text, "skeleton").description
    assert description == f"Skeleton generated from export{separator}review: yes.csv"


def test_a_long_file_name_keeps_its_full_length_up_to_the_name_width() -> None:
    analysis = analyse_file(FIXTURES / "01_survey_export_de.csv")
    analysis.info = replace(analysis.info, name="a" * 100 + ".csv")
    assert f"for {'a' * 100}.csv.\n" in build_skeleton(analysis, "survey")
    analysis.info = replace(analysis.info, name="b" * 200 + ".csv")
    assert f"for {'b' * 119}~.\n" in build_skeleton(analysis, "survey")


def test_the_table_escapes_headers_and_truncates_the_normalised_name(tmp_path: Path) -> None:
    long_name = "a_very_long_header_name_that_does_not_fit_the_table"
    source = tmp_path / "headers.csv"
    source.write_bytes(f'"first\nline\tx",{long_name}\n1,2\n'.encode())
    lines = text_table(analyse_file(source)).splitlines()
    first = next(line for line in lines if line.startswith("  1  "))
    second = next(line for line in lines if line.startswith("  2  "))
    assert first.startswith("  1  first\\nline\\tx ")
    assert "\t" not in first
    assert f" {long_name[:25]}~ " in second
    assert long_name not in second
    assert len(first) == len(second)


def test_the_table_prints_a_capped_distinct_count(tmp_path: Path) -> None:
    source = tmp_path / "many.csv"
    source.write_text("id\n" + "".join(f"value{n}\n" for n in range(10_001)), encoding="utf-8")
    analysis = analyse_file(source)
    assert analysis.columns[0].distinct_capped
    assert "10000+" in text_table(analysis)


def test_native_excel_dates_are_labelled_native(tmp_path: Path) -> None:
    rows: list[list[object]] = [
        ["day", "visits"],
        [dt.date(2026, 1, 5), 3],
        [dt.date(2026, 1, 6), 4],
        [dt.date(2026, 2, 1), 5],
    ]
    analysis = analyse_file(workbook(tmp_path / "dates.xlsx", rows))
    guess = analysis.columns[0].guess
    assert guess is not None and guess.label() == "date (native)"
    text = build_skeleton(analysis, "dates")
    assert "# date (native) parses 100.0 % of the values" in text
    assert "date()" not in text
    assert "date()" not in text_table(analysis)
