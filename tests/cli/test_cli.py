from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from support import FIXTURES, PROFILES, SOURCE_DATE_EPOCH, invoke

SURVEY = str(FIXTURES / "01_survey_export_de.csv")
SURVEY_PROFILE = str(PROFILES / "01_survey.yaml")
SUMMARY = "read 1026 · empty 8 · rejected 18 · duplicates 18 · out 982 · exit 0\n"
ENV = {"SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH}
HAS_FUZZY = importlib.util.find_spec("rapidfuzz") is not None
HAS_PARQUET = importlib.util.find_spec("pyarrow") is not None


def changed_profile(tmp_path: Path, source: str, change: Any) -> str:
    data = yaml.safe_load((PROFILES / source).read_text(encoding="utf-8"))
    change(data)
    target = tmp_path / f"changed-{source}"
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return str(target)


def clean(tmp_path: Path, *extra: str, profile: str = SURVEY_PROFILE, source: str = SURVEY):
    return invoke(
        [
            "clean",
            source,
            "--profile",
            profile,
            "--out",
            str(tmp_path / "out.csv"),
            "--rejects",
            str(tmp_path / "rejects.csv"),
            "--report",
            str(tmp_path / "report.json"),
            *extra,
        ],
        env=ENV,
    )


def test_version() -> None:
    result = invoke(["--version"])
    assert result.exit_code == 0
    assert re.fullmatch(r"clean-data \S+ \(Python 3\.\d+, pandas \S+, extras: [a-z, ]+\)\n", result.stdout)


def test_profile_command_prints_a_table(tmp_path: Path) -> None:
    result = invoke(
        ["profile", SURVEY, "--json", str(tmp_path / "p.json"), "--html", str(tmp_path / "p.html")]
    )
    assert result.exit_code == 0
    assert "01_survey_export_de.csv" in result.stdout
    assert "encoding   utf-8-sig (bom)" in result.stdout
    assert "haushaltseinkommen_eur" in result.stdout
    assert (tmp_path / "p.json").is_file() and (tmp_path / "p.html").is_file()


def test_init_prints_yaml_and_refuses_to_overwrite(tmp_path: Path) -> None:
    result = invoke(["init", SURVEY])
    assert result.exit_code == 0
    assert result.stdout.startswith("# Profile skeleton from clean-data")
    target = tmp_path / "p.yaml"
    assert invoke(["init", SURVEY, "--out", str(target)]).exit_code == 0
    again = invoke(["init", SURVEY, "--out", str(target)])
    assert again.exit_code == 2
    assert "pass --force" in again.stderr
    assert invoke(["init", SURVEY, "--out", str(target), "--force"]).exit_code == 0


def test_clean_prints_the_summary_line(tmp_path: Path) -> None:
    result = clean(tmp_path)
    assert result.exit_code == 0
    assert result.stdout == SUMMARY
    assert {p.name for p in tmp_path.iterdir()} == {"out.csv", "rejects.csv", "report.json"}


def test_bad_option_is_exit_2() -> None:
    result = invoke(["clean", SURVEY, "--profile", SURVEY_PROFILE, "--nope"])
    assert result.exit_code == 2


def test_invalid_profile_is_exit_2_with_the_path(tmp_path: Path) -> None:
    profile = changed_profile(tmp_path, "01_survey.yaml", lambda d: d["columns"]["age"].update(mx=99))
    result = clean(tmp_path, profile=profile)
    assert result.exit_code == 2
    assert "columns.age.mx: Extra inputs are not permitted" in result.stderr


def test_chunk_incompatible_profile_is_exit_2_listing_every_reason(tmp_path: Path) -> None:
    result = clean(tmp_path, "--chunked")
    assert result.exit_code == 2
    for reason in ("drop_empty_columns", "dedupe.key.1", "household_income.missing", "satisfaction.missing"):
        assert reason in result.stderr
    assert not (tmp_path / "out.csv").exists()


@pytest.mark.skipif(HAS_FUZZY, reason="only meaningful without the fuzzy extra (core-only job)")
def test_missing_fuzzy_extra_is_exit_2(tmp_path: Path) -> None:
    result = clean(
        tmp_path,
        profile=str(PROFILES / "02_partner.yaml"),
        source=str(FIXTURES / "02_partner_respondents.csv"),
    )
    assert result.exit_code == 2
    assert 'needs the optional extra "fuzzy"' in result.stderr


@pytest.mark.skipif(HAS_PARQUET, reason="only meaningful without the parquet extra (core-only job)")
def test_missing_parquet_extra_is_exit_2(tmp_path: Path) -> None:
    result = invoke(
        [
            "clean",
            SURVEY,
            "--profile",
            SURVEY_PROFILE,
            "--out",
            str(tmp_path / "o.parquet"),
            "--report",
            str(tmp_path / "r.json"),
        ]
    )
    assert result.exit_code == 2
    assert 'needs the optional extra "parquet"' in result.stderr


def test_out_equal_to_the_input_is_exit_2(tmp_path: Path) -> None:
    copy = tmp_path / "in.csv"
    copy.write_bytes(Path(SURVEY).read_bytes())
    result = invoke(
        [
            "clean",
            str(copy),
            "--profile",
            SURVEY_PROFILE,
            "--out",
            str(copy),
            "--report",
            str(tmp_path / "r.json"),
        ]
    )
    assert result.exit_code == 2
    assert "is the input file" in result.stderr
    assert copy.read_bytes() == Path(SURVEY).read_bytes()


def test_missing_file_is_exit_3(tmp_path: Path) -> None:
    result = clean(tmp_path, source=str(tmp_path / "absent.csv"))
    assert result.exit_code == 3
    assert "no such file" in result.stderr


def test_missing_column_is_exit_3(tmp_path: Path) -> None:
    def add(data: dict[str, Any]) -> None:
        data["columns"]["panel_id"] = {"type": "string"}

    result = clean(tmp_path, profile=changed_profile(tmp_path, "01_survey.yaml", add))
    assert result.exit_code == 3
    assert "no column 'panel_id'" in result.stderr


def test_no_matching_sheet_is_exit_3(tmp_path: Path) -> None:
    profile = changed_profile(tmp_path, "03_waves.yaml", lambda d: d["input"].update(sheets=["Results *"]))
    result = clean(tmp_path, profile=profile, source=str(FIXTURES / "03_wave_workbook.xlsx"))
    assert result.exit_code == 3
    assert "no sheet matches" in result.stderr


def test_strict_is_exit_4_and_writes_rejects_and_reports_only(tmp_path: Path) -> None:
    result = clean(tmp_path, "--strict")
    assert result.exit_code == 4
    assert result.stdout == SUMMARY.replace("exit 0", "exit 4")
    assert {p.name for p in tmp_path.iterdir()} == {"rejects.csv", "report.json"}
    assert '"status": "failed_strict"' in (tmp_path / "report.json").read_text(encoding="utf-8")


def test_quality_gate_is_exit_4(tmp_path: Path) -> None:
    profile = changed_profile(tmp_path, "01_survey.yaml", lambda d: d["quality"].update(max_reject_rate=0.01))
    result = clean(tmp_path, profile=profile)
    assert result.exit_code == 4
    assert '"status": "failed_gate"' in (tmp_path / "report.json").read_text(encoding="utf-8")
    assert not (tmp_path / "out.csv").exists()


def test_output_directory_is_exit_5(tmp_path: Path) -> None:
    (tmp_path / "out.csv").mkdir()
    result = clean(tmp_path)
    assert result.exit_code == 5
    assert "is a directory" in result.stderr


def test_dry_run_writes_only_reports(tmp_path: Path) -> None:
    result = clean(tmp_path, "--dry-run", "--report", str(tmp_path / "report.html"))
    assert result.exit_code == 0
    assert {p.name for p in tmp_path.iterdir()} == {"report.json", "report.html"}
    text = (tmp_path / "report.json").read_text(encoding="utf-8")
    assert '"dry_run": true' in text
    assert '"title": "Survey wave export, German locale"' in text


def test_explain_goes_to_stderr_and_leaves_stdout_unchanged(tmp_path: Path) -> None:
    result = clean(tmp_path, "--explain")
    assert result.stdout == SUMMARY
    assert '[types]    household_income number(decimal=",", thousands=".")' in result.stderr
    assert 'r2 "45.000,00" -> 45000.0 · r3 "38.500" -> 38500.0' in result.stderr
    assert 'age.max (<= 99)  failed 3 · r118 "130" · r544 "150" · r871 "101"' in result.stderr
    assert (
        "dedupe.key.1 [respondent_id] keep last by completed_at  removed 6 · r40 superseded by r902"
        in result.stderr
    )


def test_report_twice_and_three_times(tmp_path: Path) -> None:
    result = clean(tmp_path, "--report", str(tmp_path / "report.html"))
    assert result.exit_code == 0
    assert (tmp_path / "report.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    three = clean(tmp_path, "--report", str(tmp_path / "a.html"), "--report", str(tmp_path / "b.json"))
    assert three.exit_code == 2


def test_log_levels_and_log_file(tmp_path: Path) -> None:
    quiet = invoke(
        [
            "--quiet",
            "clean",
            SURVEY,
            "--profile",
            SURVEY_PROFILE,
            "--dry-run",
            "--report",
            str(tmp_path / "r.json"),
        ]
    )
    assert quiet.exit_code == 0
    assert quiet.stderr == ""
    info = clean(tmp_path)
    assert "INFO: read: 1026 records from 01_survey_export_de.csv" in info.stderr
    log_file = tmp_path / "run.log"
    debug = invoke(
        [
            "--log-level",
            "warning",
            "--log-file",
            str(log_file),
            "clean",
            SURVEY,
            "--profile",
            SURVEY_PROFILE,
            "--dry-run",
            "--report",
            str(tmp_path / "r2.json"),
        ]
    )
    assert debug.exit_code == 0
    assert "INFO:" not in debug.stderr
    assert re.search(
        r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d INFO read: 1026 records",
        log_file.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
