"""Fail when an installed distribution has a licence outside the allow-list.

  uv sync --locked --all-extras && uv run python tools/check_licences.py

Runtime distributions (the project's dependencies with every extra, from ``uv export``) must carry
MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC, PSF-2.0 or one of the permissive licences that
numpy's expression adds for bundled code (0BSD, Zlib, CC0-1.0); a classifier that says only "BSD
License" counts as BSD. Development-only distributions may also be MPL-2.0. The licence is read from
``License-Expression`` (PEP 639), then the ``License`` field, then the classifiers; an expression with
OR needs one allowed term, one with AND needs all. A classifier the table does not know is kept as
written, so a FAIL line shows the raw licence text. Exit 0 clean, 1 with findings, 2 when uv fails (its
error output is printed).
"""

from __future__ import annotations

import re
import subprocess
import sys
from importlib.metadata import Distribution, distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "data-cleaning-tool"
ALLOWED = {
    "MIT",
    "BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "Apache-2.0",
    "ISC",
    "PSF-2.0",
    "0BSD",
    "Zlib",
    "CC0-1.0",
}
DEV_ALSO = {"MPL-2.0"}
FIELD = {
    "mit": "MIT",
    "mit license": "MIT",
    "bsd": "BSD",
    "bsd license": "BSD",
    "bsd-3-clause": "BSD-3-Clause",
    "3-clause bsd license": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "apache-2.0": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "isc": "ISC",
    "isc license": "ISC",
    "psf": "PSF-2.0",
    "psf-2.0": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "mpl-2.0": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
}
SPDX = re.compile(r"[A-Za-z0-9.+-]+(?:\s+(?:AND|OR)\s+[A-Za-z0-9.+-]+)+")
CLASSIFIER = {
    "MIT License": "MIT",
    "BSD License": "BSD",
    "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_names() -> set[str]:
    """The locked runtime closure with every extra, without the dev group and the project itself."""
    command = ["uv", "export", "--locked", "--no-dev", "--all-extras", "--no-hashes", "--no-emit-project"]
    try:
        out = subprocess.run(command, check=True, capture_output=True, text=True, cwd=ROOT).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        lines = [f"check_licences: {' '.join(command)} failed: {exc}"]
        detail = (getattr(exc, "stderr", None) or "").strip()
        if detail:
            lines.append(detail)
        print("\n".join(lines), file=sys.stderr)
        raise SystemExit(2) from exc
    names = set()
    for line in out.splitlines():
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*==", line.strip())
        if match:
            names.add(canonical(match.group(1)))
    return names


def terms(expression: str) -> tuple[str, list[str]]:
    """Split an SPDX expression into its operator ('OR', 'AND' or '') and its licence ids."""
    parts = [p for p in re.split(r"[\s()]+", expression) if p]
    ids = [p for p in parts if p.upper() not in ("OR", "AND", "WITH")]
    operator = "AND" if "AND" in parts else "OR" if "OR" in parts else ""
    return operator, ids


def licence_of(dist: Distribution) -> tuple[str, list[str], str]:
    """(operator, licence ids, source) for one distribution; ids are empty when nothing is recognised."""
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        operator, ids = terms(expression)
        return operator, ids, "expression"
    field = (meta.get("License") or "").strip().splitlines()
    first = field[0].strip() if field else ""
    if first.lower() in FIELD:
        return "", [FIELD[first.lower()]], "field"
    if SPDX.fullmatch(first):
        operator, ids = terms(first)
        return operator, ids, "field"
    found = []
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            name = classifier.rsplit(" :: ", 1)[-1]
            # a name this table does not know stays as written, so a FAIL line shows the raw licence text
            found.append(CLASSIFIER.get(name, name))
    return ("OR" if len(found) > 1 else ""), found, "classifier"


def verdict(operator: str, ids: list[str], allowed: set[str]) -> bool:
    if not ids:
        return False
    if operator == "AND":
        return all(i in allowed for i in ids)
    return any(i in allowed for i in ids)


def main() -> int:
    runtime = runtime_names()
    problems = 0
    seen = set()
    for dist in sorted(distributions(), key=lambda d: canonical(d.metadata["Name"])):
        name = canonical(dist.metadata["Name"])
        if name in seen or name == PROJECT:
            continue
        seen.add(name)
        group = "runtime" if name in runtime else "dev"
        allowed = ALLOWED if group == "runtime" else ALLOWED | DEV_ALSO
        operator, ids, source = licence_of(dist)
        text = f" {operator} ".join(ids) if ids else "unknown"
        ok = verdict(operator, ids, allowed)
        problems += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {group:<7} {name} {dist.version}: {text} ({source})")
    missing = sorted(runtime - seen)
    for name in missing:
        print(f"FAIL runtime {name}: in uv.lock but not installed (run uv sync --locked --all-extras)")
    problems += len(missing)
    if problems:
        print(f"{problems} distribution(s) outside the allow-list or without a recognisable licence")
        return 1
    print(f"{len(seen)} distributions checked, {len(runtime)} of them runtime; all licences allowed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
