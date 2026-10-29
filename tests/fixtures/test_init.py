"""``init`` on every fixture gives a skeleton that validates and runs with ``--dry-run`` (exit 0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cleandata.core.profile import load_profile
from support import FIXTURE_FILES, FIXTURES, invoke


def run_init(source: Path, tmp_path: Path) -> Path:
    target = tmp_path / f"{source.stem}.yaml"
    result = invoke(["init", str(source), "--out", str(target)])
    assert result.exit_code == 0, result.stderr
    profile, _ = load_profile(target)
    assert profile.dedupe.exact
    assert all(rule.missing.strategy == "keep" for rule in profile.columns.values())
    assert all(
        rule.min is None and rule.max is None and rule.regex is None for rule in profile.columns.values()
    )
    return target


def dry_run(source: Path, profile: Path, tmp_path: Path) -> None:
    result = invoke(
        ["clean", str(source), "--profile", str(profile), "--dry-run", "--report", str(tmp_path / "r.json")]
    )
    assert result.exit_code == 0, result.stderr


@pytest.mark.parametrize("number", ["01", "02", "03", "04"])
def test_init_skeleton_validates_and_dry_runs(number: str, tmp_path: Path) -> None:
    source = FIXTURES / FIXTURE_FILES[number][0]
    dry_run(source, run_init(source, tmp_path), tmp_path)


def test_init_on_the_large_fixture(tmp_path: Path, large_fixture: Path) -> None:
    dry_run(large_fixture, run_init(large_fixture, tmp_path), tmp_path)


def test_skeleton_contents_for_the_survey(tmp_path: Path) -> None:
    text = run_init(FIXTURES / "01_survey_export_de.csv", tmp_path).read_text(encoding="utf-8")
    assert 'encoding: "utf-8-sig"  # bom' in text
    assert 'delimiter: ";"' in text
    assert "rename: {}" in text
    assert "# Normalised input names: respondent_id, welle, startdatum" in text
    assert 'drop: ["column_14"]' in text
    assert "dedupe:\n  exact: true" in text


def test_skeleton_for_the_workbook_selects_the_wave_sheets(tmp_path: Path) -> None:
    text = run_init(FIXTURES / "03_wave_workbook.xlsx", tmp_path).read_text(encoding="utf-8")
    assert "header_row: 4" in text
    assert 'sheets: ["Wave 1", "Wave 2", "Wave 3"]' in text
    assert 'sheet_column: "source_sheet"' in text
