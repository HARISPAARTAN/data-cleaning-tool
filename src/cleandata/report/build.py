"""The JSON report: fixed key order, ``schema_version: 1``, floats rounded to 6 significant digits.

The HTML report is rendered from this same dictionary, so the two never disagree. With
``SOURCE_DATE_EPOCH`` set, ``run.started_at`` is that instant and ``run.duration_s`` is null, so a
report reproduces byte for byte.
"""

from __future__ import annotations

import importlib.metadata
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from cleandata import DISTRIBUTION, __version__
from cleandata.core.coerce import canonical_str, json_value
from cleandata.core.text import redact_value
from cleandata.core.writer import write_text

if TYPE_CHECKING:
    from cleandata.core.headers import HeaderPlan
    from cleandata.core.ledger import Ledger, Reject
    from cleandata.core.profile import ColumnPlan, Profile
    from cleandata.core.reader import SourceInfo

SCHEMA_VERSION: Final = 1
PACKAGES: Final = (
    "pandas",
    "pydantic",
    "click",
    "openpyxl",
    "charset-normalizer",
    "PyYAML",
    "Jinja2",
    "rapidfuzz",
    "pyarrow",
)
STAGES: Final = ("read", "validate", "dedupe", "missing")


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def tool_info() -> dict[str, Any]:
    return {
        "name": "clean-data",
        "distribution": DISTRIBUTION,
        "version": __version__,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "packages": {name: package_version(name) for name in PACKAGES},
    }


def round_floats(value: Any) -> Any:
    """Round every float to 6 significant digits; NaN and infinities become null."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return float(f"{value:.6g}")
    if isinstance(value, dict):
        return {key: round_floats(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [round_floats(item) for item in value]
    return value


def to_json(report: Mapping[str, Any]) -> str:
    return json.dumps(round_floats(dict(report)), ensure_ascii=False, indent=2) + "\n"


def input_section(info: SourceInfo) -> dict[str, Any]:
    workbook = info.kind == "xlsx"
    return {
        "name": info.name,
        "size": info.size,
        "sha256": info.sha256,
        "kind": info.kind,
        "encoding": None
        if workbook
        else {"name": info.encoding, "method": info.encoding_method, "confidence": info.encoding_confidence},
        "delimiter": None if workbook else {"value": info.delimiter, "share": info.delimiter_share},
        "header_row": info.header_row,
        "sheets": [{"name": s.name, "header_row": s.header_row, "records": s.records} for s in info.sheets],
        "ignored_sheets": list(info.ignored_sheets),
        "records": info.records,
        "footer_skipped": info.footer_skipped,
        "short_records": info.short_records,
        "malformed": info.malformed,
    }


def reject_preview(reject: Reject, data: Sequence[str], redacted: set[str], workbook: bool) -> dict[str, Any]:
    values: dict[str, str] = {}
    for name in data:
        text = canonical_str(reject.raw.get(name))
        values[name] = redact_value(text) if name in redacted and text else text
    entry: dict[str, Any] = {"source_row": reject.source_row}
    if workbook:
        entry["source_sheet"] = reject.source_sheet
    entry.update({"stage": reject.stage, "rule": reject.rule, "detail": reject.detail, "values": values})
    return entry


@dataclass(frozen=True)
class ReportInput:
    profile: Profile
    profile_name: str
    profile_sha256: str
    plans: Mapping[str, ColumnPlan]
    info: SourceInfo
    header_plan: HeaderPlan
    ledger: Ledger
    stats: Mapping[str, Mapping[str, Any]]
    changed: Mapping[str, int]
    columns: list[str]
    data_columns: list[str]
    dry_run: bool
    strict: bool
    chunked: bool
    chunk_size: int | None
    status: str
    exit_code: int
    started_at: str
    duration: float | None
    rejects_file: str | None
    explain: list[str] | None


def _columns(data: ReportInput) -> dict[str, Any]:
    ledger = data.ledger
    columns: dict[str, Any] = {}
    for name in data.columns:
        plan = data.plans.get(name)
        tally = ledger.columns.get(name)
        stats = data.stats.get(name, {})
        if plan is not None:
            source = "profile"
        elif name in data.data_columns:
            source = "unlisted"
        else:
            source = "cluster"
        entry: dict[str, Any] = {
            "type": plan.type if plan is not None else "string",
            "source": source,
            "before": stats.get("before"),
            "after": stats.get("after"),
            "coercion": None,
            "missing": None,
            "changed_cells": data.changed.get(name, 0),
        }
        if plan is not None and tally is not None:
            entry["coercion"] = {
                "parsed": tally.parsed,
                "null": tally.null,
                "invalid": tally.invalid,
                "nulled": tally.nulled,
                "patterns": dict(sorted(tally.patterns.items())),
            }
        if plan is not None and plan.missing != "keep":
            entry["missing"] = {
                "strategy": plan.missing,
                "value": None if tally is None else json_value(tally.fill_value),
                "filled": 0 if tally is None else tally.filled,
            }
        columns[name] = entry
    return columns


def _dedupe(data: ReportInput) -> dict[str, Any]:
    profile, ledger = data.profile, data.ledger
    exact = ledger.rules.get("dedupe.exact")
    keys = []
    for number, rule in enumerate(profile.dedupe.keys, start=1):
        tally = ledger.rules.get(f"dedupe.key.{number}")
        if tally is not None:
            keys.append(
                {
                    "id": tally.id,
                    "columns": list(rule.columns),
                    "keep": rule.keep,
                    "order_by": rule.order_by,
                    "checked": tally.checked,
                    "removed": tally.failed,
                }
            )
    result: dict[str, Any] = {
        "exact": None if exact is None else {"checked": exact.checked, "removed": exact.failed},
        "keys": keys,
        "fuzzy": None,
    }
    fuzzy = profile.dedupe.fuzzy
    if fuzzy is not None:
        tally = ledger.rules.get("dedupe.fuzzy")
        skipped = ledger.notices.get("dedupe.fuzzy_block_skipped")
        result["fuzzy"] = {
            "threshold": fuzzy.threshold,
            "action": fuzzy.action,
            "block_on": list(fuzzy.block_on),
            "cluster_column": fuzzy.cluster_column if fuzzy.action == "flag" else None,
            "blocks": ledger.counts["fuzzy.blocks"],
            "blocks_compared": ledger.counts["fuzzy.blocks_compared"],
            "skipped_blocks": 0 if skipped is None else skipped.count,
            "clustered_rows": ledger.counts["fuzzy.clustered_rows"],
            "removed": 0 if tally is None else tally.failed,
            "clusters": [
                {
                    "id": cluster.id,
                    "members": [
                        {"label": m.label, "source_row": m.source_row, "source_sheet": m.source_sheet}
                        for m in cluster.members
                    ],
                    "scores": [{"a": a, "b": b, "score": score} for a, b, score in cluster.pairs],
                }
                for cluster in ledger.clusters
            ],
        }
    return result


def build_report(data: ReportInput) -> dict[str, Any]:
    profile, ledger = data.profile, data.ledger
    redacted = set(profile.report.redact)
    workbook = data.info.kind == "xlsx"
    notices = list(ledger.notices.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "clean",
        "title": ("Dry run: " if data.dry_run else "") + (profile.report.title or profile.name),
        "tool": tool_info(),
        "run": {
            "started_at": data.started_at,
            "duration_s": data.duration,
            "mode": "chunked" if data.chunked else "in_memory",
            "chunk_size": data.chunk_size,
            "dry_run": data.dry_run,
            "strict": data.strict,
            "status": data.status,
            "exit_code": data.exit_code,
        },
        "input": input_section(data.info),
        "profile": {"name": profile.name, "file": data.profile_name, "sha256": data.profile_sha256},
        "headers": [
            {
                "position": None if item.position is None else item.position + 1,
                "raw": item.raw,
                "normalised": item.normalised,
                "final": item.final,
                "action": item.action,
            }
            for item in data.header_plan.mappings
        ],
        "rows": {
            "read": ledger.read,
            "empty": ledger.empty,
            "rejected": ledger.rejected,
            "duplicates": ledger.duplicates,
            "out": ledger.out,
            "reject_rate": ledger.reject_rate(),
            "max_reject_rate": profile.quality.max_reject_rate,
            "by_rule": dict(sorted(ledger.by_rule.items())),
        },
        "columns": _columns(data),
        "rules": [
            {
                "id": rule.id,
                "kind": rule.kind,
                "column": rule.column,
                "parameters": rule.parameters,
                "checked": rule.checked,
                "failed": rule.failed,
                "examples": rule.examples,
            }
            for rule in ledger.rules.values()
        ],
        "dedupe": _dedupe(data),
        "rejects": {
            "file": data.rejects_file,
            "rows": ledger.rejected + ledger.duplicates,
            "by_stage": {stage: ledger.by_stage[stage] for stage in STAGES},
            "by_rule": dict(sorted(ledger.by_rule.items())),
            "by_column": dict(sorted(ledger.by_column.items())),
            "preview": [
                reject_preview(item, data.data_columns, redacted, workbook) for item in ledger.preview
            ],
        },
        "warnings": [
            {"code": n.code, "message": n.message, "count": n.count} for n in notices if n.level == "warning"
        ],
        "notes": [
            {"code": n.code, "message": n.message, "count": n.count} for n in notices if n.level == "info"
        ],
        "explain": data.explain,
        "banner": profile.report.banner,
        "footer": profile.report.footer,
    }


def write_reports(report: Mapping[str, Any], targets: Sequence[Path]) -> None:
    """Write the JSON and/or HTML report; both come from the same rounded dictionary."""
    from cleandata.report.html import render_html

    rounded: dict[str, Any] = round_floats(dict(report))
    for target in targets:
        if target.suffix.lower() == ".json":
            write_text(target, json.dumps(rounded, ensure_ascii=False, indent=2) + "\n")
        else:
            write_text(target, render_html(rounded))
