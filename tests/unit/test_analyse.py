"""The analysis behind ``profile`` and ``init``: guesses, file notes and the skeleton."""

from __future__ import annotations

import re

from cleandata.core.analyse import analyse_file, analysis_report, choose_guess
from cleandata.core.profile import parse_profile
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
