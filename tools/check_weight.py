"""Fail when the Pages site is heavier than 3 MB (3,000,000 bytes).

  uv run python tools/check_weight.py

The limit applies to the whole of web/, which bounds what any single page can load: the usage page
with the vendored kit, the self-contained report and the demo files. Exit 0 within the limit, 1 above,
and also 1 when web/ is missing or holds no file.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
LIMIT = 3_000_000


def sizes() -> list[tuple[str, int]]:
    found = [(p.relative_to(ROOT).as_posix(), p.stat().st_size) for p in WEB.rglob("*") if p.is_file()]
    return sorted(found, key=lambda item: (-item[1], item[0]))


def main() -> int:
    if not WEB.is_dir():
        print("FAIL web/ is missing, so nothing was weighed")
        return 1
    found = sizes()
    if not found:
        print("FAIL web/ holds no file, so nothing was weighed")
        return 1
    total = sum(size for _, size in found)
    for name, size in found[:10]:
        print(f"{size:>10,}  {name}")
    verdict = "ok  " if total <= LIMIT else "FAIL"
    print(f"{verdict} web/ holds {len(found)} files, {total:,} of {LIMIT:,} bytes")
    return 0 if total <= LIMIT else 1


if __name__ == "__main__":
    sys.exit(main())
