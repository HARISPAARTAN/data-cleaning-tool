"""Data-Cleaning Tool: cleans messy CSV and Excel exports reproducibly from a YAML profile."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

DISTRIBUTION = "data-cleaning-tool"

try:
    __version__ = version(DISTRIBUTION)
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0.0.0"
