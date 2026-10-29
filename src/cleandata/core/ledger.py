"""Stage events, row accounting and the rejects collector.

Each stage returns an ``Events`` object; the pipeline hands every one of them to ``Ledger.absorb``,
chunk after chunk, so in-memory and chunked runs add up the same counts and keep the same first
examples.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from cleandata.errors import InternalError

Level = Literal["warning", "info"]


@dataclass
class Reject:
    """One removed row. ``raw`` maps output column names to the values as read."""

    ordinal: int
    source_row: int
    source_sheet: str | None
    stage: str
    rules: list[str]
    detail: str
    raw: dict[str, object] = field(default_factory=dict)

    @property
    def rule(self) -> str:
        return self.rules[0]


@dataclass
class Notice:
    code: str
    message: str
    count: int = 1
    level: Level = "warning"
    key: str = ""


@dataclass
class RuleTally:
    id: str
    kind: str
    column: str | None
    parameters: dict[str, object]
    checked: int = 0
    failed: int = 0
    examples: list[str] = field(default_factory=list)

    def merge(self, other: RuleTally, limit: int) -> None:
        self.checked += other.checked
        self.failed += other.failed
        _extend(self.examples, other.examples, limit)


@dataclass
class ColumnTally:
    text_changed: int = 0
    text_nulled: int = 0
    text_examples: list[str] = field(default_factory=list)
    parsed: int = 0
    null: int = 0
    invalid: int = 0
    nulled: int = 0
    patterns: Counter[str] = field(default_factory=Counter)
    type_examples: list[str] = field(default_factory=list)
    invalid_examples: list[str] = field(default_factory=list)
    filled: int = 0
    fill_value: object = None
    changed_cells: int = 0

    def merge(self, other: ColumnTally, limit: int) -> None:
        self.text_changed += other.text_changed
        self.text_nulled += other.text_nulled
        _extend(self.text_examples, other.text_examples, limit)
        self.parsed += other.parsed
        self.null += other.null
        self.invalid += other.invalid
        self.nulled += other.nulled
        self.patterns.update(other.patterns)
        _extend(self.type_examples, other.type_examples, limit)
        _extend(self.invalid_examples, other.invalid_examples, limit)
        self.filled += other.filled
        if self.fill_value is None:
            self.fill_value = other.fill_value
        self.changed_cells += other.changed_cells


@dataclass
class ClusterMember:
    label: str
    source_row: int
    source_sheet: str | None


@dataclass
class Cluster:
    id: str
    members: list[ClusterMember]
    pairs: list[tuple[str, str, float]]


@dataclass
class Events:
    stage: str
    rejects: list[Reject] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    rules: list[RuleTally] = field(default_factory=list)
    columns: dict[str, ColumnTally] = field(default_factory=dict)
    clusters: list[Cluster] = field(default_factory=list)

    def column(self, name: str) -> ColumnTally:
        return self.columns.setdefault(name, ColumnTally())


def _extend(target: list[str], items: list[str], limit: int) -> None:
    room = limit - len(target)
    if room > 0:
        target.extend(items[:room])


REMOVING_STAGES = ("read", "validate", "dedupe", "missing")
COLUMN_RULE_KINDS = ("type", "required", "min", "max", "regex", "allowed", "missing")


def rule_column(rule: str) -> str | None:
    """The column of a rule id such as ``age.max``; None for ``read.*`` and ``dedupe.*`` ids."""
    head, _, kind = rule.rpartition(".")
    if head and kind in COLUMN_RULE_KINDS and not head.startswith("dedupe."):
        return head
    return None


class Ledger:
    """Row accounting, rule and column tallies, notices and a preview of the rejects."""

    def __init__(self, limit: int, preview_rows: int) -> None:
        self.limit = limit
        self.preview_rows = preview_rows
        self.counts: Counter[str] = Counter()
        self.notices: dict[str, Notice] = {}
        self.rules: dict[str, RuleTally] = {}
        self.columns: dict[str, ColumnTally] = {}
        self.clusters: list[Cluster] = []
        self.by_stage: Counter[str] = Counter()
        self.by_rule: Counter[str] = Counter()
        self.by_column: Counter[str] = Counter()
        self.preview: list[Reject] = []

    def notice(
        self, code: str, message: str, count: int = 1, level: Level = "warning", key: str = ""
    ) -> None:
        """Notices merge by code; a ``key`` (such as a column name) keeps one notice per key."""
        slot = f"{code}:{key}" if key else code
        existing = self.notices.get(slot)
        if existing is None:
            self.notices[slot] = Notice(code, message, count, level, key)
        else:
            existing.count += count

    def absorb(self, events: Events) -> None:
        self.counts.update(events.counts)
        for notice in events.notices:
            self.notice(notice.code, notice.message, notice.count, notice.level, notice.key)
        for tally in events.rules:
            known = self.rules.get(tally.id)
            if known is None:
                self.rules[tally.id] = RuleTally(
                    tally.id, tally.kind, tally.column, tally.parameters, examples=[]
                )
                known = self.rules[tally.id]
            known.merge(tally, self.limit)
        for name, column_tally in events.columns.items():
            self.columns.setdefault(name, ColumnTally()).merge(column_tally, self.limit)
        self.clusters.extend(events.clusters)
        for reject in sorted(events.rejects, key=lambda item: item.ordinal):
            self.counts["duplicates" if reject.stage == "dedupe" else "rejected"] += 1
            self.by_stage[reject.stage] += 1
            self.by_rule[reject.rule] += 1
            for name in sorted({column for column in map(rule_column, reject.rules) if column}):
                self.by_column[name] += 1
        self.preview = sorted(self.preview + events.rejects, key=lambda item: item.ordinal)[
            : self.preview_rows
        ]

    @property
    def read(self) -> int:
        return self.counts["read"]

    @property
    def empty(self) -> int:
        return self.counts["empty"]

    @property
    def rejected(self) -> int:
        return self.counts["rejected"]

    @property
    def duplicates(self) -> int:
        return self.counts["duplicates"]

    @property
    def out(self) -> int:
        return self.counts["out"]

    def check_accounting(self) -> None:
        """read = out + rejected + duplicates + empty, or the run is a bug."""
        total = self.out + self.rejected + self.duplicates + self.empty
        if total != self.read:
            raise InternalError(
                f"row accounting does not add up: read {self.read} != out {self.out} + rejected "
                f"{self.rejected} + duplicates {self.duplicates} + empty {self.empty}"
            )

    def warnings(self) -> list[Notice]:
        return [notice for notice in self.notices.values() if notice.level == "warning"]

    def reject_rate(self) -> float:
        base = self.read - self.empty
        return self.rejected / base if base else 0.0
