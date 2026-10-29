"""Write docs/profile.schema.json from the pydantic profile model, or check that it is current.

uv run python tools/export_schema.py            # write
uv run python tools/export_schema.py --check    # exit 1 when the committed file differs
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from cleandata.core.profile import schema

TARGET = Path(__file__).resolve().parents[1] / "docs" / "profile.schema.json"


def render() -> str:
    return json.dumps(schema(), ensure_ascii=False, indent=2) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
        if current != text:
            print(f"{TARGET.name} is out of date; run tools/export_schema.py", file=sys.stderr)
            return 1
        print(f"{TARGET.name} is current", file=sys.stderr)
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
