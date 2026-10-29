"""Seeded generator for the five fixtures, their expected outcomes and the manifest.

    uv run python tools/make_data.py                 # fixtures 1 to 4, expected/*.json, MANIFEST.json
    uv run python tools/make_data.py --large         # also fixture 5 (about 4.5 MB, not committed)
    uv run python tools/make_data.py --check         # regenerate into a temporary folder and compare

Defects are planted at exact counts on disjoint, seeded record sets, and ``fixtures/expected/<nn>.json``
is written from the planting log: row accounting, per-rule counts, fills, rejected rows and cluster
members. That oracle does not use the cleaning code. Names come from the fixed pools in
``tools/name_pool.py``; e-mail addresses use the reserved top-level domain ``.example``
(``inbox.example``, ``mail.example``, ``post.example``, ``northgate.example``). Needs the ``fuzzy``
extra (rapidfuzz checks the near-duplicate and decoy scores).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import random
import statistics
import sys
import tempfile
import unicodedata
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from name_pool import FIRST_NAMES, LAST_NAMES, LOCALES, TOWNS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = 20261029
NBSP = "\N{NO-BREAK SPACE}"
ZWSP = "\N{ZERO WIDTH SPACE}"
ZIP_TIME = (2026, 10, 29, 0, 0, 0)
EXCEL_EPOCH = dt.date(1899, 12, 30)
XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


# --------------------------------------------------------------------------------------------- helpers


@dataclass
class Fixture:
    number: str
    name: str
    data: bytes
    expected: dict[str, Any]
    committed: bool = True


@dataclass
class Record:
    """One planted record: its raw fields, its fate and, for kept rows, the typed values the oracle needs."""

    fields: list[str]
    kind: str = "clean"
    rule: str | None = None
    winner: Record | None = None
    line: int = 0
    sheet: str | None = None
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"r{self.line}" if self.sheet is None else f"r{self.line} ({self.sheet})"


class Names:
    """Seeded draws from the name pools, one random generator per locale."""

    def __init__(self, seed: int) -> None:
        self.rngs = {locale: random.Random(seed + index) for index, locale in enumerate(LOCALES)}

    def first(self, locale: str) -> str:
        return unicodedata.normalize("NFC", self.rngs[locale].choice(FIRST_NAMES[locale]))

    def last(self, locale: str) -> str:
        return unicodedata.normalize("NFC", self.rngs[locale].choice(LAST_NAMES[locale]))

    def city(self, locale: str) -> str:
        return unicodedata.normalize("NFC", self.rngs[locale].choice(TOWNS[locale]))


FOLD = str.maketrans(
    {"ø": "o", "Ø": "O", "æ": "ae", "Æ": "Ae", "ß": "ss", "ł": "l", "Ł": "L", "đ": "d", "œ": "oe"}
)


def ascii_fold(text: str) -> str:
    """Lower-case ASCII letters and digits only: the local part of an invented e-mail address."""
    decomposed = unicodedata.normalize("NFKD", text.translate(FOLD))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(ch for ch in plain.lower() if ch.isascii() and ch.isalnum())


def write_csv(rows: Sequence[Sequence[str]], delimiter: str, newline: str) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator=newline, quoting=csv.QUOTE_MINIMAL)
    writer.writerows(rows)
    return buffer.getvalue()


def number_lines(records: Sequence[Record], first_line: int = 2) -> None:
    """Physical start line of every record, counting line breaks inside quoted fields."""
    line = first_line
    for record in records:
        record.line = line
        line += 1 + sum(value.count("\n") for value in record.fields)


def german(value: float, decimals: bool) -> str:
    text = f"{value:,.2f}" if decimals else f"{int(value):,}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def reject_entry(record: Record) -> dict[str, Any]:
    entry: dict[str, Any] = {"source_row": record.line}
    if record.sheet is not None:
        entry["source_sheet"] = record.sheet
    entry["rule"] = record.rule
    entry["duplicate_of"] = None if record.winner is None else record.winner.label
    return entry


def removed(records: Sequence[Record]) -> list[Record]:
    return [r for r in records if r.kind in ("rejected", "duplicate")]


def kept(records: Sequence[Record]) -> list[Record]:
    return [r for r in records if r.kind == "clean"]


def rule_counts(records: Sequence[Record]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in removed(records):
        assert record.rule is not None
        counts[record.rule] = counts.get(record.rule, 0) + 1
    return dict(sorted(counts.items()))


def place_after(rng: random.Random, records: list[Record], original: Record, copy: Record) -> None:
    index = next(i for i, r in enumerate(records) if r is original)
    records.insert(rng.randint(index + 1, len(records)), copy)


# --------------------------------------------------------------------------------------- 1: survey export

SURVEY_HEADER = [
    "Respondent ID",
    "Welle",
    "Startdatum",
    "Abgeschlossen am",
    "Alter",
    "Geschlecht",
    "Land",
    "Haushaltseinkommen (€)",
    "Ausgaben letzter Monat (€)",
    "Zufriedenheit",
    "NPS",
    "Kommentar",
    "Kommentar",
    "",
]
GENDERS = ("w", "W", "weiblich", "m", "männlich", "divers", "keine Angabe")
COUNTRIES_DACH = ("Deutschland", "DE", "de", "Österreich", "AT", "Schweiz")
DATE_FORMATS = (("%d.%m.%Y", 6), ("%Y-%m-%d", 3), ("%d/%m/%Y", 2))
COMMENTS = (
    "Sehr zufrieden mit dem Service",
    "Lieferung verspätet, sonst gut",
    "Fragebogen war zu lang",
    "Gerne wieder",
    "Keine Anmerkungen",
    "Preis und Leistung stimmen",
    "Mehr Auswahl wäre schön",
    "Alles prima",
)
NOTES = ("Rückruf vereinbart", "Telefon besetzt,  zweiter Versuch", "Teilnahme per Tablet")
MULTILINE_NOTES = (
    "Rückruf vereinbart\r\nabends erreichbar",
    "Zwei Anläufe:\r\nerst online, dann am Telefon",
    "Interview unterbrochen\r\nam Folgetag beendet",
)
INVALID_DATES = (
    "31.02.2025",
    "2025-13-01",
    "gestern",
    "30.02.2026",
    "2025-02-30",
    "32.01.2025",
    "heute",
    "15/13/2025",
    "00.05.2025",
    "2025-00-10",
)
COPY_ORIGINAL_COMMENT = "Lieferung verspätet, sonst gut"


def _survey_person(rng: random.Random, number: int) -> dict[str, Any]:
    start = dt.date(2025, 1, 6) + dt.timedelta(days=rng.randrange(0, 600))
    completed = dt.datetime.combine(start, dt.time(rng.randrange(7, 22), rng.randrange(60)))
    completed += dt.timedelta(days=rng.randrange(0, 3))
    formats, weights = zip(*DATE_FORMATS, strict=True)
    return {
        "id": f"NG-{number:06d}",
        "wave": rng.randint(1, 4),
        "start": start.strftime(rng.choices(formats, weights)[0]),
        "completed": completed,
        "age": str(rng.randint(18, 85)),
        "gender": rng.choice(GENDERS),
        "country": rng.choice(COUNTRIES_DACH),
        "income": float(rng.randrange(36, 240) * 500),
        "income_decimals": rng.random() < 0.5,
        "spend": rng.randrange(1_000, 250_000) / 100,
        "satisfaction": rng.randint(1, 10),
        "nps": rng.randint(0, 10),
        "comment": rng.choice(COMMENTS) if rng.random() < 0.6 else "",
        "note": rng.choice(NOTES) if rng.random() < 0.25 else "",
    }


def _survey_fields(p: dict[str, Any]) -> list[str]:
    income = "" if p["income"] is None else german(p["income"], p["income_decimals"])
    spend = german(p["spend"], True) + NBSP + "€"
    return [
        p["id"],
        str(p["wave"]),
        p["start"],
        p["completed"].strftime("%d.%m.%Y %H:%M"),
        p["age"],
        p["gender"],
        p["country"],
        income,
        spend,
        "" if p["satisfaction"] is None else str(p["satisfaction"]),
        str(p["nps"]),
        p["comment"],
        p["note"],
        "",
    ]


def survey_fixture(seed: int) -> Fixture:
    rng = random.Random(seed * 10 + 1)
    numbers = rng.sample(range(100_000, 1_000_000), 1_000)
    people = [_survey_person(rng, number) for number in numbers]
    total = 1_026
    slots: list[Record | None] = [None] * total

    def record(person: dict[str, Any], kind: str = "clean", rule: str | None = None) -> Record:
        return Record(_survey_fields(person), kind, rule, values=person)

    pool = iter(people)
    # Forced positions make the explain examples of docs/cli.md reproducible.
    first, second = next(pool), next(pool)
    first.update(income=45_000.0, income_decimals=True)
    second.update(income=38_500.0, income_decimals=False)
    slots[0], slots[1] = record(first), record(second)
    for position, age in ((116, "130"), (542, "150"), (869, "101")):
        person = next(pool)
        person["age"] = age
        slots[position] = record(person, "rejected", "age.max")
    earlier = next(pool)
    later = dict(
        earlier,
        completed=earlier["completed"] + dt.timedelta(days=rng.randrange(5, 60)),
        satisfaction=rng.randint(1, 10),
    )
    slots[38] = record(earlier, "duplicate", "dedupe.key.1")
    slots[900] = record(later)
    slots[38].winner = slots[900]
    free = [position for position in range(total) if slots[position] is None]
    rng.shuffle(free)
    for _ in range(5):
        a, b = sorted((free.pop(), free.pop()))
        person = next(pool)
        resubmitted = dict(
            person,
            completed=person["completed"] + dt.timedelta(days=rng.randrange(5, 60)),
            satisfaction=rng.randint(1, 10),
        )
        slots[b] = record(resubmitted)
        slots[a] = record(person, "duplicate", "dedupe.key.1")
        slots[a].winner = slots[b]
    copies: list[tuple[Record, Record]] = []
    for _ in range(12):
        a, b = sorted((free.pop(), free.pop()))
        person = next(pool)
        person["comment"] = COPY_ORIGINAL_COMMENT
        original = record(person)
        copy = Record(list(original.fields), "duplicate", "dedupe.exact", winner=original)
        slots[a], slots[b] = original, copy
        copies.append((original, copy))
    # Four copies differ only in whitespace, a no-break space or an NFD umlaut.
    copies[0][1].fields[11] = COPY_ORIGINAL_COMMENT.replace(", ", ",  ")
    copies[1][1].fields[11] = COPY_ORIGINAL_COMMENT.replace("sonst gut", "sonst" + NBSP + "gut")
    copies[2][1].fields[11] = unicodedata.normalize("NFD", COPY_ORIGINAL_COMMENT)
    copies[3][1].fields[5] = "  " + copies[3][1].fields[5] + " "
    for _ in range(8):
        slots[free.pop()] = Record([""] * len(SURVEY_HEADER), "empty")
    defects: list[tuple[str, Callable[[dict[str, Any]], None]]] = []
    for value in INVALID_DATES:
        defects.append(("start_date.type", lambda p, v=value: p.update(start=v)))
    defects.append(("respondent_id.required", lambda p: p.update(id="")))
    defects.append(("respondent_id.regex", lambda p: p.update(id=p["id"][:-1])))
    defects.append(("respondent_id.regex", lambda p: p.update(id=p["id"] + "7")))
    defects.append(("age.min", lambda p: p.update(age="12")))
    defects.append(("age.min", lambda p: p.update(age="15")))
    for rule, change in defects:
        person = next(pool)
        change(person)
        slots[free.pop()] = record(person, "rejected", rule)
    for position in free:
        slots[position] = record(next(pool))
    records = [slot for slot in slots if slot is not None]
    assert len(records) == total
    originals = {id(original) for original, _ in copies}
    plain = [r for r in records[2:] if r.kind == "clean" and id(r) not in originals]
    rng.shuffle(plain)
    late = [r for r in plain if records.index(r) > 902]
    for r, note in zip(late[:3], MULTILINE_NOTES, strict=True):
        r.fields[12] = note
    plain = [r for r in plain if r not in late[:3]]
    for r in plain[:20]:
        r.values["income"] = None
        r.fields[7] = ""
    for r in plain[20:35]:
        r.values["satisfaction"] = None
        r.fields[9] = ""
    id_variants: tuple[Callable[[str], str], ...] = (
        str.lower,
        lambda v: f" {v} ",
        lambda v: "Ng" + v[2:],
        lambda v: v + " ",
        lambda v: " ng" + v[2:],
    )
    for r, change in zip(plain[35:40], id_variants, strict=True):
        r.fields[0] = change(r.fields[0])
    for r in plain[40:43]:
        r.fields[4] = "k.A."
    for r in plain[43:48]:
        text = r.fields[11] or "Gerne wieder"
        cut = text.index(" ") if " " in text else len(text)
        r.fields[11] = text[:cut] + ZWSP + text[cut:]
    number_lines(records)
    survivors = kept(records)
    incomes = [r.values["income"] for r in survivors if r.values["income"] is not None]
    scores = [r.values["satisfaction"] for r in survivors if r.values["satisfaction"] is not None]
    text = write_csv([SURVEY_HEADER] + [r.fields for r in records], ";", "\r\n")
    expected = {
        "fixture": "01_survey_export_de.csv",
        "profile": "01_survey.yaml",
        "rows": {"read": 1_026, "empty": 8, "rejected": 18, "duplicates": 18, "out": 982},
        "out_columns": 13,
        "rules": rule_counts(records),
        "fills": {
            "household_income": {"filled": 20, "value": float(statistics.median(incomes))},
            "satisfaction": {"filled": 15, "value": statistics.median_low(scores)},
        },
        "rejects": sorted((reject_entry(r) for r in removed(records)), key=lambda e: e["source_row"]),
        "exit": 0,
        "exit_strict": 4,
    }
    _check_rows(expected, records)
    return Fixture("01", expected["fixture"], text.encode("utf-8-sig"), expected)


def _check_rows(expected: dict[str, Any], records: Sequence[Record]) -> None:
    rows = expected["rows"]
    counted = {
        "read": len(records),
        "empty": sum(1 for r in records if r.kind == "empty"),
        "rejected": sum(1 for r in records if r.kind == "rejected"),
        "duplicates": sum(1 for r in records if r.kind == "duplicate"),
        "out": len(kept(records)),
    }
    if counted != rows:
        raise AssertionError(f"{expected['fixture']}: planted {counted}, expected {rows}")


# ------------------------------------------------------------------------------------ 2: partner panels

PARTNER_HEADER = [
    "PartnerID",
    "PanelistRef",
    "First Name",
    "Last Name",
    "Email",
    "Country",
    "Year of birth",
    "Points",
    "Opt-in",
    "Joined",
]
COUNTRY_SPELLINGS = {
    "GB": ("GB", "UK", "United Kingdom", "Great Britain"),
    "IE": ("IE", "Ireland", "Éire"),
    "DE": ("DE", "Germany", "Deutschland"),
    "NL": ("NL", "Netherlands", "The Netherlands", "Holland"),
}
COUNTRY_LOCALES = {"GB": "en_GB", "IE": "en_IE", "DE": "de_DE", "NL": "nl_NL"}
COUNTRY_WEIGHTS = (("GB", 40), ("IE", 15), ("DE", 25), ("NL", 20))
UNKNOWN_COUNTRIES = ("France", "Spain", "Denmark", "Belgium", "Portugal", "Austria", "Iceland")
OPT_IN = ("Y", "N", "yes", "no", "1", "0", "TRUE")
DOMAINS = ("inbox.example", "mail.example", "post.example")
BAD_EMAILS = (
    "{local}@mail",
    "{local}@@mail.example",
    "{local}.inbox.example",
    "{local} x@post.example",
    "{local}@mail.e",
    "@post.example",
)
BAD_YEARS = (1910, 1899, 2014, 2020)


class Panel:
    """People for the partner layout: unique references and e-mail addresses."""

    def __init__(self, rng: random.Random, names: Names) -> None:
        self.rng = rng
        self.names = names
        self.refs: set[str] = set()
        self.emails: set[str] = set()

    def ref(self, panel: str) -> str:
        while True:
            ref = f"{panel}-{self.rng.randrange(10_000, 100_000)}"
            if ref not in self.refs:
                self.refs.add(ref)
                return ref

    def email(self, first: str, last: str) -> str:
        while True:
            address = (
                f"{ascii_fold(first)}.{ascii_fold(last)}{self.rng.randrange(1, 1_000)}"
                f"@{self.rng.choice(DOMAINS)}"
            )
            if address not in self.emails:
                self.emails.add(address)
                return address

    def person(self, country: str | None = None) -> dict[str, Any]:
        rng = self.rng
        codes, weights = zip(*COUNTRY_WEIGHTS, strict=True)
        code = country or rng.choices(codes, weights)[0]
        locale = COUNTRY_LOCALES[code]
        first, last = self.names.first(locale), self.names.last(locale)
        panel = rng.choice(("PA", "PB"))
        joined = dt.date(2019, 1, 1) + dt.timedelta(days=rng.randrange(0, 2_800))
        return {
            "panel": panel,
            "ref": self.ref(panel),
            "first": first,
            "last": last,
            "email": self.email(first, last),
            "code": code,
            "country": rng.choice(COUNTRY_SPELLINGS[code]),
            "yob": rng.randint(1940, 2005),
            "points": rng.randrange(0, 25_000),
            "opt_in": rng.choice(OPT_IN),
            "joined": joined,
            "joined_format": "%Y-%m-%d" if rng.random() < 0.7 else "%d/%m/%Y",
        }

    def rename(self, person: dict[str, Any]) -> None:
        locale = COUNTRY_LOCALES[person["code"]]
        self.emails.discard(person["email"])
        person["first"], person["last"] = self.names.first(locale), self.names.last(locale)
        person["email"] = self.email(person["first"], person["last"])


def partner_fields(p: dict[str, Any]) -> list[str]:
    email = p["email"]
    style = p.get("email_style", 0)
    if style == 1:
        email = email.capitalize()
    elif style == 2:
        email = "  " + email.upper() + " "
    return [
        p["panel"],
        p["ref"],
        p["first"],
        p["last"],
        email,
        p["country"],
        str(p["yob"]),
        "" if p["points"] is None else f"{p['points']:,}",
        p["opt_in"],
        p["joined"].strftime(p["joined_format"]),
    ]


def full_name(person: dict[str, Any]) -> str:
    text = unicodedata.normalize("NFKD", f"{person['first']} {person['last']}".casefold())
    return " ".join("".join(ch for ch in text if not unicodedata.combining(ch)).split())


def _typo(rng: random.Random, name: str) -> str:
    """Change one interior letter (never a token's first letter, so the token order cannot change)."""
    positions = [
        i for i in range(1, len(name)) if name[i].isascii() and name[i].isalpha() and name[i - 1] != " "
    ]
    position = rng.choice(positions)
    letters = [ch for ch in "abcdefghijklmnopqrstuvwxyz" if ch != name[position].lower()]
    return name[:position] + rng.choice(letters) + name[position + 1 :]


def partner_fixture(seed: int) -> Fixture:
    from rapidfuzz import fuzz, process

    rng = random.Random(seed * 10 + 2)
    panel = Panel(rng, Names(seed + 200))
    records = []
    for _ in range(2_363):
        person = panel.person()
        person["email_style"] = rng.choices((0, 1, 2), (8, 1, 1))[0]
        records.append(Record(partner_fields(person), values=person))
    special: set[int] = set()
    # 40 people in both panels: same e-mail, other panel reference, joined later; keep first by joined.
    pairs: list[tuple[Record, Record]] = []
    for original in rng.sample(records, 40):
        special.add(id(original))
        p = original.values
        other = "PB" if p["panel"] == "PA" else "PA"
        twin = dict(
            p,
            panel=other,
            ref=panel.ref(other),
            email_style=rng.choice((0, 1, 2)),
            joined=p["joined"] + dt.timedelta(days=rng.randrange(30, 900)),
            country=rng.choice(COUNTRY_SPELLINGS[p["code"]]),
        )
        pairs.append((original, Record([], "duplicate", "dedupe.key.1", winner=original, values=twin)))
    # 15 near-duplicates: one interior letter changed in a full name of 11+ characters, or names swapped.
    near: list[tuple[Record, Record]] = []
    candidates = [r for r in records if id(r) not in special and len(full_name(r.values)) >= 11]
    for number, original in enumerate(rng.sample(candidates, 15)):
        special.add(id(original))
        p = original.values
        while True:
            if number % 3 == 2:
                first, last = p["last"], p["first"]
            elif rng.random() < 0.5 and len(p["first"]) > 2:
                first, last = _typo(rng, p["first"]), p["last"]
            else:
                first, last = p["first"], _typo(rng, p["last"])
            changed = dict(p, first=first, last=last)
            if fuzz.token_sort_ratio(full_name(p), full_name(changed)) >= 90:
                break
        other = rng.choice(("PA", "PB"))
        twin = dict(
            changed,
            panel=other,
            ref=panel.ref(other),
            email=panel.email(first, last),
            email_style=0,
            country=rng.choice(COUNTRY_SPELLINGS[p["code"]]),
        )
        planted = Record([], values=twin)
        special.add(id(planted))
        near.append((original, planted))
    # 20 decoy pairs: similar names, another year of birth, so another block.
    decoys: list[tuple[Record, Record]] = []
    for original in rng.sample([r for r in records if id(r) not in special], 20):
        p = original.values
        offsets = (-3, -2, -1) if p["yob"] > 2001 else (1, 2, 3)
        last = _typo(rng, p["last"]) if len(p["last"]) > 2 and rng.random() < 0.5 else p["last"]
        twin = dict(
            p,
            last=last,
            ref=panel.ref(p["panel"]),
            email=panel.email(p["first"], last),
            email_style=0,
            yob=p["yob"] + rng.choice(offsets),
        )
        decoy = Record([], values=twin)
        records.append(decoy)
        decoys.append((original, decoy))
        special.update((id(original), id(decoy)))
    _redraw(panel, records + [twin for _, twin in near], near, special, fuzz, process)
    for original, twin in near:
        score = fuzz.token_sort_ratio(full_name(original.values), full_name(twin.values))
        assert score >= 90, f"planted near-duplicate {original.values['ref']} scores {score}"
    rejected: list[Record] = []
    for value in UNKNOWN_COUNTRIES:
        p = panel.person()
        p["country"] = value
        rejected.append(Record([], "rejected", "country.allowed", values=p))
    for year in BAD_YEARS:
        p = panel.person()
        p["yob"] = year
        rejected.append(
            Record([], "rejected", "year_of_birth.min" if year < 1925 else "year_of_birth.max", values=p)
        )
    for template in BAD_EMAILS:
        p = panel.person()
        p["email"] = template.format(local=f"{ascii_fold(p['first'])}.{ascii_fold(p['last'])}")
        rejected.append(Record([], "rejected", "email.regex", values=p))
    ordered = records + [twin for _, twin in near] + [twin for _, twin in pairs] + rejected
    for record in ordered:
        record.fields = partner_fields(record.values)
    rng.shuffle(ordered)
    for original in rng.sample([r for r in records if id(r) not in special], 25):
        place_after(
            rng,
            ordered,
            original,
            Record(list(original.fields), "duplicate", "dedupe.exact", winner=original),
        )
    number_lines(ordered)
    text = write_csv([PARTNER_HEADER] + [r.fields for r in ordered], ",", "\n")
    clusters = sorted(
        (sorted(pair, key=lambda r: r.line) for pair in near), key=lambda members: members[0].line
    )
    expected = {
        "fixture": "02_partner_respondents.csv",
        "profile": "02_partner.yaml",
        "rows": {"read": 2_480, "empty": 0, "rejected": 17, "duplicates": 65, "out": 2_398},
        "out_columns": 11,
        "rules": rule_counts(ordered),
        "fills": {},
        "rejects": sorted((reject_entry(r) for r in removed(ordered)), key=lambda e: e["source_row"]),
        "clusters": [[m.values["ref"] for m in members] for members in clusters],
        "decoys": [[a.values["ref"], b.values["ref"]] for a, b in decoys],
        "exit": 0,
        "exit_strict": 4,
    }
    _check_rows(expected, ordered)
    return Fixture("02", expected["fixture"], text.encode("utf-8"), expected)


def _redraw(
    panel: Panel,
    rows: list[Record],
    near: list[tuple[Record, Record]],
    special: set[int],
    fuzz: Any,
    process: Any,
) -> None:
    """Re-draw plain people until no same-block pair outside the planted ones scores 85 or more."""
    planted = {frozenset((id(a), id(b))) for a, b in near}
    for _ in range(50):
        blocks: dict[tuple[str, int], list[Record]] = {}
        for record in rows:
            blocks.setdefault((record.values["code"], record.values["yob"]), []).append(record)
        changed = False
        for members in blocks.values():
            if len(members) < 2:
                continue
            names = [full_name(m.values) for m in members]
            scores = process.cdist(names, names, scorer=fuzz.token_sort_ratio)
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    if scores[i][j] < 85 or frozenset((id(members[i]), id(members[j]))) in planted:
                        continue
                    victim = next((m for m in (members[j], members[i]) if id(m) not in special), None)
                    if victim is None:
                        raise AssertionError("two planted rows collide; change the seed")
                    panel.rename(victim.values)
                    changed = True
        if not changed:
            return
    raise AssertionError("re-drawing did not converge")


# ------------------------------------------------------------------------------------ 3: wave workbook

REGIONS = (
    "North East",
    "North West",
    "Yorkshire and the Humber",
    "East Midlands",
    "West Midlands",
    "East of England",
    "London",
    "South East",
    "South West",
    "Wales",
    "Scotland",
    "Northern Ireland",
)
WAVE_HEADER = ["Respondent ID", "Interview date", "Region", "Score", "Total score"]
Cell = tuple[str, Any] | None


def _col(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _cell_xml(cell: Cell) -> str:
    # Cells carry no ``r`` reference: readers count them along the row, and an empty cell is ``<c/>``.
    if cell is None:
        return "<c/>"
    kind, value = cell
    if kind == "s":
        return f'<c t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'
    if kind == "n":
        return f"<c><v>{value}</v></c>"
    if kind == "d":
        return f'<c s="1"><v>{(value - EXCEL_EPOCH).days}</v></c>'
    if kind == "f":
        formula, cached = value
        return f"<c><f>{escape(formula)}</f><v>{cached}</v></c>"
    if kind == "e":
        return f'<c t="e"><f>{escape(value)}</f><v>#DIV/0!</v></c>'
    if kind == "h":
        return f'<c s="2" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
    raise ValueError(kind)


def _sheet_xml(rows: Sequence[Sequence[Cell]]) -> str:
    width = max((len(row) for row in rows), default=1)
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{_col(width - 1)}{len(rows)}"/><sheetData>'
    ]
    for number, row in enumerate(rows, start=1):
        used = len(row)
        while used and row[used - 1] is None:
            used -= 1
        if used:
            cells = "".join(_cell_xml(cell) for cell in row[:used])
            parts.append(f'<row r="{number}">{cells}</row>')
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def write_xlsx(sheets: Sequence[tuple[str, Sequence[Sequence[Cell]]]]) -> bytes:
    """A minimal OOXML workbook: inline strings, a dd/mm/yyyy date style, cached formula values, stored
    uncompressed with fixed timestamps so that the same sheets always give the same bytes."""
    ns = "http://schemas.openxmlformats.org"
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    files = {
        "[Content_Types].xml": (
            XML_HEAD + f'<Types xmlns="{ns}/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f"{overrides}</Types>"
        ),
        "_rels/.rels": (
            XML_HEAD + f'<Relationships xmlns="{ns}/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{ns}/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            XML_HEAD + f'<workbook xmlns="{ns}/spreadsheetml/2006/main" '
            f'xmlns:r="{ns}/officeDocument/2006/relationships"><sheets>'
            + "".join(
                f'<sheet name={quoteattr(name)} sheetId="{i}" r:id="rId{i}"/>'
                for i, (name, _) in enumerate(sheets, start=1)
            )
            + "</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            XML_HEAD
            + f'<Relationships xmlns="{ns}/package/2006/relationships">'
            + "".join(
                f'<Relationship Id="rId{i}" Type="{ns}/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{i}.xml"/>'
                for i in range(1, len(sheets) + 1)
            )
            + f'<Relationship Id="rId{len(sheets) + 1}" Type="{ns}/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/></Relationships>'
        ),
        "xl/styles.xml": (
            XML_HEAD + f'<styleSheet xmlns="{ns}/spreadsheetml/2006/main">'
            '<numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/></numFmts>'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            "</styleSheet>"
        ),
    }
    for i, (_, rows) in enumerate(sheets, start=1):
        files[f"xl/worksheets/sheet{i}.xml"] = _sheet_xml(rows)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            archive.writestr(info, content.encode("utf-8"))
    return buffer.getvalue()


def wave_fixture(seed: int) -> Fixture:
    rng = random.Random(seed * 10 + 3)
    numbers = iter(rng.sample(range(200_000, 1_000_000), 905))
    sheets: list[tuple[str, list[list[Cell]]]] = []
    all_records: list[Record] = []
    read_me: list[list[Cell]] = [
        [("s", "Wave workbook: three interview waves of the regional panel study")],
        [("s", "One sheet per wave; scores run from 0 to 10 and Total score is Score times 10.")],
        [("s", "Questions about the fieldwork: fieldwork@northgate.example")],
    ]
    sheets.append(("Read me", read_me))
    starts = {"Wave 1": dt.date(2026, 2, 2), "Wave 2": dt.date(2026, 5, 4), "Wave 3": dt.date(2026, 8, 3)}
    nulled = 0
    for wave, start in starts.items():
        header: list[Cell] = [("h", name) for name in WAVE_HEADER + (["Mode"] if wave == "Wave 3" else [])]
        rows: list[list[Cell]] = [
            [("s", f"Northgate Panel Research · {wave} interviews")],
            [("s", "Exported from the survey platform; one row per completed interview")],
            [],
            header,
        ]
        people = []
        for _ in range(300):
            people.append(
                {
                    "id": f"NG-{next(numbers):06d}",
                    "date": start + dt.timedelta(days=rng.randrange(0, 40)),
                    "region": rng.choice(REGIONS),
                    "score": rng.randint(0, 10),
                    "mode": rng.choice(("online", "phone")),
                }
            )
        text_scores = set(rng.sample(range(300), 40)) if wave == "Wave 1" else set()
        text_dates = set(rng.sample(range(300), 60)) if wave == "Wave 2" else set()
        errors = set(rng.sample(range(240), 3)) if wave == "Wave 2" else set()
        reentered = sorted(rng.sample(range(240), 5)) if wave == "Wave 2" else []
        while errors & set(reentered):
            errors = set(rng.sample(range(240), 3))
        records: list[Record] = []
        layout: list[tuple[dict[str, Any], int, bool]] = [(p, i, False) for i, p in enumerate(people)]
        for index in reentered:
            corrected = dict(people[index], score=(people[index]["score"] + rng.randint(1, 3)) % 11)
            layout.append((corrected, index, True))
        blanks = sorted(rng.sample(range(20, 280), 2)) if wave == "Wave 1" else []
        for position in reversed(blanks):
            layout.insert(position, ({}, -1, False))
        for p, index, again in layout:
            number = len(rows) + 1
            if not p:
                rows.append([])
                all_records.append(Record([], "empty", line=number, sheet=wave))
                continue
            date_cell: Cell = (
                ("s", p["date"].strftime("%d.%m.%Y")) if index in text_dates else ("d", p["date"])
            )
            score_cell: Cell = ("s", str(p["score"])) if index in text_scores else ("n", p["score"])
            if index in errors and not again:
                total_cell: Cell = ("e", f"D{number}*100/J{number}")
                nulled += 1
            else:
                total_cell = ("f", (f"D{number}*100/10", p["score"] * 10))
            row: list[Cell] = [("s", p["id"]), date_cell, ("s", p["region"]), score_cell, total_cell]
            if wave == "Wave 3":
                row.append(("s", p["mode"]))
            rows.append(row)
            record = Record([], line=number, sheet=wave, values=p)
            records.append(record)
            all_records.append(record)
        for index in reentered:
            first = next(r for r in records if r.values is people[index])
            later = next(r for r in records if r.values["id"] == people[index]["id"] and r is not first)
            first.kind, first.rule, first.winner = "duplicate", "dedupe.key.1", later
        last = len(rows)
        total = sum(p["score"] for p, _, _ in layout if p)
        rows.append([("s", "Total"), None, None, ("f", (f"SUM(D5:D{last})", total))])
        sheets.append((wave, rows))
    lookup: list[list[Cell]] = [[("h", "Region code"), ("h", "Region")]]
    lookup += [[("s", f"R{i:02d}"), ("s", region)] for i, region in enumerate(REGIONS, start=1)]
    sheets.append(("Lookup", lookup))
    data = write_xlsx(sheets)
    expected = {
        "fixture": "03_wave_workbook.xlsx",
        "profile": "03_waves.yaml",
        "rows": {"read": 907, "empty": 2, "rejected": 0, "duplicates": 5, "out": 900},
        "out_columns": 7,
        "rules": rule_counts(all_records),
        "fills": {},
        "rejects": sorted(
            (reject_entry(r) for r in removed(all_records)),
            key=lambda e: (e["source_sheet"], e["source_row"]),
        ),
        "sheets_read": ["Wave 1", "Wave 2", "Wave 3"],
        "sheets_ignored": ["Read me", "Lookup"],
        "footer_skipped": 3,
        "nulled": {"total_score": nulled},
        "nulls": {"mode": 600},
        "header_row": 4,
        "exit": 0,
        "exit_strict": 4,
    }
    _check_rows(expected, all_records)
    return Fixture("03", expected["fixture"], data, expected)


# ---------------------------------------------------------------------------------- 4: legacy Latin-1

LEGACY_HEADER = ["PartnerRef", "Name", "City", "Country", "Amount (£)", "Paid on", "Active", "Note", ""]
LEGACY_COUNTRIES = {
    "es_ES": "Spain",
    "fr_FR": "France",
    "de_DE": "Germany",
    "da_DK": "Denmark",
    "en_GB": "United Kingdom",
    "en_IE": "Ireland",
}
LEGACY_NOTES = ("Invoice sent", "Paid by cheque", "Reminder  sent twice", "Address changed")
LEGACY_MULTILINE = (
    "Part payment:\tsee ledger\r\nbalance due in March",
    "Two invoices\tmerged\r\nconfirmed by phone",
    "Refund issued:\tcredit note 14\r\nclosed",
)
FORCED_LETTERS = (("é", "fr_FR"), ("ñ", "es_ES"), ("ü", "de_DE"), ("ø", "da_DK"), ("ç", "fr_FR"))


def _latin1(text: str) -> bool:
    try:
        encoded = text.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return not any(0x80 <= byte <= 0x9F for byte in encoded)


def legacy_fixture(seed: int) -> Fixture:
    rng = random.Random(seed * 10 + 4)
    names = Names(seed + 400)
    locales = tuple(LEGACY_COUNTRIES)
    refs = iter(rng.sample(range(10_000, 100_000), 602))

    def draw(locale: str, letter: str | None = None) -> str:
        for _ in range(5_000):
            name = f"{names.first(locale)} {names.last(locale)}"
            if _latin1(name) and (letter is None or letter in name):
                return name
        raise AssertionError(f"no {locale} name with {letter!r}")

    def city(locale: str) -> str:
        for _ in range(1_000):
            value = names.city(locale)
            if _latin1(value):
                return value
        raise AssertionError(f"no Latin-1 city for {locale}")

    records: list[Record] = []
    for number in range(600):
        if number < len(FORCED_LETTERS):
            letter, locale = FORCED_LETTERS[number]
            name = draw(locale, letter)
        else:
            locale = rng.choice(locales)
            name = draw(locale)
        paid = dt.date(2025, 1, 1) + dt.timedelta(days=rng.randrange(0, 640))
        note = rng.choice(LEGACY_NOTES) if rng.random() < 0.2 else ""
        fields = [
            f"PB-{next(refs)}",
            name,
            city(locale),
            LEGACY_COUNTRIES[locale],
            f"£{rng.randrange(500, 999_999) / 100:,.2f}",
            paid.strftime("%d/%m/%Y"),
            rng.choice(("Y", "N")),
            note,
            "",
        ]
        records.append(Record(fields, values={"name": name}))
    for record, note in zip(rng.sample(records[5:], 3), LEGACY_MULTILINE, strict=True):
        record.fields[7] = note
    plain = [r for r in records if "\n" not in r.fields[7]]
    for record in rng.sample(plain, 4):
        record.fields = record.fields[: rng.choice((6, 7))]
    for _ in range(2):
        locale = rng.choice(locales)
        fields = [
            f"PB-{next(refs)}",
            draw(locale),
            city(locale),
            LEGACY_COUNTRIES[locale],
            "£12.00",
            "01/03/2026",
            "Y",
            "Paid twice",
            "",
            "duplicate payment",
            "see letter",
        ]
        records.insert(rng.randrange(10, len(records)), Record(fields, "rejected", "read.malformed"))
    number_lines(records)
    lines = []
    for record in records:
        lines.append(record.fields)
    text = write_csv([LEGACY_HEADER] + lines, "\t", "\r\n")
    data = text.encode("latin-1")
    if any(0x80 <= byte <= 0x9F for byte in data):
        raise AssertionError("the legacy file must not contain bytes 0x80 to 0x9F")
    expected = {
        "fixture": "04_legacy_partner_latin1.txt",
        "profile": "04_legacy.yaml",
        "rows": {"read": 602, "empty": 0, "rejected": 2, "duplicates": 0, "out": 600},
        "out_columns": 8,
        "rules": rule_counts(records),
        "fills": {},
        "rejects": sorted((reject_entry(r) for r in removed(records)), key=lambda e: e["source_row"]),
        "short_records": 4,
        "names": [r.values["name"] for r in kept(records)],
        "exit": 0,
        "exit_strict": 4,
    }
    _check_rows(expected, records)
    return Fixture("04", expected["fixture"], data, expected)


# --------------------------------------------------------------------------------------- 5: large panel


def large_fixture(seed: int) -> Fixture:
    rng = random.Random(seed * 10 + 5)
    panel = Panel(rng, Names(seed + 500))
    total = 50_000
    slots: list[Record | None] = [None] * total
    copies = 0
    while copies < 1_000:
        position = rng.randrange(0, total - 7_001)
        if slots[position] is None and slots[position + 7_001] is None:
            original = Record([], values=panel.person())
            slots[position] = original
            slots[position + 7_001] = Record([], "duplicate", "dedupe.exact", winner=original)
            copies += 1
    free = [position for position in range(total) if slots[position] is None]
    rng.shuffle(free)
    for _ in range(500):
        a, b = sorted((free.pop(), free.pop()))
        person = panel.person()
        other = "PB" if person["panel"] == "PA" else "PA"
        twin = dict(
            person, panel=other, ref=panel.ref(other), joined=person["joined"] + dt.timedelta(days=40)
        )
        slots[a] = Record([], values=person)
        slots[b] = Record([], "duplicate", "dedupe.key.1", winner=slots[a], values=twin)
    for _ in range(250):
        person = panel.person()
        person["country"] = rng.choice(UNKNOWN_COUNTRIES)
        slots[free.pop()] = Record([], "rejected", "country.allowed", values=person)
    for position in free:
        slots[position] = Record([], values=panel.person())
    records = [slot for slot in slots if slot is not None]
    originals = {id(r.winner) for r in records if r.winner is not None}
    plain = [r for r in records if r.kind == "clean" and id(r) not in originals]
    for record in rng.sample(plain, 1_000):
        record.values["points"] = None
    for record in records:
        if record.rule == "dedupe.exact":
            assert record.winner is not None
            record.values = record.winner.values
        record.fields = partner_fields(record.values)
    number_lines(records)
    text = write_csv([PARTNER_HEADER] + [r.fields for r in records], ",", "\n")
    expected = {
        "fixture": "05_panel_large.csv",
        "profile": "05_large.yaml",
        "rows": {"read": 50_000, "empty": 0, "rejected": 250, "duplicates": 1_500, "out": 48_250},
        "out_columns": 10,
        "rules": rule_counts(records),
        "fills": {"points": {"filled": 1_000, "value": 0}},
        "copy_offset": 7_001,
        "exit": 0,
        "exit_strict": 4,
    }
    _check_rows(expected, records)
    return Fixture("05", expected["fixture"], text.encode("utf-8"), expected, committed=False)


# --------------------------------------------------------------------------------------------- output

BUILDERS: tuple[Callable[[int], Fixture], ...] = (
    survey_fixture,
    partner_fixture,
    wave_fixture,
    legacy_fixture,
)


def build(seed: int) -> list[Fixture]:
    """All five fixtures; fixture 5 is always built because its SHA-256 goes into the manifest."""
    return [builder(seed) for builder in (*BUILDERS, large_fixture)]


def expected_text(fixture: Fixture) -> str:
    return json.dumps(fixture.expected, ensure_ascii=False, indent=2) + "\n"


def manifest(fixtures: Sequence[Fixture], seed: int) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for fixture in fixtures:
        files[fixture.name] = {
            "sha256": hashlib.sha256(fixture.data).hexdigest(),
            "bytes": len(fixture.data),
            "committed": fixture.committed,
        }
        name = f"expected/{fixture.number}.json"
        files[name] = {"sha256": hashlib.sha256(expected_text(fixture).encode("utf-8")).hexdigest()}
    return {"generator": "tools/make_data.py", "seed": seed, "files": dict(sorted(files.items()))}


def write(fixtures: Sequence[Fixture], out: Path, seed: int, write_large: bool) -> None:
    """Write the committed fixtures, every expected file and the manifest (fixture 5 only on request)."""
    (out / "expected").mkdir(parents=True, exist_ok=True)
    for fixture in fixtures:
        if fixture.committed or write_large:
            (out / fixture.name).write_bytes(fixture.data)
        text = expected_text(fixture)
        (out / "expected" / f"{fixture.number}.json").write_text(text, encoding="utf-8", newline="\n")
    document = json.dumps(manifest(fixtures, seed), indent=2) + "\n"
    (out / "MANIFEST.json").write_text(document, encoding="utf-8", newline="\n")


def check(seed: int, out: Path) -> int:
    """Regenerate into a temporary folder and compare every committed file byte for byte."""
    fixtures = build(seed)
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as folder:
        fresh = Path(folder)
        write(fixtures, fresh, seed, write_large=False)
        for path in sorted(p for p in fresh.rglob("*") if p.is_file()):
            name = path.relative_to(fresh).as_posix()
            committed = out / name
            if not committed.is_file():
                problems.append(f"{name}: missing in {out}")
            elif committed.read_bytes() != path.read_bytes():
                problems.append(f"{name}: differs from a fresh build")
    for problem in problems:
        print(problem, file=sys.stderr)
    print("fixtures match a fresh build" if not problems else f"{len(problems)} differences", file=sys.stderr)
    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the seeded fixtures, expected outcomes and manifest.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=ROOT / "fixtures")
    parser.add_argument(
        "--large", action="store_true", help="also write fixture 5 (about 4.5 MB, not committed)"
    )
    parser.add_argument("--check", action="store_true", help="regenerate into a temporary folder and compare")
    args = parser.parse_args(argv)
    if args.check:
        return check(args.seed, args.out)
    fixtures = build(args.seed)
    write(fixtures, args.out, args.seed, write_large=args.large)
    for fixture in fixtures:
        state = "written" if fixture.committed or args.large else "hash only"
        print(f"{fixture.name}: {len(fixture.data):,} bytes ({state})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
