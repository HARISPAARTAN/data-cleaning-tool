"""One test per stage contract (docs/architecture.md, table "Pipeline stages"), plus row accounting."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from cleandata.core.coerce import Invalid, canonical_str, types_stage
from cleandata.core.dedupe import dedupe_stage
from cleandata.core.frame import (
    CarryState,
    StageContext,
    column,
    columns_of,
    data_columns,
    ordinals,
    positional,
    rows_of,
)
from cleandata.core.headers import plan_headers, structure_stage
from cleandata.core.ledger import Ledger
from cleandata.core.missing import missing_stage
from cleandata.core.pipeline import RunOptions, chunk_problems, run_clean, started_now
from cleandata.core.profile import Profile, load_profile
from cleandata.core.reader import ReadOptions, open_source
from cleandata.core.text import normalise_stage
from cleandata.core.validate import validate_stage
from cleandata.errors import InternalError, UsageError
from support import FIXTURE_FILES, FIXTURES, PROFILES, profile_of


def stages(number: str) -> Iterator[tuple[str, pd.DataFrame, pd.DataFrame]]:
    """Yield (stage, frame before, frame after) for every stage of one fixture, in memory."""
    data, profile_file = FIXTURE_FILES[number]
    profile, _ = load_profile(PROFILES / profile_file)
    source = open_source(FIXTURES / data, ReadOptions.from_profile(profile))
    chunk = next(iter(source.chunks(None)))
    plan = plan_headers(source.headers, profile)
    structured, _ = structure_stage(chunk.frame, plan, len(source.headers), profile.input.drop_empty_rows)
    yield "structure", chunk.frame, structured
    ctx = context(profile, structured)
    frame = structured
    for name, stage in (
        ("normalise", normalise_stage),
        ("types", types_stage),
        ("validate", validate_stage),
        ("dedupe", dedupe_stage),
        ("missing", missing_stage),
    ):
        after, _ = stage(frame, ctx)
        yield name, frame, after
        frame = after


def context(profile: Profile, raw: pd.DataFrame) -> StageContext:
    return StageContext(
        profile, profile.column_plans(), profile.default_text_plan(), raw, CarryState(), True, 5
    )


@pytest.fixture(scope="module")
def survey() -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    return {name: (before, after) for name, before, after in stages("01")}


@pytest.fixture(scope="module")
def waves() -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    return {name: (before, after) for name, before, after in stages("03")}


def same_values(before: pd.DataFrame, after: pd.DataFrame, names: list[str]) -> bool:
    kept = before.loc[list(after.index)]
    return all(column(kept, name) == column(after, name) for name in names)


def test_structure_changes_names_and_rows_never_cell_values(
    survey: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
) -> None:
    before, after = survey["structure"]
    assert len(after) == len(before) - 8
    plan_columns = data_columns(after)
    source = open_source(
        FIXTURES / "01_survey_export_de.csv", ReadOptions(encoding="utf-8-sig", delimiter=";")
    )
    headers = source.headers
    profile, _ = load_profile(PROFILES / "01_survey.yaml")
    plan = plan_headers(headers, profile)
    for mapping in plan.mappings:
        if mapping.final in plan_columns and mapping.position is not None:
            raw = column(before.loc[list(after.index)], positional(mapping.position))
            assert raw == column(after, mapping.final)


def test_normalise_keeps_names_rows_and_native_values(
    survey: dict[str, tuple[pd.DataFrame, pd.DataFrame]], waves: dict[str, tuple[pd.DataFrame, pd.DataFrame]]
) -> None:
    for frames in (survey, waves):
        before, after = frames["normalise"]
        assert columns_of(before) == columns_of(after)
        assert ordinals(before) == ordinals(after)
        for name in data_columns(before):
            for old, new in zip(column(before, name), column(after, name), strict=True):
                if old is not None and not isinstance(old, str):
                    assert new == old


def test_types_keep_names_and_rows(survey: dict[str, tuple[pd.DataFrame, pd.DataFrame]]) -> None:
    before, after = survey["types"]
    assert columns_of(before) == columns_of(after)
    assert ordinals(before) == ordinals(after)
    assert any(isinstance(value, Invalid) for value in column(after, "start_date"))


def test_validate_changes_only_the_row_set(survey: dict[str, tuple[pd.DataFrame, pd.DataFrame]]) -> None:
    before, after = survey["validate"]
    assert columns_of(before) == columns_of(after)
    assert len(after) == len(before) - 18
    assert same_values(before, after, data_columns(after))
    assert not any(isinstance(v, Invalid) for name in data_columns(after) for v in column(after, name))


def test_dedupe_changes_only_the_row_set(survey: dict[str, tuple[pd.DataFrame, pd.DataFrame]]) -> None:
    before, after = survey["dedupe"]
    assert columns_of(before) == columns_of(after)
    assert len(after) == len(before) - 18
    assert same_values(before, after, data_columns(after))


@pytest.mark.needs_fuzzy
def test_flag_adds_the_cluster_column() -> None:
    frames = {name: (before, after) for name, before, after in stages("02")}
    before, after = frames["dedupe"]
    assert data_columns(after) == [*data_columns(before), "dup_cluster"]
    assert len(after) == len(before) - 65


def test_missing_fills_nulls_only(survey: dict[str, tuple[pd.DataFrame, pd.DataFrame]]) -> None:
    before, after = survey["missing"]
    assert columns_of(before) == columns_of(after)
    assert ordinals(before) == ordinals(after)
    for name in data_columns(before):
        for old, new in zip(column(before, name), column(after, name), strict=True):
            if old is not None:
                assert new == old
    assert column(before, "household_income").count(None) == 20
    assert column(after, "household_income").count(None) == 0


def test_write_leaves_the_frame_alone(tmp_path: Path) -> None:
    profile, data = load_profile(PROFILES / "01_survey.yaml")
    options = RunOptions(
        input=FIXTURES / "01_survey_export_de.csv",
        out=tmp_path / "out.csv",
        reports=(tmp_path / "report.json",),
    )
    result = run_clean(profile, "01_survey.yaml", "0" * 64, options, keep_frames=True)
    assert result.output is not None
    with (tmp_path / "out.csv").open(encoding="utf-8", newline="") as handle:
        header, *rows = list(csv.reader(handle))
    names = data_columns(result.output)
    assert header == names
    assert rows == [[canonical_str(value) for value in row] for row in rows_of(result.output, names)]


@pytest.mark.parametrize("number", ["01", "03", "04"])
def test_row_accounting(number: str, tmp_path: Path) -> None:
    data, profile_file = FIXTURE_FILES[number]
    profile, _ = load_profile(PROFILES / profile_file)
    options = RunOptions(input=FIXTURES / data, dry_run=True, reports=(tmp_path / "r.json",))
    ledger = run_clean(profile, profile_file, "0" * 64, options).ledger
    assert ledger.read == ledger.out + ledger.rejected + ledger.duplicates + ledger.empty


def test_broken_accounting_is_an_internal_error() -> None:
    ledger = Ledger(5, 20)
    ledger.counts["read"] = 3
    ledger.counts["out"] = 2
    with pytest.raises(InternalError, match="row accounting"):
        ledger.check_accounting()


@pytest.mark.parametrize("value", ["abc", "1.5", "12s", "99999999999999999999"])
def test_a_bad_source_date_epoch_is_a_usage_error_before_any_read(
    value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", value)
    profile, _ = load_profile(PROFILES / "01_survey.yaml")
    report = tmp_path / "r.json"
    options = RunOptions(input=tmp_path / "does-not-exist.csv", dry_run=True, reports=(report,))
    with pytest.raises(UsageError, match="SOURCE_DATE_EPOCH must be a whole number of seconds") as caught:
        run_clean(profile, "01_survey.yaml", "0" * 64, options)
    assert caught.value.exit_code == 2
    assert not report.exists()


def test_a_whole_source_date_epoch_pins_the_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1793232000")
    assert started_now() == ("2026-10-29T00:00:00Z", None)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "")
    assert started_now()[1] is not None


def test_the_report_names_every_all_null_median_column(tmp_path: Path) -> None:
    source = tmp_path / "in.csv"
    source.write_text("id,a,b,c\n1,,,5\n2,,,6\n", encoding="utf-8")
    profile = profile_of(
        {
            "id": {"type": "string"},
            "a": {"type": "number", "missing": "median"},
            "b": {"type": "number", "missing": "median"},
            "c": {"type": "number", "missing": "median"},
        }
    )
    options = RunOptions(input=source, dry_run=True, reports=(tmp_path / "r.json",))
    warnings = run_clean(profile, "p.yaml", "0" * 64, options).report["warnings"]
    named = [item for item in warnings if item["code"] == "missing.all_null"]
    assert [item["message"].split(":")[0] for item in named] == ["a", "b"]
    assert all(item["count"] == 1 for item in named)


def test_chunk_problems_list_every_reason(tmp_path: Path) -> None:
    profile, _ = load_profile(PROFILES / "01_survey.yaml")
    options = RunOptions(input=Path("in.xlsx"), out=Path("out.xlsx"), chunked=True)
    reasons = chunk_problems(profile, options)
    assert [reason.split(":")[0].split(" ")[0] for reason in reasons] == [
        "Excel",
        "XLSX",
        "input.drop_empty_columns",
        "dedupe.key.1",
        "columns.household_income.missing",
        "columns.satisfaction.missing",
    ]
