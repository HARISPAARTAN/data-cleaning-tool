"""Soft timing check on fixture 5 (50,000 records): the median of three in-process runs.

Thresholds: 10 s in memory and 15 s chunked (``--chunk-size 7000``), set for GitHub's ubuntu-24.04
runner. Above a threshold the test still passes, prints a ``::warning::`` annotation and adds the time
to the job summary (``GITHUB_STEP_SUMMARY``); ``CLEANDATA_TIMING_STRICT=1`` makes it fail, for local
tuning only. No timing figure goes into the README.
"""

from __future__ import annotations

import os
import platform
import statistics
import time
from pathlib import Path

import pytest

from support import clean_fixture

pytestmark = pytest.mark.timing

RUNS = 3
LIMITS = {"in-memory": 10.0, "chunked": 15.0}
OPTIONS = {"in-memory": (), "chunked": ("--chunked", "--chunk-size", "7000")}


def median_seconds(mode: str, large_fixture: Path, tmp_path: Path) -> float:
    times = []
    for run in range(RUNS):
        start = time.perf_counter()
        result, _ = clean_fixture("05", tmp_path / f"{mode}-{run}", *OPTIONS[mode], input_path=large_fixture)
        times.append(time.perf_counter() - start)
        assert result.exit_code == 0, result.stderr
    return statistics.median(times)


@pytest.mark.parametrize("mode", ["in-memory", "chunked"])
def test_fixture_5_timing(
    mode: str, large_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for run in range(RUNS):
        (tmp_path / f"{mode}-{run}").mkdir()
    seconds = median_seconds(mode, large_fixture, tmp_path)
    limit = LIMITS[mode]
    line = (
        f"fixture 5, {mode}: median {seconds:.2f} s of {RUNS} runs (soft limit {limit:.0f} s) "
        f"on {platform.system()} {platform.machine()}, Python {platform.python_version()}"
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"- {line}\n")
    with capsys.disabled():
        if seconds > limit:
            print(f"\n::warning title=timing::{line}")
        else:
            print(f"\n{line}")
    if os.environ.get("CLEANDATA_TIMING_STRICT") == "1":
        assert seconds <= limit, line
