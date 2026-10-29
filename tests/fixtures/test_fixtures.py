"""The five fixtures against fixtures/expected/<nn>.json (the generator's planting log) and the golden files.

The expected files come from tools/make_data.py, not from the cleaning code. The golden outputs in
tests/golden/ are the cleaned and rejects CSV files of fixtures 1 to 4; rewrite them with
``uv run pytest tests/fixtures --update-golden`` and review the diff before committing.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from support import FIXTURES, GOLDEN, clean_fixture, expected, invoke, report

SPEC_ROWS = {
    "01": {"read": 1026, "empty": 8, "rejected": 18, "duplicates": 18, "out": 982},
    "02": {"read": 2480, "empty": 0, "rejected": 17, "duplicates": 65, "out": 2398},
    "03": {"read": 907, "empty": 2, "rejected": 0, "duplicates": 5, "out": 900},
    "04": {"read": 602, "empty": 0, "rejected": 2, "duplicates": 0, "out": 600},
    "05": {"read": 50000, "empty": 0, "rejected": 250, "duplicates": 1500, "out": 48250},
}
SPEC_RULES = {
    "01": {
        "start_date.type": 10,
        "respondent_id.required": 1,
        "respondent_id.regex": 2,
        "age.max": 3,
        "age.min": 2,
        "dedupe.exact": 12,
        "dedupe.key.1": 6,
    },
    "02": {
        "country.allowed": 7,
        "year_of_birth.min": 2,
        "year_of_birth.max": 2,
        "email.regex": 6,
        "dedupe.exact": 25,
        "dedupe.key.1": 40,
    },
    "03": {"dedupe.key.1": 5},
    "04": {"read.malformed": 2},
    "05": {"country.allowed": 250, "dedupe.exact": 1000, "dedupe.key.1": 500},
}
SPEC_COLUMNS = {"01": 13, "02": 11, "03": 7, "04": 8, "05": 10}
NEEDS_FUZZY = pytest.mark.needs_fuzzy


def rows_of_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def failed_rules(data: dict[str, Any]) -> dict[str, int]:
    counts = {rule["id"]: rule["failed"] for rule in data["rules"] if rule["failed"]}
    counts.update(
        {rule: count for rule, count in data["rows"]["by_rule"].items() if rule.startswith("read.")}
    )
    return counts


def check_against_expected(number: str, folder: Path) -> dict[str, Any]:
    want = expected(number)
    data = report(folder / f"{number}.report.json")
    rows = {key: data["rows"][key] for key in ("read", "empty", "rejected", "duplicates", "out")}
    assert rows == SPEC_ROWS[number] == want["rows"]
    assert failed_rules(data) == SPEC_RULES[number] == want["rules"]
    assert len(data["columns"]) == SPEC_COLUMNS[number] == want["out_columns"]
    for name, fill in want["fills"].items():
        assert (data["columns"][name]["missing"]["filled"], data["columns"][name]["missing"]["value"]) == (
            fill["filled"],
            fill["value"],
        )
    if "rejects" in want:
        got = sorted(
            (int(row["_source_row"]), row.get("_source_sheet") or None, row["_rule"], row["_detail"])
            for row in rows_of_csv(folder / f"{number}.rejects.csv")
        )
        planted = sorted(
            (entry["source_row"], entry.get("source_sheet"), entry["rule"], entry["duplicate_of"])
            for entry in want["rejects"]
        )
        assert [item[:3] for item in got] == [item[:3] for item in planted]
        for (_, _, _, detail), (_, _, _, winner) in zip(got, planted, strict=True):
            if winner is not None:
                assert detail == f"duplicate of {winner}"
    return data


def check_golden(number: str, folder: Path, update: bool) -> None:
    for kind in ("cleaned", "rejects"):
        produced = (folder / f"{number}.{kind}.csv").read_bytes()
        golden = GOLDEN / f"{number}.{kind}.csv"
        if update:
            golden.parent.mkdir(parents=True, exist_ok=True)
            golden.write_bytes(produced)
        assert golden.is_file(), f"{golden.name} is missing; run pytest tests/fixtures --update-golden"
        assert produced == golden.read_bytes(), f"{golden.name} differs; review, then --update-golden"


def test_fixture_1_survey(tmp_path: Path, update_golden: bool) -> None:
    result, folder = clean_fixture("01", tmp_path)
    assert result.exit_code == 0
    assert result.stdout == "read 1026 · empty 8 · rejected 18 · duplicates 18 · out 982 · exit 0\n"
    data = check_against_expected("01", folder)
    assert data["columns"]["household_income"]["coercion"]["parsed"] == 998
    income, satisfaction = data["columns"]["household_income"], data["columns"]["satisfaction"]
    assert (income["missing"]["strategy"], income["missing"]["filled"], income["missing"]["value"]) == (
        "median",
        20,
        68000.0,
    )
    assert (satisfaction["missing"]["filled"], satisfaction["missing"]["value"]) == (15, 6)
    check_golden("01", folder, update_golden)
    strict, _ = clean_fixture("01", tmp_path / "strict", "--strict")
    assert strict.exit_code == expected("01")["exit_strict"] == 4


@NEEDS_FUZZY
def test_fixture_2_partner(tmp_path: Path, update_golden: bool) -> None:
    result, folder = clean_fixture("02", tmp_path)
    assert result.exit_code == 0
    check_against_expected("02", folder)
    clusters: dict[str, list[str]] = defaultdict(list)
    for row in rows_of_csv(folder / "02.cleaned.csv"):
        if row["dup_cluster"]:
            clusters[row["dup_cluster"]].append(row["panelist_ref"])
    want = expected("02")
    found = sorted(sorted(members) for members in clusters.values())
    assert (len(found), sum(len(members) for members in found)) == (15, 30)
    assert found == sorted(sorted(members) for members in want["clusters"])
    together = {ref: cluster for cluster, members in clusters.items() for ref in members}
    for first, second in want["decoys"]:
        assert together.get(first) is None or together.get(first) != together.get(second)
    check_golden("02", folder, update_golden)


def test_fixture_3_waves(tmp_path: Path, update_golden: bool) -> None:
    result, folder = clean_fixture("03", tmp_path)
    assert result.exit_code == 0
    data = check_against_expected("03", folder)
    want = expected("03")
    assert [sheet["header_row"] for sheet in data["input"]["sheets"]] == [want["header_row"]] * 3 == [4, 4, 4]
    sheets = [sheet["name"] for sheet in data["input"]["sheets"]]
    assert sheets == want["sheets_read"] == ["Wave 1", "Wave 2", "Wave 3"]
    assert data["input"]["ignored_sheets"] == want["sheets_ignored"] == ["Read me", "Lookup"]
    assert data["input"]["footer_skipped"] == want["footer_skipped"] == 3
    assert data["columns"]["total_score"]["coercion"]["nulled"] == want["nulled"]["total_score"] == 3
    assert data["columns"]["mode"]["after"]["nulls"] == want["nulls"]["mode"] == 600
    check_golden("03", folder, update_golden)


def test_fixture_4_legacy(tmp_path: Path, update_golden: bool) -> None:
    result, folder = clean_fixture("04", tmp_path)
    assert result.exit_code == 0
    data = check_against_expected("04", folder)
    want = expected("04")
    assert data["input"]["short_records"] == want["short_records"] == 4
    names = [row["name"] for row in rows_of_csv(folder / "04.cleaned.csv")]
    assert names == want["names"]
    notes = [row["note"] for row in rows_of_csv(folder / "04.cleaned.csv") if "\n" in row["note"]]
    assert len(notes) == 3 and all("\t" in note for note in notes)
    assert [w["code"] for w in data["warnings"]] == ["read.short_record"]
    check_golden("04", folder, update_golden)
    strict, _ = clean_fixture("04", tmp_path / "strict", "--strict")
    assert strict.exit_code == 4


def test_fixture_4_detection_without_a_pinned_encoding() -> None:
    """Spec 4.3: a Western single-byte encoding is detected with a warning; no byte 0x80-0x9F: latin-1."""
    result = invoke(["profile", str(FIXTURES / "04_legacy_partner_latin1.txt")])
    assert result.exit_code == 0
    assert "encoding   latin-1 (detected" in result.stdout
    assert "delimiter  '\\t' (consistency 1.00)" in result.stdout
    warning = r"encoding latin-1 was detected \(confidence [01]\.\d+\); pin it with input\.encoding"
    assert re.search(warning, result.stderr)


def test_fixture_5_large_in_memory_and_chunked(tmp_path: Path, large_fixture: Path) -> None:
    memory, folder = clean_fixture("05", tmp_path / "memory", input_path=large_fixture)
    assert memory.exit_code == 0
    data = check_against_expected("05", folder)
    assert (
        data["columns"]["points"]["missing"]["filled"],
        data["columns"]["points"]["missing"]["value"],
    ) == (
        1000,
        0,
    )
    chunked, chunked_folder = clean_fixture(
        "05", tmp_path / "chunked", "--chunked", "--chunk-size", "7000", input_path=large_fixture
    )
    assert chunked.exit_code == 0
    assert chunked.stdout == memory.stdout
    for kind in ("cleaned", "rejects"):
        assert (chunked_folder / f"05.{kind}.csv").read_bytes() == (folder / f"05.{kind}.csv").read_bytes()
    rows = rows_of_csv(folder / "05.rejects.csv")
    copies = [row for row in rows if row["_rule"] == "dedupe.exact"]
    assert all(
        int(row["_source_row"]) - int(row["_detail"].removeprefix("duplicate of r")) == 7001 for row in copies
    )
