"""Build the Pages demo: ``web/demo/*`` from fixture 1 and ``web/index.html`` from the usage template.

  uv run python tools/build_demo.py           rebuild web/demo/ and web/index.html
  uv run python tools/build_demo.py --check   rebuild into a temporary folder; exit 1 on any byte difference

The four commands of the usage page run in-process through click's ``CliRunner`` inside a temporary
copy of ``fixtures/`` and ``profiles/``, with relative paths and ``SOURCE_DATE_EPOCH=1793232000``
(2026-10-29T00:00:00Z). The output then depends only on the code, the locked packages and the Python
minor version, which the report records (CI builds with Python 3.13).
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import jinja2
from click.testing import CliRunner
from markupsafe import Markup, escape

import cleandata
from cleandata.cli import cli
from cleandata.core.coerce import canonical_str
from cleandata.core.frame import META_ROW, data_columns, rows_of
from cleandata.core.pipeline import RunOptions, run_clean
from cleandata.core.profile import load_profile
from cleandata.core.text import display

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
TEMPLATE = ROOT / "tools" / "templates" / "usage.html.j2"
EPOCH = "1793232000"
REPO = "https://github.com/HARISPAARTAN/data-cleaning-tool"
INPUT = "fixtures/01_survey_export_de.csv"
PROFILE = "profiles/01_survey.yaml"
INIT_LINES = 30
PREVIEW_ROWS = 8
EXCERPT = ("[plan]", "[read]", "[types]    household_income", "[validate] age.", "[dedupe]", "[missing]")
CONTINUATION = "INFO:            "
BACKSLASH = chr(92)
DEMO_FILES = {
    "report.html": "out/report.html",
    "report.json": "out/report.json",
    "profile.yaml": PROFILE,
    "input.csv": INPUT,
    "cleaned.csv": "out/cleaned.csv",
    "rejects.csv": "out/rejects.csv",
}
EXIT_CODES = [
    ("0", "success; a run without --strict may still have rejects"),
    ("1", "internal error (a bug); run again with --log-level debug"),
    ("2", "usage or profile error, a profile that cannot run chunked, a missing extra"),
    ("3", "input error: missing or undecodable file, no matching sheet, a profile column absent"),
    ("4", "quality gate: --strict or quality.max_reject_rate; rejects and reports written, output not"),
    ("5", "output error: target not writable, Excel row limit exceeded"),
    ("130", "interrupted; temporary files removed"),
]


@dataclass
class Step:
    """One command of the usage page with its captured streams."""

    key: str
    title: str
    args: list[str]
    stdout: str
    stderr: str
    exit_code: int
    note: str = ""

    @property
    def command(self) -> str:
        return " ".join(["clean-data", *self.args])


@dataclass
class Cell:
    text: str
    changed: bool = False


@dataclass
class Record:
    label: str
    status: str
    before: list[Cell]
    after: list[Cell]


@contextlib.contextmanager
def workspace() -> Iterator[Path]:
    """A temporary folder with copies of fixture 1 and the profiles; the current directory inside it."""
    with tempfile.TemporaryDirectory(prefix="build-demo-") as name:
        folder = Path(name)
        (folder / "fixtures").mkdir()
        shutil.copy2(ROOT / INPUT, folder / INPUT)
        shutil.copytree(ROOT / "profiles", folder / "profiles")
        with contextlib.chdir(folder):
            yield folder


def run(key: str, title: str, args: Sequence[str]) -> Step:
    result = CliRunner().invoke(cli, list(args), env={"SOURCE_DATE_EPOCH": EPOCH}, catch_exceptions=False)
    return Step(key, title, list(args), result.stdout, result.stderr, result.exit_code)


def excerpt(text: str, prefixes: Sequence[str]) -> str:
    """The lines starting with one of the prefixes (after ``INFO: ``) and their continuation lines."""
    kept: list[str] = []
    keeping = False
    skipped = False
    for line in text.splitlines():
        if line.startswith(CONTINUATION) and keeping:
            kept.append(line)
            continue
        keeping = any(line.startswith(f"INFO: {prefix}") for prefix in prefixes)
        if keeping:
            if skipped and kept:
                kept.append("…")
            kept.append(line)
            skipped = False
        else:
            skipped = True
    if skipped:
        kept.append("…")
    return "\n".join(kept) + "\n"


def run_commands() -> list[Step]:
    profile = run("profile", "Profile the file", ["profile", INPUT])
    init = run("init", "Write a profile skeleton", ["init", INPUT])
    lines = init.stdout.splitlines()
    init.note = f"The first {INIT_LINES} of {len(lines)} lines."
    init.stdout = "\n".join(lines[:INIT_LINES]) + "\n"
    explain = run(
        "explain",
        "Dry run with --explain",
        ["clean", INPUT, "--profile", PROFILE, "--dry-run", "--explain", "--report", "out/dry-run.json"],
    )
    explain.note = (
        "An excerpt of stderr: the plan, then the blocks for one column, the age rules, de-duplication "
        "and the fills. The full text is in the report's Explain section when --explain is given."
    )
    explain.stderr = excerpt(explain.stderr, EXCERPT)
    clean = run(
        "clean",
        "Clean, with rejects and both reports",
        [
            "clean",
            INPUT,
            "--profile",
            PROFILE,
            "--out",
            "out/cleaned.csv",
            "--rejects",
            "out/rejects.csv",
            "--report",
            "out/report.html",
            "--report",
            "out/report.json",
        ],
    )
    steps = [profile, init, explain, clean]
    for step in steps:
        if step.exit_code != 0:
            raise SystemExit(f"build_demo: '{step.command}' ended with exit {step.exit_code}\n{step.stderr}")
    return steps


def preview(folder: Path) -> tuple[list[str], list[Record]]:
    """The first records before (as read) and after cleaning, with changed cells marked."""
    profile, _ = load_profile(folder / PROFILE)
    options = RunOptions(input=folder / INPUT, dry_run=True, reports=(folder / "out" / "preview.json",))
    result = run_clean(profile, "01_survey.yaml", "0" * 64, options, keep_frames=True)
    assert result.raw is not None and result.output is not None
    names = data_columns(result.output)
    raw_rows = rows_of(result.raw, [META_ROW, *names])[:PREVIEW_ROWS]
    after = {row[0]: row[1:] for row in rows_of(result.output, [META_ROW, *names])}
    with (folder / "out" / "rejects.csv").open(encoding="utf-8", newline="") as handle:
        removed = {int(row["_source_row"]): row["_rule"] for row in csv.DictReader(handle)}
    records = []
    for source_row, *values in raw_rows:
        before = ["" if value is None else str(value) for value in values]
        cells = [Cell(display(text)) for text in before]
        if source_row in after:
            out = [canonical_str(value) for value in after[source_row]]
            status = "kept"
            cleaned = [Cell(display(text), text != raw) for text, raw in zip(out, before, strict=True)]
        else:
            status = f"removed: {removed.get(int(str(source_row)), 'unknown')}"
            cleaned = [Cell("") for _ in names]
        records.append(Record(f"r{source_row}", status, cells, cleaned))
    return names, records


def html_text(value: object) -> Markup:
    """Escape for HTML and write each backslash as ``&#92;``, so the page source holds no escape sequence."""
    return Markup(str(escape(value)).replace(BACKSLASH, "&#92;"))


def size_of(path: Path) -> str:
    return f"{path.stat().st_size:,} bytes"


def render(steps: list[Step], names: list[str], records: list[Record], files: dict[str, Path]) -> str:
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE.parent),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        finalize=html_text,
    )
    summary = steps[-1].stdout.strip()
    counts = [part.rsplit(" ", 1) for part in summary.split(" · ")]
    return environment.get_template(TEMPLATE.name).render(
        version=cleandata.__version__,
        repo=REPO,
        steps=steps,
        summary=summary,
        counts=counts,
        names=names,
        records=records,
        files=[(name, size_of(path)) for name, path in files.items()],
        exit_codes=EXIT_CODES,
    )


def build(target: Path) -> list[Path]:
    """Write index.html and demo/* below ``target``; returns the written paths."""
    with workspace() as folder:
        steps = run_commands()
        names, records = preview(folder)
        demo = target / "demo"
        demo.mkdir(parents=True, exist_ok=True)
        written = []
        sources = {name: folder / source for name, source in DEMO_FILES.items()}
        for name, source in sources.items():
            shutil.copyfile(source, demo / name)
            written.append(demo / name)
        page = target / "index.html"
        page.write_bytes(render(steps, names, records, sources).encode("utf-8"))
        written.append(page)
    return written


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check() -> int:
    with tempfile.TemporaryDirectory(prefix="build-demo-check-") as name:
        fresh = Path(name)
        produced = build(fresh)
        problems = []
        for path in produced:
            relative = path.relative_to(fresh)
            committed = WEB / relative
            if not committed.is_file():
                problems.append(f"missing: web/{relative.as_posix()}")
            elif digest(committed) != digest(path):
                problems.append(f"differs: web/{relative.as_posix()}")
        expected = {
            path.relative_to(fresh / "demo").as_posix() for path in produced if path.parent.name == "demo"
        }
        for extra in sorted((WEB / "demo").glob("*")):
            if extra.name not in expected:
                problems.append(f"not produced by the build: web/demo/{extra.name}")
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} difference(s): run uv run python tools/build_demo.py and commit web/")
        return 1
    print(f"web/index.html and web/demo/ match a fresh build ({len(produced)} files)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare a fresh build with web/")
    args = parser.parse_args(argv)
    os.environ["SOURCE_DATE_EPOCH"] = EPOCH
    if args.check:
        return check()
    written = build(WEB)
    for path in written:
        print(f"wrote {path.relative_to(ROOT).as_posix()} ({size_of(path)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
