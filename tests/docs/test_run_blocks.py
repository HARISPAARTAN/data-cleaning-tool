"""Every command block marked ``<!-- run exit=N -->`` in the READMEs and docs/cli.md runs as written.

A marked block is a fenced ``bash`` or ``powershell`` block right below the marker. It may hold only
``clean-data`` commands (``uv run`` in front is allowed), comments and line continuations (a trailing
backslash in bash, a trailing backtick in PowerShell). The commands of one block run in order,
in-process, in one folder holding copies of fixtures 1 to 4 and the profiles; each must end with exit
code N. Examples that need an extra (fixture 2, Parquet output) are not marked, so the core-only job
runs this file too.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from support import FIXTURE_FILES, ROOT, SOURCE_DATE_EPOCH, invoke, workspace

DOCS = ["README.md", "README.de.md", "docs/cli.md"]
BLOCK = re.compile(r"<!-- run exit=(\d+) -->\n```(bash|powershell)\n(.*?)^```", re.MULTILINE | re.DOTALL)
CONTINUATION = {"bash": " \\", "powershell": " `"}


def commands(body: str, shell: str) -> list[str]:
    found: list[str] = []
    pending = ""
    for line in body.splitlines():
        if line.endswith(CONTINUATION[shell]):
            pending += line[: -len(CONTINUATION[shell])].strip() + " "
            continue
        command = (pending + line.strip()).strip()
        pending = ""
        if command and not command.startswith("#"):
            found.append(command)
    assert not pending, f"a {shell} block ends with a line continuation"
    return found


def blocks() -> list[tuple[str, int, int, list[str]]]:
    found = []
    for doc in DOCS:
        text = (ROOT / doc).read_text(encoding="utf-8")
        for number, match in enumerate(BLOCK.finditer(text), start=1):
            exit_code, shell, body = int(match.group(1)), match.group(2), match.group(3)
            found.append((doc, number, exit_code, commands(body, shell)))
    return found


BLOCKS = blocks()


def arguments(command: str) -> list[str]:
    parts = shlex.split(command)
    if parts[:2] == ["uv", "run"]:
        parts = parts[2:]
    assert parts[:1] == ["clean-data"], f"only clean-data commands may sit in a run block: {command}"
    return parts[1:]


@pytest.mark.parametrize("doc", DOCS)
def test_every_document_has_run_blocks(doc: str) -> None:
    assert any(block[0] == doc and block[3] for block in BLOCKS), f"{doc} has no <!-- run exit=N --> block"


@pytest.mark.parametrize(
    ("doc", "number", "exit_code", "lines"), BLOCKS, ids=[f"{b[0]}#{b[1]}" for b in BLOCKS]
)
def test_run_block(
    doc: str, number: int, exit_code: int, lines: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = [FIXTURE_FILES[key][0] for key in ("01", "02", "03", "04")]
    monkeypatch.chdir(workspace(tmp_path, *names))
    for command in lines:
        result = invoke(arguments(command), env={"SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH})
        assert result.exit_code == exit_code, f"{doc} block {number}: {command}\n{result.stderr}"
