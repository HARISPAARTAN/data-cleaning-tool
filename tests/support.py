"""Helpers shared by the test modules (on ``sys.path`` through ``[tool.pytest.ini_options] pythonpath``).

The profile model and the command line are imported inside the helpers that need them, so the unit
tests of the early modules run before those modules exist.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from cleandata.core.frame import META_ROW, META_SHEET, CarryState, StageContext, new_frame

if TYPE_CHECKING:
    from click.testing import Result

    from cleandata.core.profile import Profile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
PROFILES = ROOT / "profiles"
GOLDEN = ROOT / "tests" / "golden"
SOURCE_DATE_EPOCH = "1793232000"  # 2026-10-29T00:00:00Z
FIXTURE_FILES = {
    "01": ("01_survey_export_de.csv", "01_survey.yaml"),
    "02": ("02_partner_respondents.csv", "02_partner.yaml"),
    "03": ("03_wave_workbook.xlsx", "03_waves.yaml"),
    "04": ("04_legacy_partner_latin1.txt", "04_legacy.yaml"),
    "05": ("05_panel_large.csv", "05_large.yaml"),
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def profile_of(columns: dict[str, Any], **sections: Any) -> Profile:
    """A validated profile from plain dictionaries; ``version`` and ``name`` are filled in."""
    from cleandata.core.profile import Profile

    return Profile.model_validate({"version": 1, "name": "test", "columns": columns, **sections})


def frame_of(
    columns: Sequence[str], rows: Sequence[Sequence[object]], sheet: str | None = None, first_row: int = 2
) -> pd.DataFrame:
    """A stage frame: data columns plus ``_source_row`` (from ``first_row``) and ``_source_sheet``."""
    full = [[*row, first_row + index, sheet] for index, row in enumerate(rows)]
    return new_frame([*columns, META_ROW, META_SHEET], full, list(range(len(rows))))


def context(
    profile: Profile,
    raw: pd.DataFrame,
    in_memory: bool = True,
    state: CarryState | None = None,
    limit: int = 5,
) -> StageContext:
    return StageContext(
        profile=profile,
        plans=profile.column_plans(),
        default_text=profile.default_text_plan(),
        raw=raw,
        state=state or CarryState(),
        in_memory=in_memory,
        limit=limit,
    )


def invoke(args: Sequence[str], env: dict[str, str] | None = None) -> Result:
    """Run ``clean-data`` in-process; stdout and stderr stay separate (click 8.2 and later)."""
    from click.testing import CliRunner

    from cleandata.cli import cli

    runner = CliRunner()
    return runner.invoke(cli, list(args), env=env, catch_exceptions=False)


def workspace(tmp_path: Path, *names: str) -> Path:
    """A folder with copies of the named fixtures and every profile, for commands with relative paths."""
    (tmp_path / "fixtures").mkdir()
    for name in names:
        shutil.copy2(FIXTURES / name, tmp_path / "fixtures" / name)
    shutil.copytree(PROFILES, tmp_path / "profiles")
    return tmp_path


def clean_fixture(
    number: str, tmp_path: Path, *extra: str, input_path: Path | None = None
) -> tuple[Result, Path]:
    """Clean one fixture with its profile; outputs land in ``tmp_path``. Returns the result and the folder."""
    data, profile = FIXTURE_FILES[number]
    source = input_path or FIXTURES / data
    args = [
        "clean",
        str(source),
        "--profile",
        str(PROFILES / profile),
        "--out",
        str(tmp_path / f"{number}.cleaned.csv"),
        "--rejects",
        str(tmp_path / f"{number}.rejects.csv"),
        "--report",
        str(tmp_path / f"{number}.report.json"),
        *extra,
    ]
    return invoke(args, env={"SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH}), tmp_path


def expected(number: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        (FIXTURES / "expected" / f"{number}.json").read_text(encoding="utf-8")
    )
    return loaded


def report(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded
