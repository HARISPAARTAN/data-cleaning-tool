"""The report files of real runs: JSON keys, HTML sections, redaction and the profile report."""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from cleandata.report.build import round_floats
from cleandata.report.html import render_html, stylesheet
from support import ROOT, clean_fixture, report

TOP_KEYS = [
    "schema_version",
    "kind",
    "title",
    "tool",
    "run",
    "input",
    "profile",
    "headers",
    "rows",
    "columns",
    "rules",
    "dedupe",
    "rejects",
    "warnings",
    "notes",
    "explain",
    "banner",
    "footer",
]
SECTIONS = ["flow", "warnings", "headers", "columns", "rules", "duplicates", "rejects", "run"]
URL = re.compile(
    r"""(?:src|href)\s*=\s*["']?(?:https?:)?//|url\(\s*["']?(?:https?:)?//|@import""", re.IGNORECASE
)


@pytest.fixture(scope="module")
def survey(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, object], str]:
    folder = tmp_path_factory.mktemp("survey")
    result, _ = clean_fixture("01", folder, "--report", str(folder / "01.report.html"), "--explain")
    assert result.exit_code == 0
    return report(folder / "01.report.json"), (folder / "01.report.html").read_text(encoding="utf-8")


def test_json_keys_and_schema_version(survey: tuple[dict[str, object], str]) -> None:
    data, _ = survey
    assert list(data) == TOP_KEYS
    assert data["schema_version"] == 1
    assert data["kind"] == "clean"
    run = data["run"]
    assert isinstance(run, dict)
    assert run["started_at"] == "2026-10-29T00:00:00Z"
    assert run["duration_s"] is None
    assert list(run) == [
        "started_at",
        "duration_s",
        "mode",
        "chunk_size",
        "dry_run",
        "strict",
        "status",
        "exit_code",
    ]


def test_html_has_every_section_and_no_external_resource(survey: tuple[dict[str, object], str]) -> None:
    _, html = survey
    for section in SECTIONS + ["explain"]:
        assert f'<h2 id="{section}">' in html
    assert URL.search(html) is None
    assert stylesheet() in html, "the inline CSS must not be HTML-escaped"
    assert "<th scope=" in html
    assert html.count("<script>") == 1
    assert "Demo with invented data for a fictional company" in html


@pytest.mark.needs_fuzzy
def test_redaction_keeps_first_character_and_domain(tmp_path: Path) -> None:
    result, _ = clean_fixture("02", tmp_path)
    assert result.exit_code == 0
    data = report(tmp_path / "02.report.json")
    preview = data["rejects"]["preview"]
    emails = [row["values"]["email"] for row in preview if row["values"]["email"]]
    assert emails
    assert all(re.fullmatch(r".\*\*\*(@.+)?", email) for email in emails)
    top = data["columns"]["email"]["after"]["top"]
    assert all(value.startswith(value[0] + "***") for value, _ in top)


def test_profile_kind_renders_with_the_same_template(tmp_path: Path) -> None:
    from cleandata.core.analyse import analyse_file, analysis_report

    analysis = analyse_file(ROOT / "fixtures" / "01_survey_export_de.csv")
    html = render_html(round_floats(analysis_report(analysis)))
    assert '<h2 id="file">' in html
    assert URL.search(html) is None


def test_redaction_withholds_the_raw_line_of_malformed_records(tmp_path: Path) -> None:
    result, _ = clean_fixture("04", tmp_path, "--report", str(tmp_path / "04.report.html"))
    assert result.exit_code == 0
    with (tmp_path / "04.rejects.csv").open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["_rule"] == "read.malformed"]
    names = [row["_detail"].split("\t")[1] for row in rows]
    assert [row["_source_row"] for row in rows] == ["198", "282"]
    assert all(name and name in row["_detail"] for name, row in zip(names, rows, strict=True))
    data = report(tmp_path / "04.report.json")
    previews = [row for row in data["rejects"]["preview"] if row["rule"] == "read.malformed"]
    assert [row["source_row"] for row in previews] == [198, 282]
    assert {row["detail"] for row in previews} == {
        "malformed record (raw line withheld: report.redact is set)"
    }
    document = (tmp_path / "04.report.html").read_text(encoding="utf-8")
    json_text = (tmp_path / "04.report.json").read_text(encoding="utf-8")
    for name in names:
        assert name not in json_text
        assert name not in document
    assert "malformed record (raw line withheld: report.redact is set)" in document
