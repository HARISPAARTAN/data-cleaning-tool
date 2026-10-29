"""One test per stage contract (docs/architecture.md, table "Pipeline stages"), plus row accounting."""

from __future__ import annotations

import csv
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from cleandata.core import pipeline
from cleandata.core.coerce import Invalid, canonical_str, types_stage
from cleandata.core.dedupe import _sort_key, dedupe_stage, sort_key
from cleandata.core.frame import (
    CarryState,
    StageContext,
    column,
    columns_of,
    data_columns,
    ordinals,
    positional,
    row_count,
    rows_of,
)
from cleandata.core.headers import plan_headers, structure_stage
from cleandata.core.ledger import Ledger
from cleandata.core.missing import missing_stage
from cleandata.core.pipeline import RunOptions, check_options, chunk_problems, run_clean, started_now
from cleandata.core.profile import Profile, load_profile
from cleandata.core.reader import ReadOptions, open_source
from cleandata.core.text import normalise_stage
from cleandata.core.validate import validate_stage
from cleandata.errors import InternalError, MissingExtraError, OutputError, UsageError
from support import FIXTURE_FILES, FIXTURES, PROFILES, SOURCE_DATE_EPOCH, profile_of


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


MESSY_HEADER = "id,name,score,active,joined"


def messy_profile() -> Profile:
    """A chunk-compatible profile with a range rule, a required column, a key rule and a constant fill."""
    return profile_of(
        {
            "id": {"type": "integer", "min": 1, "max": 150},
            "name": {"type": "string", "case": "upper", "required": True},
            "score": {"type": "number", "missing": {"strategy": "constant", "value": 0}},
            "active": {"type": "boolean"},
            "joined": {"type": "date"},
        },
        input={"locale": {"date_formats": ["%Y-%m-%d"]}},
        dedupe={"exact": True, "keys": [{"columns": ["id"], "keep": "first"}]},
    )


def messy_csv(count: int) -> bytes:
    """``count`` records with out-of-range ids, blank names, repeated keys, blank scores and bad dates."""
    lines = [MESSY_HEADER]
    for n in range(count):
        ident = (n * 7) % 170 + 1 if n % 15 != 14 else (n - 3) * 7 % 170 + 1
        name = "" if n % 11 == 0 else f"  name{n % 9} "
        score = "" if n % 6 == 0 else f"{n * 1.5}"
        joined = "2026-13-40" if n % 13 == 12 else f"2026-{n % 12 + 1:02d}-{n % 28 + 1:02d}"
        lines.append(f"{ident},{name},{score},{['yes', 'no', '', 'true'][n % 4]},{joined}")
    return ("\n".join(lines) + "\n").encode()


def run_in(
    tmp_path: Path, source: Path, mode: str, size: int = 10, profile: Profile | None = None
) -> pipeline.RunResult:
    folder = tmp_path / mode
    options = RunOptions(
        input=source,
        out=folder / "out.csv",
        rejects=folder / "rejects.csv",
        reports=(folder / "report.json",),
        chunked=mode == "chunked",
        chunk_size=size,
    )
    return run_clean(profile or messy_profile(), "p.yaml", "0" * 64, options)


def record_chunk_sizes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Wrap the structure stage so every chunk's record count lands in the returned list."""
    sizes: list[int] = []
    real = pipeline.structure_stage

    def spy(frame: pd.DataFrame, *args: Any) -> Any:
        sizes.append(row_count(frame))
        return real(frame, *args)

    monkeypatch.setattr(pipeline, "structure_stage", spy)
    return sizes


@pytest.fixture
def pinned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", SOURCE_DATE_EPOCH)


class NoisyAbort:
    """A sink whose abort cleans up and then fails, as a real one can when the disk misbehaves."""

    def __init__(self, sink: Any) -> None:
        self.sink = sink

    def write(self, frame: pd.DataFrame) -> None:
        self.sink.write(frame)

    def commit(self) -> None:
        self.sink.commit()

    def abort(self) -> None:
        self.sink.abort()
        raise OSError("abort failed")


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True
        if self.fail:
            raise OSError("cannot remove")


def failing_stage(monkeypatch: pytest.MonkeyPatch, call: int, folder: Path) -> list[list[str]]:
    """Make the types stage raise on its ``call``-th chunk; the list gets the temp files seen just before."""
    seen: list[list[str]] = []
    calls: list[int] = []
    name, real = pipeline.STAGES[1]

    def stage(frame: pd.DataFrame, ctx: StageContext) -> Any:
        calls.append(1)
        if len(calls) == call:
            seen.append(sorted(path.name for path in folder.glob("*.tmp")))
            raise RuntimeError(f"stage failed on chunk {call}")
        return real(frame, ctx)

    monkeypatch.setattr(pipeline, "STAGES", (pipeline.STAGES[0], (name, stage), *pipeline.STAGES[2:]))
    return seen


@pytest.mark.parametrize(("mode", "call"), [("chunked", 3), ("memory", 1)])
def test_a_stage_failure_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, call: int
) -> None:
    source = tmp_path / "in.csv"
    source.write_bytes(messy_csv(60))
    seen = failing_stage(monkeypatch, call, tmp_path / mode)
    with pytest.raises(RuntimeError, match=f"stage failed on chunk {call}"):
        run_in(tmp_path, source, mode)
    assert len(seen) == 1 and len(seen[0]) == 2  # the output and the rejects were open when it failed
    assert not list(tmp_path.rglob("*.tmp"))
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([mode, "in.csv"])
    assert list((tmp_path / mode).iterdir()) == []


def test_a_sink_that_fails_to_abort_does_not_keep_the_other_one_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "in.csv"
    source.write_bytes(messy_csv(60))
    failing_stage(monkeypatch, 3, tmp_path / "chunked")
    real_open = pipeline.open_output
    monkeypatch.setattr(pipeline, "open_output", lambda *a, **k: NoisyAbort(real_open(*a, **k)))
    with pytest.raises(OSError, match="abort failed") as caught:
        run_in(tmp_path, source, "chunked")
    assert isinstance(caught.value.__context__, RuntimeError)  # the stage failure is still on record
    assert not list(tmp_path.rglob("*.tmp"))


def test_aborting_the_sinks_always_reaches_the_second_one() -> None:
    output, rejects = Recorder(fail=True), Recorder()
    sinks = pipeline._Sinks(output=output, rejects=rejects)  # type: ignore[arg-type]
    with pytest.raises(OSError, match="cannot remove"):
        sinks.abort()
    assert output.aborted and rejects.aborted


@pytest.mark.usefixtures("pinned_clock")
@pytest.mark.parametrize(("count", "size"), [(60, 20), (60, 60), (60, 30), (60, 1), (30, 100)])
def test_chunked_outputs_equal_the_in_memory_run_whatever_the_last_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int, size: int
) -> None:
    source = tmp_path / "in.csv"
    source.write_bytes(messy_csv(count))
    memory = run_in(tmp_path, source, "memory", size)
    sizes = record_chunk_sizes(monkeypatch)
    chunked = run_in(tmp_path, source, "chunked", size)
    if count % size == 0:
        assert sizes[-1] == 0 and len(sizes) == count // size + 1  # the reader ends with an empty chunk
    for name in ("out.csv", "rejects.csv"):
        assert (tmp_path / "chunked" / name).read_bytes() == (tmp_path / "memory" / name).read_bytes()
    assert chunked.summary == memory.summary
    assert (chunked.ledger.read, chunked.ledger.out) == (memory.ledger.read, memory.ledger.out)
    assert chunked.ledger.rejected > 0 and chunked.ledger.duplicates > 0 and chunked.ledger.out > 0


@pytest.mark.parametrize("mode", ["memory", "chunked"])
def test_a_header_only_input_gives_header_only_outputs(tmp_path: Path, mode: str) -> None:
    source = tmp_path / "in.csv"
    source.write_bytes((MESSY_HEADER + "\n").encode())
    result = run_in(tmp_path, source, mode)
    assert (result.exit_code, result.status, result.summary) == (
        0,
        "ok",
        "read 0 · empty 0 · rejected 0 · duplicates 0 · out 0 · exit 0",
    )
    folder = tmp_path / mode
    assert (folder / "out.csv").read_bytes() == (MESSY_HEADER + "\n").encode()
    rejects_header = f"_source_row,_stage,_rule,_detail,{MESSY_HEADER}\n"
    assert (folder / "rejects.csv").read_bytes() == rejects_header.encode()
    rows = json.loads((folder / "report.json").read_text(encoding="utf-8"))["rows"]
    assert (rows["read"], rows["out"], rows["rejected"], rows["reject_rate"]) == (0, 0, 0, 0)


def test_a_reader_without_any_chunk_is_an_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "in.csv"
    source.write_bytes(messy_csv(3))
    real_open = pipeline.open_source

    class Silent:
        def __init__(self, inner: Any) -> None:
            self.info, self.headers = inner.info, inner.headers

        def chunks(self, size: int | None) -> Iterator[Any]:
            return iter(())

    monkeypatch.setattr(pipeline, "open_source", lambda path, options: Silent(real_open(path, options)))
    with pytest.raises(InternalError, match="no chunk") as caught:
        run_in(tmp_path, source, "memory")
    assert caught.value.exit_code == 1
    assert not (tmp_path / "memory" / "out.csv").exists()


@pytest.mark.usefixtures("pinned_clock")
def test_the_report_is_the_same_in_both_modes_apart_from_the_chunk_only_fields(tmp_path: Path) -> None:
    source = tmp_path / "in.csv"
    source.write_bytes(messy_csv(200))
    run_in(tmp_path, source, "memory", 30)
    run_in(tmp_path, source, "chunked", 30)
    reports = {
        mode: json.loads((tmp_path / mode / "report.json").read_text(encoding="utf-8"))
        for mode in ("memory", "chunked")
    }
    memory, chunked = reports["memory"], reports["chunked"]
    assert (memory["run"]["mode"], memory["run"]["chunk_size"]) == ("in_memory", None)
    assert (chunked["run"]["mode"], chunked["run"]["chunk_size"]) == ("chunked", 30)
    assert chunked["run"]["duration_s"] is None  # SOURCE_DATE_EPOCH is set: no duration in either report
    dropped = 0
    for mode, report in reports.items():
        for run_field in ("mode", "chunk_size", "duration_s"):
            del report["run"][run_field]
        for stats in report["columns"].values():
            for side, key in (("before", "top"), ("after", "top"), ("after", "median")):
                if key not in stats[side]:
                    continue
                dropped += mode == "memory" and stats[side][key] is not None
                assert mode == "memory" or stats[side][key] is None  # chunked runs leave them out
                del stats[side][key]
    assert dropped > 0  # the in-memory report did carry them
    assert memory == chunked


def test_a_report_that_cannot_be_written_fails_before_anything_is_committed(tmp_path: Path) -> None:
    source = tmp_path / "in.csv"
    source.write_bytes(messy_csv(20))
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a folder is needed", encoding="utf-8")
    good = tmp_path / "reports" / "report.json"
    for bad in (blocker / "report.json", blocker / "deeper" / "report.json"):
        options = RunOptions(
            input=source,
            out=tmp_path / "out.csv",
            rejects=tmp_path / "rejects.csv",
            reports=(good, bad),
        )
        message = r"--report .*report\.json cannot be written: .*blocker is not a directory"
        with pytest.raises(OutputError, match=message):
            run_clean(messy_profile(), "p.yaml", "0" * 64, options)
        assert sorted(path.name for path in tmp_path.iterdir()) == ["blocker", "in.csv"]


def test_a_missing_report_folder_is_created_and_a_read_only_one_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "in.csv"
    source.write_bytes(messy_csv(20))
    nested = tmp_path / "new" / "deeper" / "report.json"
    options = RunOptions(input=source, dry_run=True, reports=(nested,))
    run_clean(messy_profile(), "p.yaml", "0" * 64, options)
    assert nested.is_file()
    with monkeypatch.context() as patch:
        patch.setattr(os, "access", lambda *args, **kwargs: False)
        with pytest.raises(OutputError, match="is not writable"):
            check_options(messy_profile(), options)


def test_a_missing_extra_is_reported_before_the_input_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the input was opened before the preflight checks")

    monkeypatch.setattr(pipeline, "open_source", forbidden)
    monkeypatch.setitem(sys.modules, "rapidfuzz", None)
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    fuzzy_profile, _ = load_profile(PROFILES / "02_partner.yaml")
    source = FIXTURES / "02_partner_respondents.csv"
    with pytest.raises(MissingExtraError, match='"fuzzy"') as fuzzy:
        run_clean(
            fuzzy_profile,
            "02_partner.yaml",
            "0" * 64,
            RunOptions(input=source, dry_run=True, reports=(tmp_path / "r.json",)),
        )
    assert fuzzy.value.exit_code == 2
    with pytest.raises(MissingExtraError, match='"parquet"') as parquet:
        run_clean(
            messy_profile(),
            "p.yaml",
            "0" * 64,
            RunOptions(input=source, out=tmp_path / "o.parquet", reports=(tmp_path / "r.json",)),
        )
    assert parquet.value.exit_code == 2
    assert list(tmp_path.iterdir()) == []


def test_the_sort_key_is_public_and_the_old_name_still_works() -> None:
    assert _sort_key is sort_key
    assert sorted([3, None, 1, 2], key=sort_key) == [1, 2, 3, None]
