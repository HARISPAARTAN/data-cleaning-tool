"""Render the profile skeleton and load it back through the pydantic model before anyone sees it.

The skeleton carries what the analysis found (encoding, delimiter, header row, sheets, locale, one
column entry per non-empty column with its guessed type) and comments with counts, examples and
ambiguity notes. It adds no validation rules and no missing strategies: those are the analyst's
decisions. Every string scalar is written double-quoted, so YAML 1.1 never reads ``no`` as false.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Final

import jinja2

from cleandata import __version__
from cleandata.core.analyse import Analysis, ColumnAnalysis
from cleandata.core.profile import parse_profile
from cleandata.errors import InputError, InternalError

PLAIN_KEY: Final = re.compile(r"[a-z][a-z0-9_]*")
YAML11_WORDS: Final = frozenset({"y", "n", "yes", "no", "on", "off", "true", "false", "null"})
DEFAULT_DATE_FORMATS: Final = ["%Y-%m-%d"]
DEFAULT_DATETIME_FORMATS: Final = ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]
COMMENT_WIDTH: Final = 40
NAME_WIDTH: Final = 120
# What PyYAML refuses in any scalar or comment (everything outside its printable set: DEL, the C1 range
# without U+0085, U+FFFE, U+FFFF, lone surrogates), plus the three characters YAML 1.1 loaders treat as
# line breaks (U+0085, U+2028, U+2029). All of them are written as \uXXXX escapes, which a double-quoted
# scalar reads back as the same character.
YAML_UNSAFE: Final = re.compile(
    "[^\\t\\n\\r\\x20-\\x7e\\xa0-\\ud7ff\\ue000-\\ufffd\\U00010000-\\U0010ffff]|[\\x85\\u2028\\u2029]"
)


def _json_body(text: str) -> str:
    """The text as the inside of a JSON string, with each character YAML refuses or breaks on escaped."""
    escaped = json.dumps(text, ensure_ascii=False)[1:-1]
    return YAML_UNSAFE.sub(lambda match: f"\\u{ord(match.group()):04x}", escaped)


def quote(value: object) -> str:
    """A YAML flow scalar: strings double-quoted (JSON escapes are valid YAML), lists in brackets."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return json.dumps(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(quote(item) for item in value) + "]"
    return '"' + _json_body(str(value)) + '"'


def key(name: str) -> str:
    """A mapping key, quoted unless it is a plain snake_case word YAML 1.1 cannot misread."""
    if PLAIN_KEY.fullmatch(name) and name not in YAML11_WORDS:
        return name
    return quote(name)


def comment_text(text: str, width: int = COMMENT_WIDTH) -> str:
    """One line, control characters escaped, at most 40 characters (``width`` for the file name)."""
    escaped = _json_body(text)
    return escaped if len(escaped) <= width else escaped[: width - 1] + "~"


def column_entry(
    item: ColumnAnalysis, analysis: Analysis, date_formats: Sequence[str], datetime_formats: Sequence[str]
) -> dict[str, Any]:
    guess = item.guess
    ctype = "string" if guess is None else guess.type
    locale: dict[str, Any] = {}
    strip: list[str] = []
    if guess is not None and ctype in ("integer", "number"):
        if (guess.decimal, guess.thousands) != analysis.locale:
            locale["decimal"] = guess.decimal
            locale["thousands"] = guess.thousands
        strip = list(guess.strip)
    if guess is not None and ctype == "date":
        own = [p for p in guess.patterns if p != "native"]
        if own and own != list(date_formats):
            locale["date_formats"] = own
    if guess is not None and ctype == "datetime":
        own = [p for p in guess.patterns if p != "native"]
        if own and own != list(datetime_formats):
            locale["datetime_formats"] = own
    comments = [f"{item.non_empty} non-empty, {item.distinct}{'+' if item.distinct_capped else ''} distinct"]
    if guess is not None:
        comments.append(f"{guess.label()} parses {guess.share * 100:.1f} % of the values")
    if item.examples:
        comments.append("e.g. " + ", ".join(f'"{comment_text(example)}"' for example in item.examples))
    for token, count in item.null_like.items():
        comments.append(f'null-like "{comment_text(token)}" x{count}')
    comments.extend(item.notes)
    return {"name": item.normalised, "type": ctype, "locale": locale, "strip": strip, "comments": comments}


def render_skeleton(analysis: Analysis, name: str) -> str:
    info = analysis.info
    null_tokens = [""]
    for item in analysis.columns:
        for token in item.null_like:
            if token not in null_tokens:
                null_tokens.append(token)
    date_formats = analysis.date_formats or DEFAULT_DATE_FORMATS
    datetime_formats = analysis.datetime_formats or DEFAULT_DATETIME_FORMATS
    selected = analysis.selected_sheets
    columns = [
        column_entry(item, analysis, date_formats, datetime_formats)
        for item in analysis.columns
        if item.non_empty > 0
    ]
    if not columns:
        raise InputError(f"no usable columns found in {info.name} (no records, or every column is empty)")
    if info.kind == "xlsx":
        # the rows the sheets were actually read with, so an explicit --header-row reaches the skeleton
        header_rows = {sheet.header_row for sheet in info.sheets}
        header_row: object = header_rows.pop() if len(header_rows) == 1 else "auto"
    else:
        header_row = info.header_row
    environment = jinja2.Environment(
        loader=jinja2.PackageLoader("cleandata.skeleton", "templates"),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )
    environment.filters["q"] = quote
    environment.filters["key"] = key
    environment.filters["comment_text"] = comment_text
    decimal, thousands = analysis.locale
    return environment.get_template("profile.yaml.j2").render(
        version=__version__,
        name=name,
        name_width=NAME_WIDTH,
        info=info,
        analysis=analysis,
        header_row=header_row,
        sheets=selected if info.kind == "xlsx" else None,
        sheet_column="source_sheet" if info.kind == "xlsx" and len(selected) > 1 else None,
        null_tokens=null_tokens,
        decimal=decimal,
        thousands=thousands,
        date_formats=date_formats,
        datetime_formats=datetime_formats,
        normalised=[item.normalised for item in analysis.columns],
        drop=analysis.empty_columns,
        columns=columns,
    )


def build_skeleton(analysis: Analysis, name: str) -> str:
    """The skeleton text; a skeleton that fails validation is a bug (exit 1)."""
    text = render_skeleton(analysis, name)
    try:
        parse_profile(text, "skeleton")
    except Exception as exc:
        raise InternalError(f"init produced an invalid profile: {exc}") from exc
    return text
