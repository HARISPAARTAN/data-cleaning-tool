from __future__ import annotations

import codecs
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cleandata.core import sniff
from cleandata.core.sniff import (
    ALLOWED_ENCODINGS,
    DELIMITER_SAMPLE,
    ENCODING_SAMPLE,
    NO_DELIMITER,
    SHARE_TOLERANCE,
    EncodingGuess,
    detect_encoding,
    detect_header_row,
    find_decode_error,
    is_blank,
    looks_like_value,
    resolve_encoding,
    sample_text,
    sniff_delimiter,
)
from cleandata.errors import InputError

WESTERN_TEXT = "Name;Stadt\nRenée;Köln\nJosé;München\nFrançois;Brüssel\n" * 20
UTF16_TEXT = "Name;Stadt\nRenée;Köln\n" * 10
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@dataclass
class FakeMatch:
    """The part of a charset-normalizer match that detect_encoding reads."""

    encoding: str
    coherence: float = 0.0
    chaos: float = 0.0
    could_be_from_charset: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.could_be_from_charset:
            self.could_be_from_charset = [self.encoding]


def library_says(monkeypatch: pytest.MonkeyPatch, *matches: FakeMatch) -> None:
    monkeypatch.setattr(sniff, "from_bytes", lambda sample: list(matches))


# --- detect_encoding: BOM, strict UTF-8, NUL bytes -------------------------------------------------


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [(b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")],
)
def test_a_bom_decides(prefix: bytes, expected: str) -> None:
    guess = detect_encoding(prefix + b"a;b\n")
    assert (guess.encoding, guess.method) == (expected, "bom")


@pytest.mark.parametrize("bom", [codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE])
def test_a_utf32_bom_is_not_reported_as_utf16(bom: bytes) -> None:
    with pytest.raises(InputError, match="UTF-32.*set input.encoding to utf-32"):
        detect_encoding(bom + "a;b\n".encode("utf-32-le"))


def test_valid_utf8_is_strict_utf8() -> None:
    guess = detect_encoding("Größe;Straße\n".encode())
    assert guess == EncodingGuess("utf-8", "utf-8", None)


def test_empty_sample_is_utf8() -> None:
    assert detect_encoding(b"") == EncodingGuess("utf-8", "utf-8", None)


def test_a_cut_multibyte_character_at_the_sample_end_is_still_utf8() -> None:
    data = WESTERN_TEXT.encode()[:-1] + "é".encode()[:1]
    assert detect_encoding(data, complete=False) == EncodingGuess("utf-8", "utf-8", None)
    # the same bytes as a whole file are not UTF-8, and the library finds no allowed encoding for them
    with pytest.raises(InputError, match="set input.encoding"):
        detect_encoding(data, complete=True)


def test_nul_bytes_are_never_utf8() -> None:
    data = "a;b\n1;2\n".encode("utf-16-le")
    assert data.decode("utf-8")  # strictly valid UTF-8, which is why the NUL rule exists
    guess = detect_encoding(data)
    assert (guess.encoding, guess.method) == ("utf-16-le", "detected")


# --- detect_encoding: UTF-16 without a BOM ---------------------------------------------------------


@pytest.mark.parametrize(("codec", "expected"), [("utf-16-le", "utf-16-le"), ("utf-16-be", "utf-16-be")])
def test_utf16_with_real_content_and_no_bom(codec: str, expected: str) -> None:
    guess = detect_encoding(UTF16_TEXT.encode(codec))
    assert (guess.encoding, guess.method) == (expected, "detected")
    assert guess.confidence == pytest.approx(0.417)


def test_utf16_with_a_bom_and_real_content() -> None:
    assert detect_encoding(UTF16_TEXT.encode("utf-16")) == EncodingGuess("utf-16", "bom", None)


def test_utf16_needs_nul_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    # the library may name utf_16_be for short single-byte text such as b"K\xf6ln"; without NULs that is wrong
    library_says(monkeypatch, FakeMatch("utf_16_be", coherence=0.9))
    with pytest.raises(InputError, match="set input.encoding"):
        detect_encoding("Köln".encode("latin-1"))


def test_single_byte_encodings_are_refused_with_nul_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    library_says(monkeypatch, FakeMatch("cp1250", coherence=0.9, could_be_from_charset=["cp1250", "cp1252"]))
    with pytest.raises(InputError, match="set input.encoding"):
        detect_encoding(b"ab\x00cd\xe9")


# --- detect_encoding: Western rule (a) --------------------------------------------------------------


def test_western_text_without_c1_bytes_becomes_latin1() -> None:
    guess = detect_encoding(WESTERN_TEXT.encode("latin-1"))
    assert (guess.encoding, guess.method, guess.confidence) == ("latin-1", "detected", pytest.approx(0.206))


def test_western_text_with_c1_bytes_becomes_cp1252() -> None:
    text = WESTERN_TEXT + "Preis;€ 12 \N{EN DASH} „quoted“\n" * 5
    guess = detect_encoding(text.encode("cp1252"))
    assert (guess.encoding, guess.method, guess.confidence) == ("cp1252", "western", pytest.approx(0.312))


def test_cp1252_english_with_curly_quotes_and_an_en_dash_is_not_cp852() -> None:
    quoted = "\N{LEFT DOUBLE QUOTATION MARK}quick\N{RIGHT DOUBLE QUOTATION MARK}"
    text = f"The {quoted} brown fox \N{EN DASH} jumps over the lazy dog. "
    guess = detect_encoding((text * 3).encode("cp1252"))
    assert (guess.encoding, guess.method) == ("cp1252", "western")
    assert guess.confidence is not None
    assert 0.0 <= guess.confidence < 1.0


def test_c1_bytes_that_cp1252_cannot_decode_skip_the_western_rule() -> None:
    # 0x9D (t with caron in cp1250) is undefined in cp1252, so only cp1250 is left
    guess = detect_encoding("Příliš žluťoučký kůň".encode("cp1250"))
    assert (guess.encoding, guess.method, guess.confidence) == ("cp1250", "detected", 0.0)


def test_short_latin1_text_the_library_cannot_place_is_an_input_error() -> None:
    with pytest.raises(InputError, match="set input.encoding in the profile"):
        detect_encoding("Köln".encode("latin-1"))


def test_undetectable_bytes_are_an_input_error() -> None:
    with pytest.raises(InputError, match="set input.encoding"):
        detect_encoding(bytes(range(0x80, 0x100)) * 4 + b"\x00\x81\x8d\x8f\x90\x9d")


# --- detect_encoding: allow list, ties and confidence ----------------------------------------------


def test_the_allow_list_is_the_documented_one() -> None:
    assert ALLOWED_ENCODINGS == (
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


@pytest.mark.parametrize("name", ["big5", "cp852", "cp775", "mac_iceland", "shift_jis", "gb18030", "cp1251"])
def test_encodings_off_the_allow_list_are_refused(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    library_says(monkeypatch, FakeMatch(name, coherence=0.9))
    with pytest.raises(InputError, match="set input.encoding in the profile"):
        detect_encoding(b"ab\xe9cd")


def test_the_first_allowed_match_wins_over_better_ranked_disallowed_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_says(
        monkeypatch,
        FakeMatch("cp852", coherence=0.9),
        FakeMatch("iso8859_2", coherence=0.5),
    )
    guess = detect_encoding(b"ab\xe9cd")
    assert (guess.encoding, guess.method, guess.confidence) == ("iso-8859-2", "detected", 0.5)


@pytest.mark.parametrize(
    ("library_name", "expected"),
    [("cp1250", "cp1250"), ("iso8859_2", "iso-8859-2"), ("Windows-1250", "cp1250"), ("latin2", "iso-8859-2")],
)
def test_library_names_are_normalised_to_the_allow_list(
    monkeypatch: pytest.MonkeyPatch, library_name: str, expected: str
) -> None:
    library_says(monkeypatch, FakeMatch(library_name, coherence=0.6))
    assert detect_encoding(b"ab\xe9cd").encoding == expected


def test_a_western_tie_resolves_to_latin1(monkeypatch: pytest.MonkeyPatch) -> None:
    library_says(
        monkeypatch,
        FakeMatch("cp1250", coherence=0.4, could_be_from_charset=["cp1250", "cp1252"]),
        FakeMatch("cp775", coherence=0.4),
    )
    guess = detect_encoding(b"ab\xe9cd")
    assert (guess.encoding, guess.method) == ("latin-1", "detected")


def test_a_strictly_better_non_western_match_is_not_a_western_tie(monkeypatch: pytest.MonkeyPatch) -> None:
    library_says(
        monkeypatch,
        FakeMatch("cp1250", coherence=0.6),
        FakeMatch("cp1252", coherence=0.4),
    )
    assert detect_encoding(b"ab\xe9cd").encoding == "cp1250"


def test_confidence_is_coherence_over_one_plus_the_equally_ranked_alternatives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_says(monkeypatch, FakeMatch("cp1250", coherence=0.8))
    assert detect_encoding(b"ab\xe9cd").confidence == 0.8
    library_says(monkeypatch, FakeMatch("cp1250", coherence=0.8), FakeMatch("cp775", coherence=0.8))
    assert detect_encoding(b"ab\xe9cd").confidence == 0.4
    library_says(
        monkeypatch,
        FakeMatch("cp1250", coherence=0.9),
        FakeMatch("cp775", coherence=0.9),
        FakeMatch("mac_iceland", coherence=0.9),
        FakeMatch("cp437", coherence=0.5),
    )
    assert detect_encoding(b"ab\xe9cd").confidence == 0.3


def test_confidence_is_clamped_to_the_unit_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    library_says(monkeypatch, FakeMatch("cp1250", coherence=1.7))
    assert detect_encoding(b"ab\xe9cd").confidence == 1.0
    library_says(monkeypatch, FakeMatch("cp1250", coherence=-0.3))
    assert detect_encoding(b"ab\xe9cd").confidence == 0.0


def test_a_library_detected_result_is_never_fully_confident() -> None:
    guess = detect_encoding(WESTERN_TEXT.encode("latin-1"))
    assert guess.confidence is not None
    assert 0.0 < guess.confidence < 1.0


# --- resolve_encoding ------------------------------------------------------------------------------


def test_a_given_encoding_is_returned_without_reading_the_file(tmp_path: Path) -> None:
    assert resolve_encoding(tmp_path / "missing.csv", "cp1252") == EncodingGuess("cp1252", "given", None)


@pytest.mark.parametrize("name", ["no-such-codec", "rot13", "hex"])
def test_an_unknown_or_non_text_given_encoding_is_an_input_error(tmp_path: Path, name: str) -> None:
    with pytest.raises(InputError, match="unknown encoding"):
        resolve_encoding(tmp_path / "a.csv", name)


def test_auto_reads_the_bom(tmp_path: Path) -> None:
    path = tmp_path / "bom.csv"
    path.write_bytes(codecs.BOM_UTF8 + b"a;b\n1;2\n")
    assert resolve_encoding(path, "auto") == EncodingGuess("utf-8-sig", "bom", None)


def test_auto_detects_a_legacy_encoding(tmp_path: Path) -> None:
    path = tmp_path / "legacy.txt"
    path.write_bytes(WESTERN_TEXT.encode("latin-1"))
    guess = resolve_encoding(path, "auto")
    assert (guess.encoding, guess.method, guess.confidence) == ("latin-1", "detected", pytest.approx(0.206))


def test_auto_treats_a_character_cut_by_the_one_mib_sample_as_utf8(tmp_path: Path) -> None:
    path = tmp_path / "big.csv"
    # the two bytes of "é" straddle the sample end, and the file goes on after it
    path.write_bytes(b"a;b\n" + b"x" * (ENCODING_SAMPLE - 5) + "é".encode() + b"\n")
    assert resolve_encoding(path, "auto") == EncodingGuess("utf-8", "utf-8", None)


def test_auto_does_not_call_a_one_mib_file_that_ends_inside_a_character_utf8(tmp_path: Path) -> None:
    path = tmp_path / "exact.csv"
    # the file ends inside the character: nothing follows, so it is not UTF-8
    path.write_bytes(b"a;b\n" + b"x" * (ENCODING_SAMPLE - 5) + "é".encode()[:1])
    assert path.stat().st_size == ENCODING_SAMPLE
    guess = resolve_encoding(path, "auto")
    assert (guess.encoding, guess.method) == ("latin-1", "detected")


def test_auto_accepts_valid_utf8_of_exactly_one_mib(tmp_path: Path) -> None:
    path = tmp_path / "exact.csv"
    path.write_bytes(b"a;b\n" + b"x" * (ENCODING_SAMPLE - 5) + b"\n")
    assert path.stat().st_size == ENCODING_SAMPLE
    assert resolve_encoding(path, "auto").encoding == "utf-8"


# --- find_decode_error -----------------------------------------------------------------------------


def test_a_clean_file_has_no_decode_error(tmp_path: Path) -> None:
    path = tmp_path / "ok.csv"
    path.write_bytes("Größe;Straße\n".encode() * 3)
    assert find_decode_error(path, "utf-8") == -1


def test_the_first_bad_byte_is_found(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_bytes(b"a" * 40 + b"\xe4" + b"xyz\n")
    assert find_decode_error(path, "utf-8") == 40


@pytest.mark.parametrize("tail", [b"", b"xyz\n"])
def test_a_utf8_sig_error_offset_counts_the_bom(tmp_path: Path, tail: bytes) -> None:
    path = tmp_path / "sig.csv"
    path.write_bytes(codecs.BOM_UTF8 + b"a" * 40 + b"\xe4" + tail)
    assert find_decode_error(path, "utf-8-sig") == 43


def test_a_clean_utf8_sig_file_has_no_decode_error(tmp_path: Path) -> None:
    path = tmp_path / "sig.csv"
    path.write_bytes(codecs.BOM_UTF8 + "Größe;Straße\n".encode())
    assert find_decode_error(path, "utf-8-sig") == -1


def test_a_utf8_sig_error_after_the_first_block_keeps_its_offset(tmp_path: Path) -> None:
    path = tmp_path / "sig.csv"
    path.write_bytes(codecs.BOM_UTF8 + b"a" * 70000 + b"\xe4x")
    assert find_decode_error(path, "utf-8-sig") == 70003


def test_a_character_split_across_blocks_is_not_an_error(tmp_path: Path) -> None:
    path = tmp_path / "split.csv"
    path.write_bytes(b"a" * 65535 + "é".encode() + b"b" * 10)
    assert find_decode_error(path, "utf-8") == -1


def test_the_offset_after_pending_bytes_from_the_previous_block(tmp_path: Path) -> None:
    path = tmp_path / "split.csv"
    # block 1 ends with the first byte of "é"; the bad byte sits in block 2, after that pending byte
    path.write_bytes(b"a" * 65535 + "é".encode() + b"b" * 10 + b"\xe4x")
    assert find_decode_error(path, "utf-8") == 65547


def test_a_bad_byte_at_the_start_of_the_second_block(tmp_path: Path) -> None:
    path = tmp_path / "edge.csv"
    path.write_bytes(b"a" * 65536 + b"\xe4x")
    assert find_decode_error(path, "utf-8") == 65536


def test_a_truncated_character_at_the_end_of_the_file_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "cut.csv"
    path.write_bytes(b"abc" + "é".encode()[:1])
    assert find_decode_error(path, "utf-8") == 3


def test_a_byte_cp1252_does_not_define(tmp_path: Path) -> None:
    path = tmp_path / "undefined.csv"
    path.write_bytes(b"ab\x81cd")
    assert find_decode_error(path, "cp1252") == 2
    assert find_decode_error(path, "latin-1") == -1


def test_find_decode_error_rejects_an_unknown_encoding(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    path.write_bytes(b"abc")
    with pytest.raises(InputError, match="unknown encoding"):
        find_decode_error(path, "no-such-codec")


# --- sample_text -----------------------------------------------------------------------------------


def test_a_short_file_is_returned_whole_even_without_a_final_newline(tmp_path: Path) -> None:
    path = tmp_path / "short.csv"
    path.write_bytes(b"a;b\n1;2")
    assert sample_text(path, "utf-8") == "a;b\n1;2"


def test_a_file_of_exactly_64_kib_is_returned_whole(tmp_path: Path) -> None:
    path = tmp_path / "exact.csv"
    data = b"abcdefghi\n" * 6553 + b"abcdef"
    assert len(data) == DELIMITER_SAMPLE
    path.write_bytes(data)
    assert sample_text(path, "utf-8") == data.decode()


def test_a_longer_file_is_cut_after_the_last_complete_line(tmp_path: Path) -> None:
    path = tmp_path / "long.csv"
    data = b"abcdefghi\n" * 6553 + b"abcdefg\n"
    assert len(data) == DELIMITER_SAMPLE + 2
    path.write_bytes(data)
    text = sample_text(path, "utf-8")
    assert text == "abcdefghi\n" * 6553
    assert len(text) < DELIMITER_SAMPLE


def test_one_byte_over_64_kib_is_already_cut(tmp_path: Path) -> None:
    path = tmp_path / "over.csv"
    data = b"abcdefghi\n" * 6553 + b"abcdef" + b"g"
    assert len(data) == DELIMITER_SAMPLE + 1
    path.write_bytes(data)
    assert sample_text(path, "utf-8") == "abcdefghi\n" * 6553


def test_a_long_sample_without_a_newline_keeps_all_64_kib(tmp_path: Path) -> None:
    path = tmp_path / "one-line.csv"
    path.write_bytes(b"x" * (DELIMITER_SAMPLE + 100))
    assert sample_text(path, "utf-8") == "x" * DELIMITER_SAMPLE


def test_a_character_split_by_the_64_kib_cut_leaves_no_replacement_character(tmp_path: Path) -> None:
    path = tmp_path / "split.csv"
    # line 1 ends at byte 10; the first byte of the "é" in line 2 is the last byte of the sample
    line_two = b"x" * (DELIMITER_SAMPLE - 11) + "é".encode() + b"\n"
    path.write_bytes(b"abcdefghi\n" + line_two)
    text = sample_text(path, "utf-8")
    assert text == "abcdefghi\n"
    assert "\N{REPLACEMENT CHARACTER}" not in text


def test_the_bom_is_not_part_of_the_text(tmp_path: Path) -> None:
    path = tmp_path / "bom.csv"
    path.write_bytes(codecs.BOM_UTF8 + "Größe;Straße\n".encode())
    assert sample_text(path, "utf-8-sig") == "Größe;Straße\n"


@pytest.mark.parametrize(
    ("encoding", "data"),
    [("utf-16", UTF16_TEXT.encode("utf-16")), ("latin-1", UTF16_TEXT.encode("latin-1"))],
)
def test_sample_text_decodes_with_the_given_encoding(tmp_path: Path, encoding: str, data: bytes) -> None:
    path = tmp_path / "enc.csv"
    path.write_bytes(data)
    assert sample_text(path, encoding) == UTF16_TEXT


def test_sample_text_rejects_an_unknown_encoding(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    path.write_bytes(b"abc")
    with pytest.raises(InputError, match="unknown encoding"):
        sample_text(path, "no-such-codec")


# --- the committed fixtures ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "encoding", "method", "delimiter", "fields"),
    [
        ("01_survey_export_de.csv", "utf-8-sig", "bom", ";", 14),
        ("02_partner_respondents.csv", "utf-8", "utf-8", ",", 10),
        ("04_legacy_partner_latin1.txt", "latin-1", "detected", "\t", 9),
    ],
)
def test_the_fixtures_are_detected(
    name: str, encoding: str, method: str, delimiter: str, fields: int
) -> None:
    path = FIXTURES / name
    guess = resolve_encoding(path, "auto")
    assert (guess.encoding, guess.method) == (encoding, method)
    if method == "detected":
        assert guess.confidence is not None
        assert 0.0 < guess.confidence < 1.0
    else:
        assert guess.confidence is None
    assert find_decode_error(path, guess.encoding) == -1
    found = sniff_delimiter(sample_text(path, guess.encoding))
    assert (found.delimiter, found.fields, found.share) == (delimiter, fields, 1.0)


# --- sniff_delimiter -------------------------------------------------------------------------------


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
    assert (guess.delimiter, guess.share, guess.fields) == (None, 0.0, 1)


def _ragged_semicolon_file(short_rows: int) -> str:
    """A header and 49 rows with 4 `;` fields and one decimal comma, the first rows cut short."""
    lines = ["id;note;amount;city"]
    for number in range(49):
        lines.append(f"{number};12,5;Köln" if number < short_rows else f"{number};text;12,5;Köln")
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(("short_rows", "share"), [(2, 0.96), (3, 0.94)])
def test_a_ragged_semicolon_file_with_decimal_commas_stays_semicolon(short_rows: int, share: float) -> None:
    guess = sniff_delimiter(_ragged_semicolon_file(short_rows))
    assert (guess.delimiter, guess.fields, guess.share) == (";", 4, share)


def test_a_semicolon_share_below_one_is_reported() -> None:
    text = "a;b;c\n" * 9 + "x;y\n"
    guess = sniff_delimiter(text)
    assert (guess.delimiter, guess.fields, guess.share) == (";", 3, 0.9)


def test_shares_within_the_tolerance_count_as_equal() -> None:
    # `,` has the better share (0.98 against 0.96) with 2 fields; `;` wins on fields
    assert SHARE_TOLERANCE == 0.05
    guess = sniff_delimiter(_ragged_semicolon_file(2))
    assert guess.delimiter == ";"


def test_a_better_share_beyond_the_tolerance_wins_over_more_fields() -> None:
    # `;` has 4 fields in 9 of 10 rows (0.9); `,` has 2 fields in every row (1.0)
    text = "a;b;c;d,e\n" * 9 + "a;b;c,d\n"
    guess = sniff_delimiter(text)
    assert (guess.delimiter, guess.fields, guess.share) == (",", 2, 1.0)


def test_a_candidate_whose_first_record_has_the_modal_count_is_preferred() -> None:
    # `;` is modal with 4 fields, but the first record has 1, so the next candidate (`,`) is taken
    text = "a,b\n" + "1;2,5;3;4\n" * 20
    guess = sniff_delimiter(text)
    assert (guess.delimiter, guess.fields, guess.share) == (",", 2, 1.0)


def test_a_ragged_file_whose_first_record_is_not_modal_keeps_its_delimiter() -> None:
    # the records have 3, 2, 5 and 4 fields, so every count is equally frequent and the header is not modal
    guess = sniff_delimiter("a,b,c\n1,2\n1,2,3,,\n1,2,3,4\n")
    assert (guess.delimiter, guess.fields, guess.share) == (",", 5, 0.25)


def test_a_first_record_without_the_delimiter_still_vetoes_the_candidate() -> None:
    # `;` is modal with 3 fields, but the header is a single column: the file has no delimiter
    guess = sniff_delimiter("name\n" + "1;2;3\n" * 10)
    assert (guess.delimiter, guess.share, guess.fields) == (None, 0.0, 1)


def test_thousands_formatted_single_column_numbers_have_no_delimiter() -> None:
    text = "amount\n" + "\n".join(["1,234", "12,345", "1,234,567", "987", "45,678"] * 6) + "\n"
    guess = sniff_delimiter(text)
    assert (guess.delimiter, guess.share, guess.fields) == (None, 0.0, 1)
    assert NO_DELIMITER == "\x1f"


def test_blank_records_do_not_count_as_the_first_record() -> None:
    guess = sniff_delimiter("\n\n;;\na;b\n1;2\n")
    assert (guess.delimiter, guess.fields) == (";", 2)


# --- looks_like_value, is_blank, detect_header_row -------------------------------------------------


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("", True),
        ("  \t", True),
        ("x", False),
        (0, False),
        (0.0, False),
        (dt.date(2026, 3, 1), False),
    ],
)
def test_is_blank(value: object, expected: bool) -> None:
    assert is_blank(value) is expected


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


def test_the_header_is_looked_for_in_the_first_20_records_only() -> None:
    numbers = [["1", "2"]] * 19
    assert detect_header_row([*numbers, ["id", "score"]]) == 20
    assert detect_header_row([*numbers, ["3", "4"], ["id", "score"]]) is None


def test_none_cells_are_blank_in_a_header_row() -> None:
    assert detect_header_row([[None, "id", "name"], [None, "1", "a"]]) == 1
    assert detect_header_row([[None, None], [None, None]]) is None


def test_a_record_with_a_non_string_cell_is_not_a_header() -> None:
    records = [[1, "id", "name"], [dt.date(2026, 3, 1), "x", "y"], ["id", "name", "score"]]
    assert detect_header_row(records) == 3
    assert detect_header_row([[1, "id"], [2, "name"]]) is None
    assert detect_header_row([[True, "id"], [1.5, "name"]]) is None
