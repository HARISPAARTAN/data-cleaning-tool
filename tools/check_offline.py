"""Fail when a page in web/ or the report template would load anything from another host.

  uv run python tools/check_offline.py

Checked: every .html, .css, .js and .j2 file below web/ and src/cleandata/report/ (the vendored kit
included). A resource reference is ``src``, ``srcset``, ``poster`` and ``data`` attributes, ``href``
on ``<link>``, ``url()`` and ``@import`` in CSS and style attributes, and ``import``, ``fetch`` and
``new Worker`` targets in scripts. It may not start with ``http:``, ``https:`` or ``//``. Links in
``<a href>`` are navigation, not requests, and may point anywhere. Exit 0 clean, 1 with findings, and
also 1 when web/ is missing or no file was found to check.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = [ROOT / "web", ROOT / "src" / "cleandata" / "report"]
SUFFIXES = {".html", ".css", ".js", ".mjs", ".j2"}
EXTERNAL = re.compile(r"^\s*(?:https?:)?//", re.IGNORECASE)
CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+)|@import\s+(?:url\(\s*)?["']?([^"');\s]+)""", re.IGNORECASE)
SCRIPT_URL = re.compile(
    r"""(?:\bimport\s*(?:\(|[^;]*?\bfrom\s*)|\bfetch\s*\(|\bnew\s+Worker\s*\(|\bimportScripts\s*\()\s*["'`]([^"'`]+)""",
)
RESOURCE_ATTRIBUTES = {"src", "srcset", "poster", "data", "background"}


def css_findings(text: str) -> list[str]:
    found = []
    for match in CSS_URL.finditer(text):
        target = match.group(1) or match.group(2) or ""
        if EXTERNAL.match(target):
            found.append(target.strip())
    return found


def script_findings(text: str) -> list[str]:
    return [m.group(1) for m in SCRIPT_URL.finditer(text) if EXTERNAL.match(m.group(1))]


class Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []
        self.inside: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        for name, value in values.items():
            targets = (
                [part.strip().split(" ")[0] for part in value.split(",")] if name == "srcset" else [value]
            )
            if name in RESOURCE_ATTRIBUTES and any(EXTERNAL.match(t) for t in targets):
                self.found.append(f"<{tag} {name}={value}>")
            if name == "style":
                self.found += [f"<{tag} style> {hit}" for hit in css_findings(value)]
        if tag == "link" and EXTERNAL.match(values.get("href", "")):
            self.found.append(f"<link href={values['href']}>")
        if tag in ("style", "script"):
            self.inside = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self.inside:
            self.inside = None

    def handle_data(self, data: str) -> None:
        if self.inside == "style":
            self.found += [f"<style> {hit}" for hit in css_findings(data)]
        elif self.inside == "script":
            self.found += [f"<script> {hit}" for hit in script_findings(data)]


def findings(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".css":
        return css_findings(text)
    if path.suffix in (".js", ".mjs"):
        return script_findings(text)
    collector = Collector()
    collector.feed(text)
    collector.close()
    return collector.found


def files() -> list[Path]:
    return sorted(p for folder in FOLDERS for p in folder.rglob("*") if p.is_file() and p.suffix in SUFFIXES)


def main() -> int:
    if not (ROOT / "web").is_dir():
        print("FAIL web/ is missing, so nothing was checked")
        return 1
    checked = files()
    if not checked:
        print("FAIL no .html, .css, .js or .j2 file found, so nothing was checked")
        return 1
    problems = [f"{path.relative_to(ROOT).as_posix()}: {hit}" for path in checked for hit in findings(path)]
    for problem in problems:
        print(f"FAIL {problem}")
    if problems:
        print(f"{len(problems)} external resource reference(s)")
        return 1
    print(f"no external resource reference in {len(checked)} files of web/ and src/cleandata/report/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
