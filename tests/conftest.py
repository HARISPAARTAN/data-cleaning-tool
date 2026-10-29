"""Shared fixtures, markers and the hypothesis settings profiles.

Markers: ``needs_fuzzy`` and ``needs_parquet`` skip with the reason when the extra is absent (the
core-only CI job), ``timing`` marks the soft timing check. ``--update-golden`` rewrites the golden
outputs; review the diff before committing it. ``HYPOTHESIS_PROFILE=dev`` switches to random examples.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

settings.register_profile(
    "ci", derandomize=True, max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.register_profile(
    "dev", max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))

EXTRAS = {"needs_fuzzy": ("rapidfuzz", "fuzzy"), "needs_parquet": ("pyarrow", "parquet")}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden", action="store_true", help="rewrite tests/golden from the current code"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for marker, (module, extra) in EXTRAS.items():
        if importlib.util.find_spec(module) is not None:
            continue
        skip = pytest.mark.skip(reason=f'needs the optional extra "{extra}" ({module} is not installed)')
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


@pytest.fixture(scope="session")
def large_fixture(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Fixture 5, generated once per session (it is not committed); its SHA-256 matches the manifest."""
    import make_data
    from support import FIXTURES, sha256_bytes

    fixture = make_data.large_fixture(make_data.DEFAULT_SEED)
    manifest = (FIXTURES / "MANIFEST.json").read_text(encoding="utf-8")
    assert sha256_bytes(fixture.data) in manifest, "fixture 5 differs from fixtures/MANIFEST.json"
    folder = tmp_path_factory.mktemp("large")
    path = folder / fixture.name
    path.write_bytes(fixture.data)
    yield path
