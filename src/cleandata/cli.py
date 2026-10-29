"""The ``clean-data`` command line: ``profile``, ``init`` and ``clean``.

stdout carries only results (the profile table, the summary line, ``init`` YAML), so it can be
piped; logging goes to stderr as ``LEVEL: message``. Every expected failure maps to its exit code
(errors.py); anything else is exit 1 with the traceback at ``--log-level debug``.
"""

from __future__ import annotations

import codecs
import functools
import hashlib
import importlib.util
import io
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar, cast

import click

from cleandata import __version__
from cleandata.errors import EXIT_INTERNAL, EXIT_INTERRUPTED, CleanDataError, UsageError

log = logging.getLogger("cleandata")
F = TypeVar("F", bound=Callable[..., Any])
DELIMITER_WORDS = {"tab": "\t", "\\t": "\t", "comma": ",", "semicolon": ";", "pipe": "|"}
LEVELS = ("debug", "info", "warning", "error")


def version_line() -> str:
    from cleandata.report.build import package_version

    extras = [
        name
        for name, module in (("fuzzy", "rapidfuzz"), ("parquet", "pyarrow"))
        if importlib.util.find_spec(module) is not None
    ]
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    return (
        f"clean-data {__version__} (Python {python}, pandas {package_version('pandas')}, "
        f"extras: {', '.join(extras) if extras else 'none'})"
    )


def _print_version(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if value and not ctx.resilient_parsing:
        click.echo(version_line())
        ctx.exit(0)


def setup_logging(level: str, log_file: Path | None) -> None:
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()
    stream = logging.StreamHandler(sys.stderr)
    stream.setLevel(level.upper())
    stream.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.addHandler(stream)
    log.setLevel(logging.DEBUG if log_file is not None else level.upper())
    log.propagate = False
    if log_file is not None:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
        )
        log.addHandler(file_handler)


def handled(func: F) -> F:
    """Map exceptions to exit codes; tracebacks only at debug level."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except CleanDataError as exc:
            log.error("%s", exc)
            log.debug("traceback", exc_info=True)
            raise SystemExit(exc.exit_code) from None
        except KeyboardInterrupt:
            log.error("interrupted; temporary files removed")
            raise SystemExit(EXIT_INTERRUPTED) from None
        except (click.ClickException, click.exceptions.Exit, SystemExit):
            raise
        except Exception as exc:
            log.error(
                "internal error: %s: %s (run with --log-level debug for the traceback)",
                type(exc).__name__,
                exc,
            )
            log.debug("traceback", exc_info=True)
            raise SystemExit(EXIT_INTERNAL) from None

    return cast(F, wrapper)


def _encoding(_ctx: click.Context, _param: click.Parameter, value: str | None) -> str | None:
    if value is None:
        return None
    try:
        codecs.lookup(value)
    except LookupError:
        raise click.BadParameter(f"unknown encoding {value!r}") from None
    return value


def _delimiter(_ctx: click.Context, _param: click.Parameter, value: str | None) -> str | None:
    if value is None:
        return None
    resolved = DELIMITER_WORDS.get(value.lower(), value)
    if resolved not in (",", ";", "\t", "|"):
        raise click.BadParameter("use comma, semicolon, tab or pipe (or the character itself)")
    return resolved


def _header_row(_ctx: click.Context, _param: click.Parameter, value: str | None) -> int | str | None:
    if value is None or value == "auto":
        return value
    if value.isdigit() and int(value) >= 1:
        return int(value)
    raise click.BadParameter("a 1-based row number or auto")


def read_overrides(func: F) -> F:
    options = [
        click.option(
            "--sheet", "sheets", multiple=True, metavar="NAME", help="Sheet name or pattern (repeatable)."
        ),
        click.option("--encoding", callback=_encoding, help="Encoding instead of detection."),
        click.option("--delimiter", callback=_delimiter, help="comma, semicolon, tab or pipe."),
        click.option(
            "--header-row", callback=_header_row, metavar="N|auto", help="Header row (default auto)."
        ),
        click.option(
            "--max-rows",
            type=click.IntRange(min=1),
            default=200_000,
            show_default=True,
            help="Analyse at most this many records.",
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return func


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--log-level", type=click.Choice(LEVELS), default="info", show_default=True)
@click.option("--quiet", is_flag=True, help="Only errors on stderr (same as --log-level error).")
@click.option(
    "--log-file", type=click.Path(dir_okay=False, path_type=Path), help="Also log at debug level here."
)
@click.option(
    "--version",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_print_version,
    help="Show the version, Python, pandas and installed extras.",
)
def cli(log_level: str, quiet: bool, log_file: Path | None) -> None:
    """Clean messy CSV and Excel exports reproducibly from a YAML profile."""
    setup_logging("error" if quiet else log_level, log_file)


def _analyse(
    file: Path,
    sheets: tuple[str, ...],
    encoding: str | None,
    delimiter: str | None,
    header_row: int | str | None,
    max_rows: int,
) -> Any:
    from cleandata.core.analyse import analyse_file

    return analyse_file(
        file,
        sheets=sheets,
        encoding=encoding or "auto",
        delimiter=delimiter or "auto",
        header_row="auto" if header_row is None else header_row,
        max_rows=max_rows,
    )


@cli.command("profile")
@click.argument("file", type=click.Path(path_type=Path))
@read_overrides
@click.option(
    "--json", "json_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the analysis as JSON."
)
@click.option(
    "--html", "html_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the analysis as HTML."
)
@handled
def profile_command(
    file: Path,
    sheets: tuple[str, ...],
    encoding: str | None,
    delimiter: str | None,
    header_row: int | str | None,
    max_rows: int,
    json_path: Path | None,
    html_path: Path | None,
) -> None:
    """Analyse a file before any profile exists."""
    from cleandata.core.analyse import analysis_report, text_table
    from cleandata.core.writer import write_text
    from cleandata.report.build import round_floats
    from cleandata.report.html import render_html

    analysis = _analyse(file, sheets, encoding, delimiter, header_row, max_rows)
    for warning in analysis.warnings:
        log.warning("%s", warning)
    click.echo(text_table(analysis), nl=False)
    if json_path is not None or html_path is not None:
        report = round_floats(analysis_report(analysis))
        if json_path is not None:
            write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        if html_path is not None:
            write_text(html_path, render_html(report))


@cli.command("init")
@click.argument("file", type=click.Path(path_type=Path))
@read_overrides
@click.option(
    "--out", "out", default="-", show_default=True, metavar="PATH|-", help="Where to write the skeleton."
)
@click.option("--force", is_flag=True, help="Replace an existing file.")
@handled
def init_command(
    file: Path,
    sheets: tuple[str, ...],
    encoding: str | None,
    delimiter: str | None,
    header_row: int | str | None,
    max_rows: int,
    out: str,
    force: bool,
) -> None:
    """Write a commented profile skeleton for a file."""
    from cleandata.core.writer import write_text
    from cleandata.skeleton.init import build_skeleton

    target = None if out == "-" else Path(out)
    if target is not None and target.exists() and not force:
        raise UsageError(f"{target} exists; pass --force to replace it")
    analysis = _analyse(file, sheets, encoding, delimiter, header_row, max_rows)
    for warning in analysis.warnings:
        log.warning("%s", warning)
    text = build_skeleton(analysis, file.stem)
    if target is None:
        click.echo(text, nl=False)
    else:
        write_text(target, text)
        log.info("init: profile skeleton written to %s", target.name)


@cli.command("clean")
@click.argument("file", type=click.Path(path_type=Path))
@click.option("--profile", "profile_path", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path), help=".csv, .xlsx or .parquet")
@click.option(
    "--report",
    "reports",
    multiple=True,
    required=True,
    type=click.Path(path_type=Path),
    help=".html or .json, once or twice",
)
@click.option("--rejects", type=click.Path(path_type=Path), help=".csv or .xlsx")
@click.option("--dry-run", is_flag=True, help="Run every stage, write only the reports.")
@click.option("--strict", is_flag=True, help="Exit 4 on any non-duplicate reject or warning.")
@click.option("--explain", is_flag=True, help="Log the resolved plan and every rule with examples.")
@click.option("--chunked", is_flag=True, help="Stream the file in chunks (chunk-compatible profiles only).")
@click.option("--chunk-size", type=int, default=100_000, show_default=True)
@handled
def clean_command(
    file: Path,
    profile_path: Path,
    out: Path | None,
    reports: tuple[Path, ...],
    rejects: Path | None,
    dry_run: bool,
    strict: bool,
    explain: bool,
    chunked: bool,
    chunk_size: int,
) -> None:
    """Clean a file with a profile."""
    from cleandata.core.pipeline import RunOptions, run_clean
    from cleandata.core.profile import load_profile

    if len(reports) > 2:
        raise UsageError("--report may be given at most twice")
    profile, data = load_profile(profile_path)
    options = RunOptions(
        input=file,
        out=out,
        rejects=rejects,
        reports=reports,
        dry_run=dry_run,
        strict=strict,
        explain=explain,
        chunked=chunked,
        chunk_size=chunk_size,
    )
    result = run_clean(profile, profile_path.name, hashlib.sha256(data).hexdigest(), options)
    click.echo(result.summary)
    if result.exit_code:
        written = "" if dry_run else "; the cleaned output was not written"
        log.error("run failed (%s)%s", result.status.replace("_", " "), written)
        raise SystemExit(result.exit_code)


def _safe_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")


def main() -> None:
    _safe_streams()
    cli()
