"""Encoding, delimiter and header-row detection.

Encoding, in this order: a UTF-32 BOM is refused (set the encoding in the profile); a UTF-8 or UTF-16 BOM
decides (``utf-8-sig``, ``utf-16``); a sample without NUL bytes that is strict UTF-8 is ``utf-8``; a sample
without NUL bytes that holds a byte in 0x80-0x9F and decodes strictly as cp1252 is ``cp1252`` (method
``western``); otherwise charset-normalizer's best match that is on ``ALLOWED_ENCODINGS``, where a Western
single-byte result becomes ``latin-1`` (the sample then holds no byte in 0x80-0x9F). NUL bytes mean UTF-16,
and UTF-16 without a BOM is only accepted with NUL bytes. Short samples often tie between cp1250 and
cp1252 (same chaos and coherence); a tie resolves to the Western reading. A detected result carries
``coherence / (1 + equally ranked alternatives)`` as its confidence; BOM, UTF-8 and given results carry
none. Delimiter: per candidate the share of the first 50 records (first 64 KiB) with the modal field
count; shares within ``SHARE_TOLERANCE`` of the best count as equal, then more fields win, then candidate
order. A candidate whose first record has the modal count is preferred; when none has it, the best
candidate whose first record still holds the delimiter (at least ``MIN_FIELDS`` fields) is kept, so a
ragged file keeps its delimiter while a single-column header vetoes it. Header row ``auto``: the first
of the first 20 records whose non-empty cell count is the maximum among them and whose non-empty cells
are all text that is neither a number nor a date.
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

from charset_normalizer import CharsetMatch, from_bytes

from cleandata.core.coerce import ParseSettings, parse_decimal
from cleandata.errors import InputError

ENCODING_SAMPLE: Final = 1 << 20
DELIMITER_SAMPLE: Final = 64 * 1024
DELIMITER_RECORDS: Final = 50
HEADER_RECORDS: Final = 20
CANDIDATES: Final = (",", ";", "\t", "|")
NO_DELIMITER: Final = "\x1f"
SHARE_TOLERANCE: Final = 0.05
MIN_FIELDS: Final = 2
BOMS: Final = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
UTF32_BOMS: Final = (codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)
ALLOWED_ENCODINGS: Final = (
    "utf-8",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "cp1252",
    "latin-1",
    "cp1250",
    "iso-8859-15",
    "iso-8859-2",
)
ALLOWED_BY_CODEC: Final = {codecs.lookup(name).name: name for name in ALLOWED_ENCODINGS}
UTF16_NAMES: Final = frozenset({"utf-16", "utf-16-le", "utf-16-be"})
WESTERN: Final = frozenset({"cp1252", "iso8859-1", "iso8859-15"})
C1_BYTES: Final = re.compile(rb"[\x80-\x9f]")
NOT_DETECTED: Final = "cannot detect the encoding reliably; set input.encoding in the profile"
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


@dataclass(frozen=True)
class _Candidate:
    delimiter: str
    order: int
    share: float
    fields: int
    first: int


def detect_encoding(sample: bytes, complete: bool = True) -> EncodingGuess:
    """Decide the encoding of a byte sample; ``complete`` says whether it is the whole file."""
    for bom in UTF32_BOMS:
        if sample.startswith(bom):
            raise InputError(
                "the file starts with a UTF-32 byte order mark, which is not detected automatically; "
                "set input.encoding to utf-32 in the profile"
            )
    for bom, name in BOMS:
        if sample.startswith(bom):
            return EncodingGuess(name, "bom")
    has_nul = b"\x00" in sample
    if not has_nul:
        try:
            codecs.getincrementaldecoder("utf-8")("strict").decode(sample, final=complete)
        except UnicodeDecodeError:
            pass
        else:
            return EncodingGuess("utf-8", "utf-8")
        if C1_BYTES.search(sample) and _decodes(sample, "cp1252"):
            return EncodingGuess("cp1252", "western", _western_confidence(sample))
    return _library_guess(sample, has_nul)


def _decodes(sample: bytes, encoding: str) -> bool:
    try:
        sample.decode(encoding)
    except UnicodeDecodeError:
        return False
    return True


def _library_guess(sample: bytes, has_nul: bool) -> EncodingGuess:
    """charset-normalizer's best match on the allow list, with the Western tie rule."""
    western_ok = not has_nul and C1_BYTES.search(sample) is None
    results = list(from_bytes(sample))
    ranked: list[tuple[CharsetMatch, str]] = []
    for match in results:
        name = _allowed_name(match, has_nul)
        if name is None and western_ok and _western(match.could_be_from_charset):
            name = "latin-1"
        if name is not None:
            ranked.append((match, name))
    if not ranked:
        raise InputError(NOT_DETECTED)
    best, name = ranked[0]
    tied = [m for m in results if _rank(m) == _rank(best)]
    if western_ok and any(_western(m.could_be_from_charset) for m in tied):
        name = "latin-1"
    return EncodingGuess(name, "detected", _confidence(best, results))


def _allowed_name(match: CharsetMatch, has_nul: bool) -> str | None:
    """The allow-list spelling of a match's encoding; NUL bytes mean UTF-16 and UTF-16 needs NUL bytes."""
    try:
        name = ALLOWED_BY_CODEC.get(codecs.lookup(match.encoding).name)
    except LookupError:
        return None
    if name is None or (name in UTF16_NAMES) != has_nul:
        return None
    return name


def _rank(match: CharsetMatch) -> tuple[float, float]:
    return (float(match.chaos), float(match.coherence))


def _confidence(match: CharsetMatch, results: Sequence[CharsetMatch]) -> float:
    """Coherence of the match over one plus the number of equally ranked alternatives, within [0, 1]."""
    equal = sum(1 for other in results if other is not match and _rank(other) == _rank(match))
    return round(min(1.0, max(0.0, float(match.coherence) / (1 + equal))), 3)


def _western_confidence(sample: bytes) -> float:
    """The confidence of the library's own Western reading of the sample, 0.0 when it has none."""
    results = list(from_bytes(sample))
    match = next((m for m in results if _western(m.could_be_from_charset)), None)
    return 0.0 if match is None else _confidence(match, results)


def _western(names: Sequence[str]) -> bool:
    """True when charset-normalizer says the bytes decode the same in a Western single-byte encoding."""
    return any(codecs.lookup(name).name in WESTERN for name in names)


def _check_codec(encoding: str) -> None:
    """Raise InputError for a name that is not a Python text encoding."""
    try:
        b"a".decode(encoding)
    except UnicodeDecodeError:
        pass  # the codec exists; this one byte is just not a whole character in it
    except LookupError:
        raise InputError(
            f'unknown encoding "{encoding}"; set input.encoding to a Python text encoding such as utf-8'
        ) from None


def resolve_encoding(path: Path, given: str) -> EncodingGuess:
    if given != "auto":
        _check_codec(given)
        return EncodingGuess(given, "given")
    with path.open("rb") as handle:
        sample = handle.read(ENCODING_SAMPLE + 1)
    complete = len(sample) <= ENCODING_SAMPLE
    return detect_encoding(sample[:ENCODING_SAMPLE], complete)


def find_decode_error(path: Path, encoding: str) -> int:
    """The byte offset of the first undecodable byte, or -1."""
    _check_codec(encoding)
    # utf-8-sig slices the BOM off before decoding, so its error positions miss the 3 BOM bytes; a BOM is
    # valid UTF-8, and plain utf-8 accepts exactly what utf-8-sig accepts.
    name = "utf-8" if codecs.lookup(encoding).name == "utf-8-sig" else encoding
    decoder = codecs.getincrementaldecoder(name)("strict")
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
    _check_codec(encoding)
    with path.open("rb") as handle:
        data = handle.read(DELIMITER_SAMPLE + 1)
    complete = len(data) <= DELIMITER_SAMPLE
    text = data[:DELIMITER_SAMPLE].decode(encoding, errors="replace")
    if not complete and "\n" in text:
        text = text[: text.rfind("\n") + 1]
    return text


def sniff_delimiter(text: str, quotechar: str = '"') -> DelimiterGuess:
    pool: list[_Candidate] = []
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
        if modal < MIN_FIELDS:
            continue
        pool.append(_Candidate(candidate, order, frequency / len(counts), modal, counts[0]))
    fallback: _Candidate | None = None
    while pool:
        top = max(c.share for c in pool)
        close = [c for c in pool if top - c.share <= SHARE_TOLERANCE + 1e-9]
        winner = max(close, key=lambda c: (c.fields, -c.order))
        if winner.first == winner.fields:
            return DelimiterGuess(winner.delimiter, round(winner.share, 6), winner.fields)
        if fallback is None and winner.first >= MIN_FIELDS:
            fallback = winner
        pool.remove(winner)
    if fallback is not None:
        return DelimiterGuess(fallback.delimiter, round(fallback.share, 6), fallback.fields)
    return DelimiterGuess(None, 0.0, 1)


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
