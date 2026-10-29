"""Exceptions and the exit codes they map to (docs/cli.md lists the same table)."""

from __future__ import annotations

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_QUALITY = 4
EXIT_OUTPUT = 5
EXIT_INTERRUPTED = 130


class CleanDataError(Exception):
    """Base class of every expected failure; the exit code says which kind it is."""

    exit_code = EXIT_INTERNAL


class InternalError(CleanDataError):
    """A broken invariant, such as row accounting that does not add up: a bug."""

    exit_code = EXIT_INTERNAL


class UsageError(CleanDataError):
    """A bad option, an invalid profile, a profile that cannot run chunked, a missing extra."""

    exit_code = EXIT_USAGE


class ProfileError(UsageError):
    """The profile YAML does not parse or does not validate; the message names each path."""


class MissingExtraError(UsageError):
    """The profile or the output format needs an optional extra that is not installed."""

    def __init__(self, extra: str, purpose: str) -> None:
        super().__init__(
            f'{purpose} needs the optional extra "{extra}": run "uv sync --extra {extra}" in a clone, '
            f'or install the wheel as "data-cleaning-tool[{extra}]"'
        )
        self.extra = extra


class InputError(CleanDataError):
    """The input file is missing, unreadable, undecodable or lacks a column the profile needs."""

    exit_code = EXIT_INPUT


class OutputError(CleanDataError):
    """A target cannot be written, or the cleaned rows exceed the Excel row limit."""

    exit_code = EXIT_OUTPUT
