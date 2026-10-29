"""Encoding, delimiter and header-row detection.

Encoding: a BOM decides (UTF-8 to ``utf-8-sig``, UTF-16 LE or BE to ``utf-16``); otherwise the first
1 MiB is tried as strict UTF-8; otherwise charset-normalizer's best match, where a Western single-byte
result (cp1252, latin-1, iso-8859-15) becomes cp1252 if the sample holds bytes 0x80-0x9F and latin-1
otherwise. Short samples often tie between cp1250 and cp1252 (same chaos and coherence); a tie
resolves to the Western reading. Delimiter: the share of the first 50 records (first 64 KiB) with the
modal field count, per candidate. Header row ``auto``: the first of the first 20 records whose
non-empty cell count is the maximum among them and whose non-empty cells are all text that is neither
a number nor a date.
"""

from __future__ import annotations

import codecs
import csv
import datetime as dt
import io
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from charset_normalizer import from_bytes

from cleandata.core.coerce import ParseSettings, parse_decimal
from cleandata.errors import InputError

ENCODING_SAMPLE: Final = 1 << 20
DELIMITER_SAMPLE: Final = 64 * 1024
DELIMITER_RECORDS: Final = 50
HEADER_RECORDS: Final = 20
CANDIDATES: Final = (",", ";", "\t", "|")
NO_DELIMITER: Final = "\x1f"
BOMS: Final = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
WESTERN: Final = frozenset({"cp1252", "iso8859-1", "iso8859-15"})
C1_BYTES: Final = re.compile(rb"[\x80-\x9f]")
BUILTIN_DATE_PATTERNS: Final = (
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%Y%m%d",
    "%d.%m.%y",
)
BUILTIN_DATETIME_PATTERNS: Final = tuple(
    pattern + suffix for pattern in BUILTIN_DATE_PATTERNS for suffix in (" %H:%M", " %H:%M:%S", "T%H:%M:%S")
)
NUMBER_STYLES: Final = (
    (".", None),
    (".", ","),
    (".", " "),
    (".", "'"),
    (",", None),
    (",", "."),
    (",", " "),
    (",", "'"),
)


@dataclass(frozen=True)
class EncodingGuess:
    encoding: str
    method: str
    confidence: float | None = None


@dataclass(frozen=True)
class DelimiterGuess:
    delimiter: str | None
    share: float
    fields: int


def detect_encoding(sample: bytes, complete: bool = True) -> EncodingGuess:
    """Decide the encoding of a byte sample; ``complete`` says whether it is the whole file."""
    for bom, name in BOMS:
        if sample.startswith(bom):
            return EncodingGuess(name, "bom")
    try:
        codecs.getincrementaldecoder("utf-8")("strict").decode(sample, final=complete)
    except UnicodeDecodeError:
        pass
    else:
        return EncodingGuess("utf-8", "utf-8")
    results = list(from_bytes(sample))
    if not results:
        raise InputError("cannot detect the encoding; set input.encoding in the profile")
    best = results[0]
    tied = [m for m in results if (m.chaos, m.coherence) == (best.chaos, best.coherence)]
    western = next((m for m in tied if _western(m.could_be_from_charset)), None)
    match = western or best
    name = codecs.lookup(match.encoding).name
    if western is not None:
        name = "cp1252" if C1_BYTES.search(sample) else "latin-1"
    return EncodingGuess(name, "detected", round(1.0 - float(match.chaos), 3))


def _western(names: Sequence[str]) -> bool:
    """True when charset-normalizer says the bytes decode the same in a Western single-byte encoding."""
    return any(codecs.lookup(name).name in WESTERN for name in names)


def resolve_encoding(path: Path, given: str) -> EncodingGuess:
    if given != "auto":
        return EncodingGuess(given, "given")
    with path.open("rb") as handle:
        sample = handle.read(ENCODING_SAMPLE + 1)
    complete = len(sample) <= ENCODING_SAMPLE
    return detect_encoding(sample[:ENCODING_SAMPLE], complete)


def find_decode_error(path: Path, encoding: str) -> int:
    """The byte offset of the first undecodable byte, or -1."""
    decoder = codecs.getincrementaldecoder(encoding)("strict")
    offset = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(1 << 16)
            pending = len(decoder.getstate()[0])
            try:
                decoder.decode(block, final=not block)
            except UnicodeDecodeError as exc:
                return offset - pending + exc.start
            if not block:
                return -1
            offset += len(block)


def sample_text(path: Path, encoding: str) -> str:
    """The first 64 KiB decoded, cut after the last complete line unless the file is shorter."""
    with path.open("rb") as handle:
        data = handle.read(DELIMITER_SAMPLE + 1)
    complete = len(data) <= DELIMITER_SAMPLE
    text = data[:DELIMITER_SAMPLE].decode(encoding, errors="replace")
    if not complete and "\n" in text:
        text = text[: text.rfind("\n") + 1]
    return text


def sniff_delimiter(text: str, quotechar: str = '"') -> DelimiterGuess:
    best: tuple[tuple[float, int, int], DelimiterGuess] | None = None
    for order, candidate in enumerate(CANDIDATES):
        counts: list[int] = []
        reader = csv.reader(
            io.StringIO(text, newline=""), delimiter=candidate, quotechar=quotechar, strict=False
        )
        try:
            for fields in reader:
                if any(field.strip() for field in fields):
                    counts.append(len(fields))
                if len(counts) >= DELIMITER_RECORDS:
                    break
        except csv.Error:
            continue
        if not counts:
            continue
        modal, frequency = max(Counter(counts).items(), key=lambda item: (item[1], item[0]))
        if modal < 2:
            continue
        share = frequency / len(counts)
        key = (share, modal, -order)
        if best is None or key > best[0]:
            best = (key, DelimiterGuess(candidate, round(share, 6), modal))
    return best[1] if best is not None else DelimiterGuess(None, 0.0, 1)


def is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def looks_like_value(text: str) -> bool:
    """True when a cell parses as a number in any built-in style or as a built-in date or datetime."""
    stripped = text.strip()
    for decimal, thousands in NUMBER_STYLES:
        if parse_decimal(stripped, ParseSettings(decimal=decimal, thousands=thousands)) is not None:
            return True
    for pattern in BUILTIN_DATE_PATTERNS + BUILTIN_DATETIME_PATTERNS:
        try:
            dt.datetime.strptime(stripped, pattern)
        except ValueError:
            continue
        return True
    return False


def detect_header_row(records: Sequence[Sequence[object]]) -> int | None:
    """The 1-based header row among the first 20 records, or None."""
    head = list(records[:HEADER_RECORDS])
    counts = [sum(1 for value in record if not is_blank(value)) for record in head]
    if not counts or max(counts) == 0:
        return None
    top = max(counts)
    for number, (record, count) in enumerate(zip(head, counts, strict=True), start=1):
        if count != top:
            continue
        cells = [value for value in record if not is_blank(value)]
        if all(isinstance(value, str) and not looks_like_value(value) for value in cells):
            return number
    return None
