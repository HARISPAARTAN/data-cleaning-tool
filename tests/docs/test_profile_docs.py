from __future__ import annotations

import re
import typing
from collections.abc import Iterator

import pytest
import yaml
from pydantic import BaseModel

from cleandata.core.profile import (
    ColumnRule,
    CsvOutput,
    DedupeOptions,
    FuzzyField,
    FuzzyRule,
    HeaderOptions,
    InputOptions,
    KeyRule,
    Locale,
    LocaleOverride,
    MissingRule,
    OutputOptions,
    Profile,
    QualityGate,
    ReportOptions,
    TextOptions,
    XlsxOutput,
)
from support import ROOT

DOCS = [ROOT / "README.md", ROOT / "README.de.md", *sorted((ROOT / "docs").glob("*.md"))]
YAML_BLOCK = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
BASE = {"version": 1, "name": "docs", "columns": {"respondent_id": {"type": "string"}}}

PREFIXES: dict[type[BaseModel], str] = {
    Profile: "",
    InputOptions: "input.",
    Locale: "input.locale.",
    HeaderOptions: "headers.",
    TextOptions: "text.",
    ColumnRule: "columns.<name>.",
    LocaleOverride: "columns.<name>.locale.",
    MissingRule: "columns.<name>.missing.",
    DedupeOptions: "dedupe.",
    KeyRule: "dedupe.keys[].",
    FuzzyRule: "dedupe.fuzzy.",
    FuzzyField: "dedupe.fuzzy.fields[].",
    QualityGate: "quality.",
    OutputOptions: "output.",
    CsvOutput: "output.csv.",
    XlsxOutput: "output.xlsx.",
    ReportOptions: "report.",
}


def nested_models(annotation: object) -> Iterator[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
    for argument in typing.get_args(annotation):
        yield from nested_models(argument)


def schema_fields() -> list[str]:
    paths: list[str] = []
    for model, prefix in PREFIXES.items():
        for name, info in model.model_fields.items():
            paths.append(prefix + name)
            for inner in nested_models(info.annotation):
                assert inner in PREFIXES, f"{inner.__name__} needs a prefix in PREFIXES"
    return paths


def doc_blocks() -> Iterator[tuple[str, str]]:
    for path in DOCS:
        for number, block in enumerate(YAML_BLOCK.findall(path.read_text(encoding="utf-8")), start=1):
            yield f"{path.name}#{number}", block


@pytest.mark.parametrize(("where", "block"), list(doc_blocks()))
def test_every_yaml_block_in_the_docs_validates(where: str, block: str) -> None:
    loaded = yaml.safe_load(block)
    assert isinstance(loaded, dict), where
    merged = loaded if "version" in loaded else {**BASE, **loaded}
    if "columns" in loaded and "version" not in loaded:
        merged["columns"] = {**BASE["columns"], **loaded["columns"]}
    Profile.model_validate(merged)


def test_profile_reference_has_a_heading_for_every_schema_field() -> None:
    text = (ROOT / "docs" / "profile-reference.md").read_text(encoding="utf-8")
    headings = set(re.findall(r"^#{2,6} `([^`]+)`", text, re.MULTILINE))
    missing = [path for path in schema_fields() if path not in headings]
    assert missing == []
