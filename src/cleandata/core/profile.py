"""The profile: pydantic models of the YAML rules file, its cross-checks and the per-column plans.

Every model forbids extra keys, so a typo fails with its YAML path
(``columns.age.mx: Extra inputs are not permitted``). PyYAML follows YAML 1.1: ``yes``, ``no``,
``on``, ``off``, ``y`` and ``n`` must be quoted wherever they appear as keys or values.
"""

from __future__ import annotations

import codecs
import datetime as dt
import re
import warnings
from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Self, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from cleandata.core.coerce import ParseSettings, coerce_literal
from cleandata.core.text import TextPlan, TextSettings, fold_key
from cleandata.errors import ProfileError, UsageError

ColumnType = Literal["string", "integer", "number", "boolean", "date", "datetime"]
UnicodeForm = Literal["NFC", "NFKC", "none"]
CaseMode = Literal["keep", "lower", "upper", "title"]
Strategy = Literal["keep", "drop", "constant", "median", "ffill"]
Scorer = Literal["ratio", "token_sort_ratio", "token_set_ratio"]

DELIMITERS = {",": ",", ";": ";", "\t": "\t", "tab": "\t", "\\t": "\t", "|": "|"}
SNAKE = re.compile(r"[a-z][a-z0-9_]*")
DIRECTIVE = re.compile(r"%[a-zA-Z]")
DATE_SAMPLE = dt.datetime(2001, 2, 3)
DATETIME_SAMPLE = dt.datetime(2001, 2, 3, 4, 5)
ORDERED_TYPES = ("integer", "number", "date", "datetime")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Locale(_Model):
    decimal: Literal[".", ","] = "."
    thousands: Literal[",", ".", " ", "'"] | None = None
    date_formats: list[str] = Field(default_factory=lambda: ["%Y-%m-%d"], min_length=1)
    datetime_formats: list[str] = Field(
        default_factory=lambda: ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"], min_length=1
    )
    true_values: list[str] = Field(default_factory=lambda: ["true", "yes", "y", "1"], min_length=1)
    false_values: list[str] = Field(default_factory=lambda: ["false", "no", "n", "0"], min_length=1)

    @model_validator(mode="after")
    def _separators_differ(self) -> Self:
        if self.thousands == self.decimal:
            raise ValueError("decimal and thousands separators must differ")
        return self

    def settings(self) -> ParseSettings:
        return ParseSettings(
            decimal=self.decimal,
            thousands=self.thousands,
            date_formats=tuple(self.date_formats),
            datetime_formats=tuple(self.datetime_formats),
            true_values=frozenset(value.strip().casefold() for value in self.true_values),
            false_values=frozenset(value.strip().casefold() for value in self.false_values),
        )


class LocaleOverride(_Model):
    """A partial ``Locale`` for one column; keys left out inherit ``input.locale``."""

    decimal: Literal[".", ","] | None = None
    thousands: Literal[",", ".", " ", "'"] | None = None
    date_formats: list[str] | None = Field(default=None, min_length=1)
    datetime_formats: list[str] | None = Field(default=None, min_length=1)
    true_values: list[str] | None = Field(default=None, min_length=1)
    false_values: list[str] | None = Field(default=None, min_length=1)

    def resolve(self, base: Locale) -> Locale:
        merged = base.model_dump()
        merged.update({name: getattr(self, name) for name in self.model_fields_set})
        return Locale.model_validate(merged)


class InputOptions(_Model):
    encoding: str = "auto"
    delimiter: str = "auto"
    quotechar: str = Field(default='"', min_length=1, max_length=1)
    header_row: Annotated[int, Field(ge=1)] | Literal["auto"] = 1
    skip_footer_rows: int = Field(default=0, ge=0)
    sheets: list[str] | None = Field(default=None, min_length=1)
    sheet_column: str | None = None
    drop_empty_rows: bool = True
    drop_empty_columns: bool = False
    null_tokens: list[str] = Field(default_factory=lambda: [""])
    locale: Locale = Field(default_factory=Locale)

    @field_validator("encoding")
    @classmethod
    def _known_encoding(cls, value: str) -> str:
        if value != "auto":
            try:
                codecs.lookup(value)
            except LookupError as exc:
                raise ValueError(f"unknown encoding {value!r}") from exc
        return value

    @field_validator("delimiter", mode="before")
    @classmethod
    def _known_delimiter(cls, value: object) -> object:
        if value == "auto":
            return value
        if isinstance(value, str) and value in DELIMITERS:
            return DELIMITERS[value]
        raise ValueError('delimiter must be "auto", ",", ";", "\\t" (or "tab") or "|"')


class HeaderOptions(_Model):
    normalise: Literal["snake_case", "keep"] = "snake_case"
    rename: dict[str, str] = Field(default_factory=dict)
    drop: list[str] = Field(default_factory=list)


class TextOptions(_Model):
    unicode: UnicodeForm = "NFC"
    trim: bool = True
    collapse_whitespace: bool = True
    remove_control: bool = True
    case: CaseMode = "keep"


class MissingRule(_Model):
    strategy: Strategy = "keep"
    value: Any = None


def _missing_schema(schema: dict[str, Any]) -> None:
    """``missing`` also takes the bare strategy name (``missing: median``); the schema says so."""
    schema["anyOf"] = [{"type": "string", "enum": list(get_args(Strategy))}, {"$ref": schema.pop("$ref")}]


class ColumnRule(_Model):
    type: ColumnType
    optional: bool = False
    null_tokens: list[str] = Field(default_factory=list)
    unicode: UnicodeForm | None = None
    trim: bool | None = None
    collapse_whitespace: bool | None = None
    remove_control: bool | None = None
    case: CaseMode | None = None
    map: dict[str, str] = Field(default_factory=dict)
    strip: list[str] = Field(default_factory=list)
    locale: LocaleOverride | None = None
    on_invalid: Literal["reject", "null"] = "reject"
    required: bool = False
    min: Any = None
    max: Any = None
    regex: str | None = None
    allowed: list[Any] | None = Field(default=None, min_length=1)
    missing: MissingRule = Field(default_factory=MissingRule, json_schema_extra=_missing_schema)

    @field_validator("missing", mode="before")
    @classmethod
    def _strategy_name(cls, value: object) -> object:
        return {"strategy": value} if isinstance(value, str) else value


class KeyRule(_Model):
    columns: list[str] = Field(min_length=1)
    keep: Literal["first", "last"] = "first"
    order_by: str | None = None


class FuzzyField(_Model):
    columns: list[str] = Field(min_length=1)
    scorer: Scorer = "token_sort_ratio"
    weight: float = Field(default=1.0, gt=0)


class FuzzyRule(_Model):
    block_on: list[str] = Field(min_length=1)
    fields: list[FuzzyField] = Field(min_length=1)
    threshold: float = Field(default=90, ge=0, le=100)
    action: Literal["flag", "remove"] = "flag"
    cluster_column: str = "dup_cluster"
    max_block_size: int = Field(default=2000, ge=2)


class DedupeOptions(_Model):
    exact: bool = True
    keys: list[KeyRule] = Field(default_factory=list)
    fuzzy: FuzzyRule | None = None


class QualityGate(_Model):
    max_reject_rate: float | None = Field(default=None, ge=0, le=1)


class CsvOutput(_Model):
    delimiter: str = ","
    encoding: str = "utf-8"
    bom: bool = False

    @field_validator("delimiter", mode="before")
    @classmethod
    def _one_delimiter(cls, value: object) -> object:
        if isinstance(value, str) and value in DELIMITERS:
            return DELIMITERS[value]
        raise ValueError('delimiter must be ",", ";", "\\t" (or "tab") or "|"')

    @field_validator("encoding")
    @classmethod
    def _known_encoding(cls, value: str) -> str:
        try:
            codecs.lookup(value)
        except LookupError as exc:
            raise ValueError(f"unknown encoding {value!r}") from exc
        return value


class XlsxOutput(_Model):
    sheet_name: str = Field(default="cleaned", min_length=1, max_length=31, pattern=r"^[^\[\]:*?/\\]+$")


class OutputOptions(_Model):
    sort_by: list[str] = Field(default_factory=list)
    csv: CsvOutput = Field(default_factory=CsvOutput)
    xlsx: XlsxOutput = Field(default_factory=XlsxOutput)


class ReportOptions(_Model):
    title: str | None = None
    examples_per_rule: int = Field(default=5, ge=0, le=50)
    preview_rows: int = Field(default=20, ge=0)
    redact: list[str] = Field(default_factory=list)
    banner: str | None = None
    footer: str | None = None


@dataclass(frozen=True)
class ColumnPlan:
    """Everything the stages need for one profile column, with defaults and overrides resolved."""

    name: str
    type: str
    optional: bool
    text: TextPlan
    settings: ParseSettings
    strip: tuple[str, ...]
    on_invalid: str
    required: bool
    min: object
    max: object
    regex: re.Pattern[str] | None
    allowed: tuple[object, ...] | None
    missing: str
    fill: object
    redact: bool


def _pattern_problem(pattern: str, kind: str) -> str | None:
    if not DIRECTIVE.search(pattern):
        return "contains no % directive"
    sample = DATETIME_SAMPLE if kind == "datetime" else DATE_SAMPLE
    try:
        text = sample.strftime(pattern)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # Python 3.13 warns on a missing year
            parsed = dt.datetime.strptime(text, pattern)
    except ValueError as exc:
        return f"does not round-trip a sample date ({exc})"
    if parsed != sample:
        return f"does not round-trip a sample date ({text!r} reads back as {parsed.isoformat()})"
    return None


class Profile(_Model):
    version: Literal[1]
    name: str = Field(min_length=1)
    description: str = ""
    input: InputOptions = Field(default_factory=InputOptions)
    headers: HeaderOptions = Field(default_factory=HeaderOptions)
    text: TextOptions = Field(default_factory=TextOptions)
    columns: dict[str, ColumnRule] = Field(min_length=1)
    keep_unlisted: bool = False
    dedupe: DedupeOptions = Field(default_factory=DedupeOptions)
    quality: QualityGate = Field(default_factory=QualityGate)
    output: OutputOptions = Field(default_factory=OutputOptions)
    report: ReportOptions = Field(default_factory=ReportOptions)

    @model_validator(mode="after")
    def _cross_checks(self) -> Self:
        problems: list[str] = []
        sheet = self.input.sheet_column
        known = set(self.columns) | ({sheet} if sheet else set())

        def need(path: str, name: str) -> None:
            if name not in known:
                problems.append(f"{path}: column {name!r} is not in columns")

        for name in list(self.columns) + ([sheet] if sheet else []):
            if name.startswith("_") or not name.strip():
                problems.append(f"columns.{name}: column names must not be empty or start with '_'")
        for index, key_rule in enumerate(self.dedupe.keys):
            for name in key_rule.columns:
                need(f"dedupe.keys.{index}.columns", name)
            if key_rule.order_by is not None:
                need(f"dedupe.keys.{index}.order_by", key_rule.order_by)
        fuzzy = self.dedupe.fuzzy
        if fuzzy is not None:
            for name in fuzzy.block_on:
                need("dedupe.fuzzy.block_on", name)
            for index, fuzzy_field in enumerate(fuzzy.fields):
                for name in fuzzy_field.columns:
                    need(f"dedupe.fuzzy.fields.{index}.columns", name)
            if fuzzy.action == "flag" and fuzzy.cluster_column in known:
                problems.append(f"dedupe.fuzzy.cluster_column: {fuzzy.cluster_column!r} is already a column")
        for name in self.output.sort_by:
            need("output.sort_by", name)
        for name in self.report.redact:
            need("report.redact", name)
        targets = list(self.headers.rename.values())
        for source, target in self.headers.rename.items():
            if not SNAKE.fullmatch(target):
                problems.append(f"headers.rename.{source}: {target!r} is not a snake_case name")
        for target in sorted({t for t in targets if targets.count(t) > 1}):
            problems.append(f"headers.rename: {target!r} is the target of more than one rename")
        problems.extend(self._locale_problems("input.locale", self.input.locale))
        for name, rule in self.columns.items():
            problems.extend(self._column_problems(name, rule))
        if problems:
            raise ValueError("\n".join(problems))
        return self

    @staticmethod
    def _locale_problems(path: str, locale: Locale) -> list[str]:
        problems: list[str] = []
        for pattern in locale.date_formats:
            problem = _pattern_problem(pattern, "date")
            if problem:
                problems.append(f"{path}.date_formats: {pattern!r} {problem}")
        for pattern in locale.datetime_formats:
            problem = _pattern_problem(pattern, "datetime")
            if problem:
                problems.append(f"{path}.datetime_formats: {pattern!r} {problem}")
        true = {value.strip().casefold() for value in locale.true_values}
        false = {value.strip().casefold() for value in locale.false_values}
        if true & false:
            problems.append(f"{path}: {sorted(true & false)} listed as both true and false")
        return problems

    def _column_problems(self, name: str, rule: ColumnRule) -> list[str]:
        path = f"columns.{name}"
        problems: list[str] = []
        if rule.locale is not None:
            try:
                resolved = rule.locale.resolve(self.input.locale)
            except ValidationError as exc:
                problems.append(f"{path}.locale: {str(exc.errors()[0]['msg']).removeprefix('Value error, ')}")
            else:
                problems.extend(self._locale_problems(f"{path}.locale", resolved))
        bounds: dict[str, Any] = {}
        for key in ("min", "max"):
            value = getattr(rule, key)
            if value is None:
                continue
            if rule.type not in ORDERED_TYPES:
                problems.append(f"{path}.{key}: only integer, number, date and datetime columns have bounds")
                continue
            try:
                bounds[key] = coerce_literal(value, rule.type)
            except ValueError as exc:
                problems.append(f"{path}.{key}: {exc}")
                continue
            if rule.type == "datetime" and bounds[key].tzinfo is not None:
                problems.append(
                    f"{path}.{key}: must be a naive datetime (no timezone); "
                    "cells are compared without timezone"
                )
        if len(bounds) == 2:
            try:
                if bounds["max"] < bounds["min"]:
                    problems.append(f"{path}.max: must not be below min")
            except TypeError as exc:  # a naive and a timezone-aware datetime
                problems.append(f"{path}.max: cannot be compared with min ({exc})")
        for index, value in enumerate(rule.allowed or []):
            try:
                coerce_literal(value, rule.type)
            except ValueError as exc:
                problems.append(f"{path}.allowed.{index}: {exc}")
        if rule.regex is not None:
            try:
                re.compile(rule.regex)
            except re.error as exc:
                problems.append(f"{path}.regex: does not compile ({exc})")
        strategy = rule.missing.strategy
        if strategy == "median" and rule.type not in ("integer", "number"):
            problems.append(f"{path}.missing: median needs an integer or number column")
        if strategy == "constant":
            try:
                coerce_literal(rule.missing.value, rule.type)
            except ValueError as exc:
                problems.append(f"{path}.missing.value: {exc}")
        elif rule.missing.value is not None:
            problems.append(f"{path}.missing.value: only the constant strategy takes a value")
        folded = {fold_key(key): target for key, target in rule.map.items()}
        for key, target in rule.map.items():
            again = folded.get(fold_key(target))
            if again is not None and again != target:
                problems.append(
                    f"{path}.map.{key}: target {target!r} is itself a key with the target {again!r}; "
                    "a map must be idempotent"
                )
        return problems

    def effective_locale(self, name: str) -> Locale:
        rule = self.columns[name]
        return rule.locale.resolve(self.input.locale) if rule.locale is not None else self.input.locale

    def default_text_plan(self) -> TextPlan:
        return TextPlan(
            settings=TextSettings(**self.text.model_dump()),
            null_tokens=frozenset({"", *(token.strip().casefold() for token in self.input.null_tokens)}),
        )

    def column_plans(self) -> dict[str, ColumnPlan]:
        plans: dict[str, ColumnPlan] = {}
        base = self.text.model_dump()
        for name, rule in self.columns.items():
            text = dict(base)
            for key in ("unicode", "trim", "collapse_whitespace", "remove_control", "case"):
                if getattr(rule, key) is not None:
                    text[key] = getattr(rule, key)
            tokens = {"", *self.input.null_tokens, *rule.null_tokens}
            redact = name in self.report.redact
            strategy = rule.missing.strategy
            plans[name] = ColumnPlan(
                name=name,
                type=rule.type,
                optional=rule.optional,
                text=TextPlan(
                    settings=TextSettings(**text),
                    null_tokens=frozenset(token.strip().casefold() for token in tokens),
                    mapping={fold_key(key): target for key, target in rule.map.items()},
                    redact=redact,
                ),
                settings=self.effective_locale(name).settings(),
                strip=tuple(rule.strip),
                on_invalid=rule.on_invalid,
                required=rule.required,
                min=None if rule.min is None else coerce_literal(rule.min, rule.type),
                max=None if rule.max is None else coerce_literal(rule.max, rule.type),
                regex=None if rule.regex is None else re.compile(rule.regex),
                allowed=None
                if rule.allowed is None
                else tuple(coerce_literal(value, rule.type) for value in rule.allowed),
                missing=strategy,
                fill=coerce_literal(rule.missing.value, rule.type) if strategy == "constant" else None,
                redact=redact,
            )
        return plans

    def canonical(self) -> Profile:
        """The profile that reads this profile's own canonical CSV output back unchanged.

        Text options, null tokens, maps, strips and locales become the canonical ones, headers are kept as
        written, empty rows and columns are kept, fuzzy matching is left out (its flags are already in the
        cluster column) and the sheet and cluster columns become string columns. A profile that both
        keeps unlisted columns and adds a sheet column gets its sheet column before the unlisted ones.
        """
        data = self.model_dump()
        csv = self.output.csv
        utf8 = codecs.lookup(csv.encoding).name == "utf-8"
        data["input"] = {
            "encoding": "utf-8-sig" if csv.bom and utf8 else csv.encoding,
            "delimiter": csv.delimiter,
            "quotechar": '"',
            "header_row": 1,
            "skip_footer_rows": 0,
            "sheets": None,
            "sheet_column": None,
            "drop_empty_rows": False,
            "drop_empty_columns": False,
            "null_tokens": [""],
            "locale": {
                "decimal": ".",
                "thousands": None,
                "date_formats": ["%Y-%m-%d"],
                "datetime_formats": ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"],
                "true_values": ["true"],
                "false_values": ["false"],
            },
        }
        data["headers"] = {"normalise": "keep", "rename": {}, "drop": []}
        data["text"] = {
            "unicode": "none",
            "trim": False,
            "collapse_whitespace": False,
            "remove_control": False,
            "case": "keep",
        }
        columns: dict[str, Any] = {}
        for name, rule in data["columns"].items():
            rule.update(
                null_tokens=[],
                unicode=None,
                trim=None,
                collapse_whitespace=None,
                remove_control=None,
                case=None,
                map={},
                strip=[],
                locale=None,
            )
            columns[name] = rule
        sheet = self.input.sheet_column
        if sheet is not None and sheet not in columns:
            columns[sheet] = {"type": "string"}
        fuzzy = self.dedupe.fuzzy
        if fuzzy is not None and fuzzy.action == "flag":
            columns[fuzzy.cluster_column] = {"type": "string", "optional": True}
        data["columns"] = columns
        data["dedupe"]["fuzzy"] = None
        return Profile.model_validate(data)


def format_validation_error(exc: ValidationError, source: str) -> str:
    lines = [f"{source} is not a valid profile:"]
    for error in exc.errors():
        path = ".".join(str(part) for part in error["loc"])
        message = str(error["msg"]).removeprefix("Value error, ")
        for line in message.splitlines():
            lines.append(f"  {path}: {line}" if path else f"  {line}")
    return "\n".join(lines)


class _UniqueKeyLoader(yaml.SafeLoader):
    """``yaml.safe_load`` that refuses a repeated mapping key (PyYAML silently keeps the last one).

    Every mapping passes through ``flatten_mapping`` before it is built, and the check runs there, on
    the keys as written, once per mapping: after a ``<<`` merge the explicit keys that override merged
    ones would look like repeats. Merge keys and anchors otherwise behave as in ``safe_load``.
    """

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._checked: set[int] = set()

    def flatten_mapping(self, node: yaml.MappingNode) -> None:
        if id(node) not in self._checked:
            self._checked.add(id(node))
            seen: set[Hashable] = set()
            for key_node, _ in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    continue
                key = self.construct_object(key_node, deep=True)
                if not isinstance(key, Hashable):
                    continue  # the constructor reports an unhashable key itself
                if key in seen:
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        f"duplicate key {key!r}",
                        key_node.start_mark,
                    )
                seen.add(key)
        super().flatten_mapping(node)


def parse_profile(data: bytes | str, source: str = "profile") -> Profile:
    """Validate YAML text; every problem is reported with its path."""
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ProfileError(
                f"{source}: the profile must be UTF-8 text ({exc.reason} at byte {exc.start})"
            ) from exc
    else:
        text = data
    try:
        loaded = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        problem = getattr(exc, "problem", None) or str(exc)
        raise ProfileError(f"{source}: invalid YAML{where}: {problem}") from exc
    if not isinstance(loaded, dict):
        raise ProfileError(f"{source}: a profile is a YAML mapping with version, name and columns")
    try:
        return Profile.model_validate(loaded)
    except ValidationError as exc:
        raise ProfileError(format_validation_error(exc, source)) from exc


def load_profile(path: Path) -> tuple[Profile, bytes]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read the profile {path}: {exc.strerror or exc}") from exc
    return parse_profile(data, path.name), data


def schema() -> dict[str, Any]:
    """The JSON Schema written to docs/profile.schema.json."""
    document = Profile.model_json_schema()
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **document}
