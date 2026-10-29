from __future__ import annotations

import pytest

from cleandata.core.sniff import detect_encoding, detect_header_row, looks_like_value, sniff_delimiter
from cleandata.errors import InputError

WESTERN_TEXT = "Name;Stadt\nRenée;Köln\nJosé;München\nFrançois;Brüssel\n" * 20


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [(b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")],
)
def test_a_bom_decides(prefix: bytes, expected: str) -> None:
    guess = detect_encoding(prefix + b"a;b\n")
    assert (guess.encoding, guess.method) == (expected, "bom")


def test_valid_utf8_is_strict_utf8() -> None:
    guess = detect_encoding("Größe;Straße\n".encode())
    assert (guess.encoding, guess.method, guess.confidence) == ("utf-8", "utf-8", None)


def test_a_cut_multibyte_character_at_the_sample_end_is_still_utf8() -> None:
    data = "abé".encode()[:-1]
    assert detect_encoding(data, complete=False).encoding == "utf-8"
    assert detect_encoding(data, complete=True).encoding != "utf-8"


def test_western_text_without_c1_bytes_becomes_latin1() -> None:
    guess = detect_encoding(WESTERN_TEXT.encode("latin-1"))
    assert (guess.encoding, guess.method) == ("latin-1", "detected")
    assert guess.confidence is not None


def test_western_text_with_c1_bytes_becomes_cp1252() -> None:
    text = WESTERN_TEXT + "Preis;€ 12 \N{EN DASH} „quoted“\n" * 5
    guess = detect_encoding(text.encode("cp1252"))
    assert (guess.encoding, guess.method) == ("cp1252", "detected")


def test_empty_sample_is_utf8() -> None:
    assert detect_encoding(b"").encoding == "utf-8"


def test_undetectable_bytes_are_an_input_error() -> None:
    with pytest.raises(InputError, match="set input.encoding"):
        detect_encoding(bytes(range(0x80, 0x100)) * 4 + b"\x00\x81\x8d\x8f\x90\x9d")


@pytest.mark.parametrize(
    ("text", "delimiter", "fields"),
    [
        ("a,b,c\n1,2,3\n4,5,6\n", ",", 3),
        ("a;b\n1,5;2,5\n3,5;4,5\n", ";", 2),
        ("a\tb\tc\n1\t2\t3\n", "\t", 3),
        ("a|b\n1|2\n", "|", 2),
        ('name;note\n"Lee, Ann";"a, b, c"\n"Bo";"d, e"\n', ";", 2),
    ],
)
def test_delimiter_scoring(text: str, delimiter: str, fields: int) -> None:
    guess = sniff_delimiter(text)
    assert (guess.delimiter, guess.fields, guess.share) == (delimiter, fields, 1.0)


def test_ties_prefer_more_fields_then_candidate_order() -> None:
    assert sniff_delimiter("a,b;c;d\n1,2;3;4\n").delimiter == ";"
    assert sniff_delimiter("a,b;c\n1,2;3\n").delimiter == ","


def test_a_single_column_file_has_no_delimiter() -> None:
    guess = sniff_delimiter("name\nAnn\nBo\n")
    assert (guess.delimiter, guess.fields) == (None, 1)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12", True),
        ("1.234,5", True),
        ("21.03.2025", True),
        ("2025-03-21 10:00", True),
        ("Name", False),
        ("NG-123", False),
    ],
)
def test_values_that_are_not_header_text(text: str, expected: bool) -> None:
    assert looks_like_value(text) is expected


def test_header_row_auto() -> None:
    records = [
        ["Partner export", "", ""],
        ["Generated 2026-03-01", "", ""],
        ["", "", ""],
        ["Respondent ID", "Interview date", "Score"],
        ["NG-1", "2026-03-01", "5"],
    ]
    assert detect_header_row(records) == 4
    assert detect_header_row([["id", "score"], ["a", "1"]]) == 1
    assert detect_header_row([["1", "2"], ["3", "4"]]) is None
    assert detect_header_row([]) is None
