from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import Result

from support import FIXTURES, PROFILES, SOURCE_DATE_EPOCH, invoke

SURVEY = str(FIXTURES / "01_survey_export_de.csv")
SURVEY_PROFILE = str(PROFILES / "01_survey.yaml")
SUMMARY = "read 1026 · empty 8 · rejected 18 · duplicates 18 · out 982 · exit 0\n"
ENV = {"SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH}
HAS_FUZZY = importlib.util.find_spec("rapidfuzz") is not None
HAS_PARQUET = importlib.util.find_spec("pyarrow") is not None
VERSION_LINE = r"clean-data \S+ \(Python 3\.\d+, pandas \S+, extras: [a-z, ]+\)\n"


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
    assert re.fullmatch(VERSION_LINE, result.stdout)


def dry_run(tmp_path: Path, before: Sequence[str] = (), after: Sequence[str] = ()) -> Result:
    """``clean --dry-run`` on the survey with global options ``before`` and clean options ``after``."""
    args = ["clean", SURVEY, "--profile", SURVEY_PROFILE, "--dry-run", "--report", str(tmp_path / "r.json")]
    return invoke([*before, *args, *after], env=ENV)


def test_python_dash_m_runs_the_same_command_line(tmp_path: Path) -> None:
    def run(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "cleandata", *args],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env={**os.environ, **env},
        )

    version = run("--version")
    assert (version.returncode, version.stderr) == (0, "")
    assert re.fullmatch(VERSION_LINE, version.stdout)
    missing = run("profile", str(tmp_path / "absent.csv"))
    assert missing.returncode == 3
    assert "absent.csv: no such file" in missing.stderr
    assert missing.stdout == ""
    # a console that cannot show every character must not turn the output into a traceback
    priced = tmp_path / "priced.csv"
    priced.write_bytes("preis €,menge\n1,2\n".encode())
    narrow = run("profile", str(priced), PYTHONIOENCODING="latin-1")
    assert narrow.returncode == 0
    assert "preis \\u20ac" in narrow.stdout


def test_help_describes_the_clean_options() -> None:
    text = " ".join(invoke(["clean", "--help"]).stdout.split())
    assert "required unless --dry-run" in text
    assert "The YAML profile that says how to clean the file." in text
    assert "Records per chunk with --chunked." in text
    assert "on stderr, at any log level" in text


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


def test_init_refuses_to_write_over_the_input(tmp_path: Path) -> None:
    copy = tmp_path / "in.csv"
    original = Path(SURVEY).read_bytes()
    copy.write_bytes(original)
    spellings = [str(copy), str(tmp_path / "." / "in.csv")]
    if sys.platform == "win32":
        spellings.append(str(copy).upper())
    for spelling in spellings:
        for extra in ([], ["--force"]):
            result = invoke(["init", str(copy), "--out", spelling, *extra])
            assert result.exit_code == 2, (spelling, extra)
            assert "--out is the input file" in result.stderr
            assert copy.read_bytes() == original


@pytest.mark.parametrize("option", ["--json", "--html"])
def test_profile_refuses_to_write_over_the_input(tmp_path: Path, option: str) -> None:
    copy = tmp_path / "in.csv"
    original = Path(SURVEY).read_bytes()
    copy.write_bytes(original)
    result = invoke(["profile", str(copy), option, str(copy)])
    assert result.exit_code == 2
    assert f"{option} is the input file" in result.stderr
    assert copy.read_bytes() == original
    assert not result.stdout


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


@pytest.mark.parametrize("extra", [[], ["--chunked", "--chunk-size", "1"]])
def test_an_unterminated_quote_warns_and_fails_strict(tmp_path: Path, extra: list[str]) -> None:
    source = tmp_path / "quotes.csv"
    source.write_text('id,note\n1,fine\n2,"never closed\n3,swallowed\n', encoding="utf-8")
    profile = tmp_path / "quotes.yaml"
    profile.write_text("version: 1\nname: quotes\ncolumns:\n  id: {type: string}\n  note: {type: string}\n")
    result = clean(tmp_path, *extra, profile=str(profile), source=str(source))
    assert result.exit_code == 0, result.stderr
    assert "read.unterminated_quote: the quoted field that starts on line 3 is never closed" in result.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["rows"]["read"] == 2
    assert [w["code"] for w in report["warnings"]] == ["read.unterminated_quote"]
    strict = clean(tmp_path, "--strict", *extra, profile=str(profile), source=str(source))
    assert strict.exit_code == 4
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


@pytest.mark.parametrize("value", ["abc", "1.5", "99999999999999999999"])
def test_a_bad_source_date_epoch_exits_2_and_writes_nothing(tmp_path: Path, value: str) -> None:
    result = invoke(
        [
            "clean",
            SURVEY,
            "--profile",
            SURVEY_PROFILE,
            "--out",
            str(tmp_path / "out.csv"),
            "--rejects",
            str(tmp_path / "rejects.csv"),
            "--report",
            str(tmp_path / "report.json"),
        ],
        env={"SOURCE_DATE_EPOCH": value},
    )
    assert result.exit_code == 2
    assert "ERROR: SOURCE_DATE_EPOCH must be a whole number of seconds" in result.stderr
    assert "internal error" not in result.stderr
    assert result.stdout == ""
    assert list(tmp_path.iterdir()) == []


def test_a_log_file_in_a_missing_folder_is_a_usage_error(tmp_path: Path) -> None:
    result = invoke(["--log-file", str(tmp_path / "missing" / "run.log"), "profile", SURVEY])
    assert result.exit_code == 2
    assert "Error: cannot open the log file" in result.stderr
    assert str(tmp_path / "missing" / "run.log") in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
    assert not (tmp_path / "missing").exists()


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


@pytest.mark.parametrize("before", [["--quiet"], ["--log-level", "error"], []])
def test_explain_reaches_stderr_at_every_log_level_exactly_once(tmp_path: Path, before: list[str]) -> None:
    result = dry_run(tmp_path, before, ["--explain"])
    assert result.exit_code == 0
    assert result.stdout == SUMMARY
    assert result.stderr.count("INFO: [plan]") == 1
    assert result.stderr.count("INFO: [write]") == 1
    assert '[types]    household_income number(decimal=",", thousands=".")' in result.stderr
    # the other info lines still follow the level
    assert ("INFO: read: 1026 records" in result.stderr) is (before == [])


def test_explain_lines_are_in_the_log_file_even_when_quiet(tmp_path: Path) -> None:
    log_file = tmp_path / "run.log"
    result = dry_run(tmp_path, ["--quiet", "--log-file", str(log_file)], ["--explain"])
    assert result.exit_code == 0
    text = log_file.read_text(encoding="utf-8")
    assert text.count("INFO [plan]") == 1
    assert "INFO read: 1026 records" in text


def test_the_log_file_is_closed_when_the_command_ends(tmp_path: Path) -> None:
    for number, source in enumerate((SURVEY, str(tmp_path / "absent.csv"))):
        log_file = tmp_path / f"run{number}.log"
        result = invoke(["--log-file", str(log_file), "profile", source])
        assert result.exit_code == (0 if number == 0 else 3)
        assert log_file.is_file()
        assert logging.getLogger("cleandata").handlers == []
        assert logging.getLogger("cleandata.explain").handlers == []
        log_file.unlink()  # a handle left open would make this fail on Windows


def test_a_log_file_that_cannot_be_opened_leaves_no_handler_behind(tmp_path: Path) -> None:
    result = invoke(["--log-file", str(tmp_path / "missing" / "run.log"), "profile", SURVEY])
    assert result.exit_code == 2
    assert logging.getLogger("cleandata").handlers == []


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5", "", "\u00b2", "\u0661"])
def test_a_bad_header_row_is_exit_2_without_a_traceback(value: str) -> None:
    result = invoke(["profile", SURVEY, "--header-row", value])
    assert result.exit_code == 2
    assert "a 1-based row number or auto" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("value", ["auto", "1", "01"])
def test_a_good_header_row_is_accepted(value: str) -> None:
    result = invoke(["profile", SURVEY, "--header-row", value])
    assert result.exit_code == 0
    assert "header     row 1\n" in result.stdout


def test_a_header_row_beyond_the_file_is_exit_3() -> None:
    result = invoke(["profile", SURVEY, "--header-row", "5000"])
    assert result.exit_code == 3
    assert "header row 5000 not found" in result.stderr


def test_the_encoding_option_checks_the_codec() -> None:
    bad = invoke(["profile", SURVEY, "--encoding", "klingon"])
    assert bad.exit_code == 2
    assert "unknown encoding 'klingon'" in bad.stderr
    good = invoke(["profile", SURVEY, "--encoding", "utf-8-sig"])
    assert good.exit_code == 0
    assert "encoding   utf-8-sig (given)" in good.stdout


@pytest.mark.parametrize("codec", ["rot13", "zlib_codec"])
def test_a_non_text_codec_is_a_usage_error_everywhere(tmp_path: Path, codec: str) -> None:
    option = invoke(["profile", SURVEY, "--encoding", codec])
    assert option.exit_code == 2
    assert f"{codec!r} is not a Python text encoding" in option.stderr
    changes = (
        lambda d: d["input"].update(encoding=codec),
        lambda d: d["output"]["csv"].update(encoding=codec),
    )
    for number, change in enumerate(changes):
        result = clean(tmp_path, profile=changed_profile(tmp_path, "01_survey.yaml", change))
        assert result.exit_code == 2, (number, result.stderr)
        assert "not a Python text encoding" in result.stderr
        assert "internal error" not in result.stderr


def test_the_delimiter_option_takes_a_word_or_the_character() -> None:
    bad = invoke(["profile", SURVEY, "--delimiter", "::"])
    assert bad.exit_code == 2
    assert "use comma, semicolon, tab or pipe" in bad.stderr
    for value in ("semicolon", "SEMICOLON", ";"):
        good = invoke(["profile", SURVEY, "--delimiter", value])
        assert good.exit_code == 0
        assert "delimiter  ';'" in good.stdout
    tab = invoke(["profile", str(FIXTURES / "04_legacy_partner_latin1.txt"), "--delimiter", "tab"])
    assert tab.exit_code == 0
    assert "delimiter  '\\t'" in tab.stdout


@pytest.mark.parametrize("content", ["a,b,c\n", "a,b\n,\n,\n"])
def test_init_on_a_file_without_usable_columns_is_exit_3(content: str, tmp_path: Path) -> None:
    source = tmp_path / "empty.csv"
    source.write_bytes(content.encode())
    result = invoke(["init", str(source)])
    assert result.exit_code == 3
    assert "ERROR: no usable columns found in empty.csv" in result.stderr
    assert "internal error" not in result.stderr
    assert result.stdout == ""


def test_an_unexpected_error_is_exit_1_and_the_traceback_needs_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("cleandata.core.pipeline.run_clean", broken)
    plain = dry_run(tmp_path)
    assert plain.exit_code == 1
    assert "ERROR: internal error: RuntimeError: boom (run with --log-level debug for the traceback)" in (
        plain.stderr
    )
    assert "Traceback" not in plain.stderr
    assert plain.stdout == ""
    debug = dry_run(tmp_path, ["--log-level", "debug"])
    assert debug.exit_code == 1
    assert "Traceback (most recent call last)" in debug.stderr
    assert "RuntimeError: boom" in debug.stderr


def test_ctrl_c_is_exit_130(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(*args: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("cleandata.core.pipeline.run_clean", interrupted)
    result = dry_run(tmp_path)
    assert result.exit_code == 130
    assert "ERROR: interrupted; temporary files removed" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
