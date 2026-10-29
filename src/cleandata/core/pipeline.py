"""The two drivers, in-memory and chunked, over the same eight stages, and the explain log.

In-memory runs are a single chunk with fresh carried state; chunked runs feed chunk after chunk
through the same stage functions, carrying the digest sets, the forward-fill values, the streaming
statistics and the example buffers. Strict and gate decisions fall at the end, so the cleaned output
goes to a temporary file that is moved into place only on success.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from cleandata.core.coerce import canonical_str, describe, types_stage
from cleandata.core.dedupe import _sort_key, dedupe_stage, describe_key
from cleandata.core.frame import (
    CarryState,
    StageContext,
    column,
    concat,
    ordinals,
    reorder,
    row_count,
    rows_at,
)
from cleandata.core.fuzzy import require as require_fuzzy
from cleandata.core.headers import HeaderPlan, empty_positions, plan_headers, structure_stage
from cleandata.core.ledger import ColumnTally, Events, Ledger
from cleandata.core.missing import missing_stage
from cleandata.core.profile import ColumnPlan, Profile
from cleandata.core.reader import ReadOptions, SourceInfo, open_source
from cleandata.core.stats import StatsCollector
from cleandata.core.text import normalise_stage
from cleandata.core.validate import describe_rule, rules_of, validate_stage
from cleandata.core.writer import (
    OUTPUT_SUFFIXES,
    REJECTS_SUFFIXES,
    REPORT_SUFFIXES,
    RejectsSink,
    Sink,
    check_output_path,
    open_output,
    parquet_available,
)
from cleandata.errors import EXIT_OK, EXIT_QUALITY, MissingExtraError, UsageError

log = logging.getLogger("cleandata")

DEFAULT_CHUNK_SIZE = 100_000
Stage = Callable[[pd.DataFrame, StageContext], tuple[pd.DataFrame, Events]]
STAGES: tuple[tuple[str, Stage], ...] = (
    ("normalise", normalise_stage),
    ("types", types_stage),
    ("validate", validate_stage),
    ("dedupe", dedupe_stage),
    ("missing", missing_stage),
)


@dataclass(frozen=True)
class RunOptions:
    input: Path
    out: Path | None = None
    rejects: Path | None = None
    reports: tuple[Path, ...] = ()
    dry_run: bool = False
    strict: bool = False
    explain: bool = False
    chunked: bool = False
    chunk_size: int = DEFAULT_CHUNK_SIZE


@dataclass
class RunResult:
    exit_code: int
    status: str
    ledger: Ledger
    report: dict[str, Any]
    summary: str
    explain: list[str] = field(default_factory=list)
    output: pd.DataFrame | None = None
    raw: pd.DataFrame | None = None


def chunk_problems(profile: Profile, options: RunOptions) -> list[str]:
    """Every reason the profile and targets cannot run chunked (section 'Chunked mode' of the docs)."""
    reasons: list[str] = []
    if options.input.suffix.lower() in (".xlsx", ".xlsm"):
        reasons.append("Excel input: openpyxl reads whole sheets; run in memory")
    if options.out is not None and options.out.suffix.lower() == ".xlsx":
        reasons.append("XLSX output: the workbook writer cannot append; write CSV or Parquet")
    if options.rejects is not None and options.rejects.suffix.lower() == ".xlsx":
        reasons.append("XLSX rejects: the workbook writer cannot append; write the rejects as CSV")
    if profile.input.drop_empty_columns:
        reasons.append("input.drop_empty_columns needs a full scan; list the columns in headers.drop")
    for number, rule in enumerate(profile.dedupe.keys, start=1):
        if rule.keep == "last" or rule.order_by is not None:
            reasons.append(
                f"dedupe.key.{number}: keep last or order_by needs whole groups; keep first on a sorted file"
            )
    for name, column_rule in profile.columns.items():
        if column_rule.missing.strategy == "median":
            reasons.append(f"columns.{name}.missing: median needs the whole column; use a constant")
    if profile.dedupe.fuzzy is not None:
        reasons.append("dedupe.fuzzy: pairwise work within blocks; run in memory")
    if profile.output.sort_by:
        reasons.append("output.sort_by needs all rows; sort downstream")
    return reasons


def check_options(profile: Profile, options: RunOptions) -> None:
    """Everything that can fail before a byte of data is read (exit 2 or 5)."""
    source = options.input.resolve()
    targets: list[Path] = []
    if options.out is None and not options.dry_run:
        raise UsageError("--out is required unless --dry-run is given")
    if options.out is not None:
        check_output_path(options.out, OUTPUT_SUFFIXES, "--out")
        targets.append(options.out)
        if options.out.suffix.lower() == ".parquet" and not parquet_available():
            raise MissingExtraError("parquet", "Parquet output")
    if options.rejects is not None:
        check_output_path(options.rejects, REJECTS_SUFFIXES, "--rejects")
        targets.append(options.rejects)
    if not options.reports:
        raise UsageError("--report is required (.html or .json, once or twice)")
    for report in options.reports:
        check_output_path(report, REPORT_SUFFIXES, "--report")
        targets.append(report)
    resolved = [target.resolve() for target in targets]
    if source in resolved:
        raise UsageError("an output path is the input file; choose another --out, --rejects or --report")
    if len(set(resolved)) != len(resolved):
        raise UsageError("--out, --rejects and --report must name different files")
    if profile.dedupe.fuzzy is not None:
        require_fuzzy()
    if options.chunked:
        if options.chunk_size < 1:
            raise UsageError("--chunk-size must be at least 1")
        reasons = chunk_problems(profile, options)
        if reasons:
            raise UsageError("the profile cannot run with --chunked:\n  " + "\n  ".join(reasons))


def started_now() -> tuple[str, float | None]:
    """The run's timestamp; with SOURCE_DATE_EPOCH set it is that instant and no duration is measured."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        moment = dt.datetime.fromtimestamp(int(epoch), dt.UTC)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ"), None
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), time.perf_counter()


def sort_frame(frame: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    values = [column(frame, name) for name in names]
    order = sorted(range(row_count(frame)), key=lambda p: [_sort_key(items[p]) for items in values])
    return reorder(frame, order)


def changed_cells(frame: pd.DataFrame, raw: pd.DataFrame, names: list[str]) -> dict[str, int]:
    if not row_count(frame):
        return {name: 0 for name in names}
    before = rows_at(raw, ordinals(frame))
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = sum(
            1
            for old, new in zip(column(before, name), column(frame, name), strict=True)
            if canonical_str(old) != canonical_str(new)
        )
    return counts


def _label(stage: str) -> str:
    return f"[{stage}]".ljust(10) + " "


INDENT = " " * 11


def _joined(items: list[str]) -> str:
    return " · ".join(items)


def explain_plan(
    profile: Profile, plans: dict[str, ColumnPlan], info: SourceInfo, chunked: bool
) -> list[str]:
    """The resolved plan: each stage, each column with its effective settings, each rule id."""
    lines = [_label("plan") + ("chunked run" if chunked else "in-memory run") + f" · profile {profile.name}"]
    if info.kind == "csv":
        delimiter = "none" if info.delimiter is None else repr(info.delimiter)
        lines.append(
            _label("read") + f"encoding {info.encoding} ({info.encoding_method}) · delimiter {delimiter} · "
            f"header row {info.header_row} · footer rows {profile.input.skip_footer_rows}"
        )
    else:
        sheets = ", ".join(sheet.name for sheet in info.sheets)
        lines.append(_label("read") + f"sheets {sheets} · footer rows {profile.input.skip_footer_rows}")
    options = profile.input
    lines.append(
        _label("structure")
        + _joined(
            [
                "drop empty rows" if options.drop_empty_rows else "keep empty rows",
                "drop empty columns" if options.drop_empty_columns else "keep empty columns",
                f"headers {profile.headers.normalise}",
                f"{len(profile.headers.rename)} renames",
                "keep unlisted" if profile.keep_unlisted else "drop unlisted",
            ]
        )
    )
    for name, plan in plans.items():
        text = plan.text.settings
        parts = [describe(plan), f"unicode {text.unicode}"]
        parts += [
            flag
            for flag, on in (
                ("trim", text.trim),
                ("collapse", text.collapse_whitespace),
                ("remove control", text.remove_control),
            )
            if on
        ]
        if text.case != "keep":
            parts.append(f"case {text.case}")
        if plan.text.mapping:
            parts.append(f"map {len(plan.text.mapping)} keys")
        if plan.strip:
            parts.append("strip " + ", ".join(repr(item) for item in plan.strip))
        parts.append("null " + ", ".join(repr(token) for token in sorted(plan.text.null_tokens)))
        parts.append(f"on_invalid {plan.on_invalid}")
        kinds = rules_of(plan)
        parts.append("rules " + ", ".join(f"{name}.{kind}" for kind in kinds) if kinds else "no rules")
        if plan.missing != "keep":
            parts.append(f"missing {plan.missing}")
        lines.append(_label("column") + f"{name} " + _joined(parts))
    dedupe = profile.dedupe
    rules = ["dedupe.exact"] if dedupe.exact else []
    rules += [f"dedupe.key.{n} {describe_key(rule)}" for n, rule in enumerate(dedupe.keys, start=1)]
    if dedupe.fuzzy is not None:
        rules.append(f"dedupe.fuzzy threshold {dedupe.fuzzy.threshold:g} · {dedupe.fuzzy.action}")
    lines.append(_label("dedupe") + (_joined(rules) if rules else "none"))
    if chunked:
        lines.append(
            _label("chunked")
            + "not available: Excel input, XLSX output, drop_empty_columns, keep last and order_by, median, "
            "fuzzy, sort_by"
        )
    return lines


def explain_blocks(
    ledger: Ledger, plans: dict[str, ColumnPlan], profile: Profile, info: SourceInfo, columns: list[str]
) -> list[str]:
    lines = [
        _label("read")
        + _joined(
            [
                f"{ledger.read} records",
                f"{info.malformed} malformed",
                f"{info.short_records} short",
                f"{info.footer_skipped} footer rows skipped",
            ]
        ),
        _label("structure") + f"{ledger.empty} empty records dropped · {len(columns)} output columns",
    ]
    for name in plans:
        tally = ledger.columns.get(name)
        if tally is not None and (tally.text_changed or tally.text_nulled):
            line = _label("normalise") + f"{name}  changed {tally.text_changed} · null {tally.text_nulled}"
            lines.append(line + (" · " + _joined(tally.text_examples) if tally.text_examples else ""))
    for name, plan in plans.items():
        tally = ledger.columns.get(name)
        if tally is None:
            continue
        line = _label("types") + f"{name} {describe(plan)}  parsed {tally.parsed} · null {tally.null} · "
        line += f"invalid {tally.invalid}" + (f" · set to null {tally.nulled}" if tally.nulled else "")
        lines.append(line)
        if tally.type_examples:
            lines.append(INDENT + _joined(tally.type_examples))
        if tally.invalid_examples:
            lines.append(INDENT + "invalid " + _joined(tally.invalid_examples))
    for rule in ledger.rules.values():
        if rule.column is None or rule.kind == "missing" or rule.column not in plans:
            continue
        text = _label("validate") + f"{describe_rule(plans[rule.column], rule.kind)}  failed {rule.failed}"
        lines.append(text + (" · " + _joined(rule.examples) if rule.examples else ""))
    for number, key_rule in enumerate(profile.dedupe.keys, start=1):
        key_tally = ledger.rules.get(f"dedupe.key.{number}")
        if key_tally is not None:
            text = _label("dedupe") + f"{key_tally.id} {describe_key(key_rule)}  removed {key_tally.failed}"
            lines.append(text + (" · " + _joined(key_tally.examples) if key_tally.examples else ""))
    exact = ledger.rules.get("dedupe.exact")
    if exact is not None:
        text = _label("dedupe") + f"dedupe.exact  removed {exact.failed}"
        lines.insert(
            len(lines) - len(profile.dedupe.keys),
            text + (" · " + _joined(exact.examples) if exact.examples else ""),
        )
    fuzzy = ledger.rules.get("dedupe.fuzzy")
    if fuzzy is not None:
        text = _label("dedupe") + f"dedupe.fuzzy  clusters {len(ledger.clusters)} · removed {fuzzy.failed}"
        lines.append(text + (" · " + _joined(fuzzy.examples) if fuzzy.examples else ""))
    for name, plan in plans.items():
        tally = ledger.columns.get(name)
        if plan.missing == "drop":
            missing_tally = ledger.rules.get(f"{name}.missing")
            if missing_tally is not None:
                lines.append(_label("missing") + f"{name} drop  removed {missing_tally.failed}")
        elif plan.missing != "keep" and tally is not None:
            strategy = "median_low" if plan.missing == "median" and plan.type == "integer" else plan.missing
            value = "" if tally.fill_value is None else f" {canonical_str(tally.fill_value)}"
            lines.append(_label("missing") + f"{name} {strategy}{value}  filled {tally.filled}")
    lines.append(_label("write") + f"{ledger.out} rows · {len(columns)} columns")
    return lines


@dataclass
class _Sinks:
    output: Sink | None = None
    rejects: RejectsSink | None = None

    def abort(self) -> None:
        if self.output is not None:
            self.output.abort()
        if self.rejects is not None:
            self.rejects.abort()


def run_clean(
    profile: Profile, profile_name: str, profile_sha256: str, options: RunOptions, keep_frames: bool = False
) -> RunResult:
    """Clean one file; raises ``CleanDataError`` subclasses for exit codes 1, 2, 3 and 5."""
    from cleandata.report.build import ReportInput, build_report, write_reports

    started_at, clock = started_now()
    check_options(profile, options)
    plans = profile.column_plans()
    default_text = profile.default_text_plan()
    source = open_source(options.input, ReadOptions.from_profile(profile))
    info = source.info
    width = len(source.headers)
    ledger = Ledger(profile.report.examples_per_rule, profile.report.preview_rows)
    if info.encoding_method in ("detected", "western"):
        ledger.notice(
            "read.encoding_detected",
            f"the encoding {info.encoding} was detected (confidence {info.encoding_confidence}); pin it with "
            f"input.encoding: {info.encoding}",
        )
    if info.kind == "csv" and info.delimiter is None:
        ledger.notice("read.single_column", "no delimiter found; the file is read as a single column")
    if info.ignored_sheets:
        ledger.notice(
            "read.sheets_ignored",
            "sheets not matched: " + ", ".join(info.ignored_sheets),
            len(info.ignored_sheets),
            "info",
        )
    explain = explain_plan(profile, plans, info, options.chunked) if options.explain else []
    for line in explain:
        log.info(line)
    state = CarryState()
    in_memory = not options.chunked
    sinks = _Sinks()
    plan: HeaderPlan | None = None
    stats: StatsCollector | None = None
    columns: list[str] = []
    data: list[str] = []
    changed: dict[str, int] = {}
    kept: list[pd.DataFrame] = []
    raws: list[pd.DataFrame] = []
    fuzzy = profile.dedupe.fuzzy
    try:
        for chunk in source.chunks(options.chunk_size if options.chunked else None):
            ledger.counts["read"] += chunk.read
            ledger.absorb(Events("read", rejects=chunk.rejects))
            if plan is None:
                empties = (
                    empty_positions(chunk.frame, width)
                    if in_memory and profile.input.drop_empty_columns
                    else frozenset()
                )
                plan = plan_headers(source.headers, profile, empties)
                for notice in plan.notices:
                    ledger.notice(notice.code, notice.message, notice.count, notice.level)
                data = plan.columns
                columns = data + (
                    [fuzzy.cluster_column] if fuzzy is not None and fuzzy.action == "flag" else []
                )
                stats = StatsCollector(columns, plans, default_text.null_tokens, in_memory)
                if not options.dry_run and options.out is not None:
                    output = profile.output
                    sinks.output = open_output(
                        options.out,
                        columns,
                        {name: p.type for name, p in plans.items()},
                        output.csv.delimiter,
                        output.csv.encoding,
                        output.csv.bom,
                        output.xlsx.sheet_name,
                    )
                if not options.dry_run and options.rejects is not None:
                    sinks.rejects = RejectsSink(options.rejects, data, info.kind == "xlsx")
            assert stats is not None
            frame, events = structure_stage(chunk.frame, plan, width, profile.input.drop_empty_rows)
            ledger.absorb(events)
            raw = frame
            stats.update_before(raw)
            ctx = StageContext(
                profile, plans, default_text, raw, state, in_memory, profile.report.examples_per_rule
            )
            rejects = list(chunk.rejects)
            for _, stage in STAGES:
                frame, events = stage(frame, ctx)
                ledger.absorb(events)
                rejects.extend(events.rejects)
            if in_memory and profile.output.sort_by:
                frame = sort_frame(frame, list(profile.output.sort_by))
            for name, count in changed_cells(frame, raw, data).items():
                changed[name] = changed.get(name, 0) + count
            ledger.counts["out"] += row_count(frame)
            stats.update_after(frame)
            if sinks.output is not None:
                sinks.output.write(frame)
            if sinks.rejects is not None:
                sinks.rejects.write(rejects)
            if keep_frames:
                kept.append(frame)
                raws.append(raw)
        if info.short_records:
            ledger.notice("read.short_record", "short records padded with nulls", info.short_records)
        ledger.check_accounting()
        gate = profile.quality.max_reject_rate
        warnings = ledger.warnings()
        if gate is not None and ledger.reject_rate() > gate:
            status, exit_code = "failed_gate", EXIT_QUALITY
        elif options.strict and (ledger.rejected or warnings):
            status, exit_code = "failed_strict", EXIT_QUALITY
        else:
            status, exit_code = "ok", EXIT_OK
        if sinks.output is not None:
            if exit_code == EXIT_OK:
                sinks.output.commit()
            else:
                sinks.output.abort()
            sinks.output = None
        if sinks.rejects is not None:
            sinks.rejects.commit()
            sinks.rejects = None
    except BaseException:
        sinks.abort()
        raise
    assert plan is not None and stats is not None
    for name, count in changed.items():
        ledger.columns.setdefault(name, ColumnTally()).changed_cells = count
    blocks = explain_blocks(ledger, plans, profile, info, columns) if options.explain else []
    for line in blocks:
        log.info(line)
    duration = None if clock is None else round(time.perf_counter() - clock, 3)
    report = build_report(
        ReportInput(
            profile=profile,
            profile_name=profile_name,
            profile_sha256=profile_sha256,
            plans=plans,
            info=info,
            header_plan=plan,
            ledger=ledger,
            stats=stats.result({name: t.filled for name, t in ledger.columns.items()}),
            changed=changed,
            columns=columns,
            data_columns=data,
            dry_run=options.dry_run,
            strict=options.strict,
            chunked=options.chunked,
            chunk_size=options.chunk_size if options.chunked else None,
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            duration=duration,
            rejects_file=None if options.dry_run or options.rejects is None else options.rejects.name,
            explain=explain + blocks if options.explain else None,
        )
    )
    write_reports(report, options.reports)
    summary = (
        f"read {ledger.read} · empty {ledger.empty} · rejected {ledger.rejected} · "
        f"duplicates {ledger.duplicates} · out {ledger.out} · exit {exit_code}"
    )
    log_summary(ledger, info, columns, options, exit_code)
    output_frame = concat(kept, columns) if keep_frames else None
    raw_frame = concat(raws, data) if keep_frames else None
    return RunResult(exit_code, status, ledger, report, summary, explain + blocks, output_frame, raw_frame)


def log_summary(
    ledger: Ledger, info: SourceInfo, columns: list[str], options: RunOptions, exit_code: int
) -> None:
    exact = ledger.rules.get("dedupe.exact")
    fuzzy = ledger.rules.get("dedupe.fuzzy")
    keys = sum(rule.failed for rule in ledger.rules.values() if rule.kind == "key")
    filled = sum(tally.filled for tally in ledger.columns.values())
    changed = sum(tally.text_changed for tally in ledger.columns.values())
    invalid = sum(tally.invalid for tally in ledger.columns.values())
    nulled = sum(tally.nulled for tally in ledger.columns.values())
    log.info("read: %d records from %s", ledger.read, info.name)
    log.info("structure: %d empty records dropped, %d output columns", ledger.empty, len(columns))
    log.info("normalise: %d cells changed", changed)
    log.info("types: %d invalid cells, %d set to null", invalid, nulled)
    log.info("validate: %d rows rejected", ledger.by_stage["validate"])
    log.info(
        "dedupe: %d rows removed (%d exact, %d by key, %d fuzzy)",
        ledger.duplicates,
        exact.failed if exact else 0,
        keys,
        fuzzy.failed if fuzzy else 0,
    )
    log.info("missing: %d cells filled, %d rows dropped", filled, ledger.by_stage["missing"])
    for notice in ledger.warnings():
        log.warning("%s: %s (%d)", notice.code, notice.message, notice.count)
    if options.dry_run:
        target = "not written (dry run, reports only)"
    elif exit_code:
        target = "not written"
    else:
        target = f"written to {options.out.name if options.out else '-'}"
    log.info("write: %d rows %s", ledger.out, target)
