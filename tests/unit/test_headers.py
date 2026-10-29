from __future__ import annotations

import pytest

from cleandata.core.frame import column, columns_of, positional
from cleandata.core.headers import normalise_headers, plan_headers, snake_case, structure_stage
from cleandata.errors import InputError, ProfileError
from support import frame_of, profile_of

CASES = [
    ("Respondent ID", "respondent_id"),
    ("Welle", "welle"),
    ("Abgeschlossen am", "abgeschlossen_am"),
    ("Haushaltseinkommen (€)", "haushaltseinkommen_eur"),
    ("Ausgaben letzter Monat (€)", "ausgaben_letzter_monat_eur"),
    ("Größe", "groesse"),
    ("Straße", "strasse"),
    ("Ärger", "aerger"),
    ("Øre", "ore"),
    ("Amount (£)", "amount_gbp"),
    ("Price $", "price_usd"),
    ("Share %", "share_pct"),
    ("Order #", "order_no"),
    ("R&D", "r_and_d"),
    ("PartnerID", "partner_id"),
    ("PanelistRef", "panelist_ref"),
    ("firstName", "first_name"),
    ("HTTPServer", "http_server"),
    ("Year of birth", "year_of_birth"),
    ("Opt-in", "opt_in"),
    ("2nd wave", "c_2nd_wave"),
    ("  spaced  out  ", "spaced_out"),
    ("", "column_7"),
    ("___", "column_7"),
    ("Émile Zola", "emile_zola"),
    ("e-mail", "e_mail"),
    ("Café", "cafe"),
    ("\N{LATIN SMALL LIGATURE FI}le", "file"),
    ("NPS (0\N{EN DASH}10)", "nps_0_10"),
    ("kommentar_2", "kommentar_2"),
]


def test_thirty_cases() -> None:
    assert len(CASES) == 30


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_snake_case(raw: str, expected: str) -> None:
    assert snake_case(raw, 7) == expected


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_snake_case_is_idempotent(raw: str, expected: str) -> None:
    assert snake_case(expected, 7) == expected


def test_repeats_are_numbered_left_to_right_skipping_taken_names() -> None:
    assert normalise_headers(["Kommentar", "Kommentar", "kommentar_2", "Kommentar"]) == [
        "kommentar",
        "kommentar_3",
        "kommentar_2",
        "kommentar_4",
    ]
    assert normalise_headers(["a", "A", "", ""]) == ["a", "a_2", "column_3", "column_4"]


def test_keep_mode_trims_and_names_empty_headers() -> None:
    assert normalise_headers([" Name ", "", "Name"], "keep") == ["Name", "column_2", "Name_2"]


def test_rename_and_drop() -> None:
    profile = profile_of(
        {"wave": {"type": "integer"}, "respondent_id": {"type": "string"}},
        headers={"rename": {"welle": "wave"}, "drop": ["notiz"]},
    )
    plan = plan_headers(["Respondent ID", "Welle", "Notiz"], profile)
    assert plan.columns == ["wave", "respondent_id"]
    assert [(m.raw, m.final, m.action) for m in plan.mappings] == [
        ("Respondent ID", "respondent_id", "kept"),
        ("Welle", "wave", "renamed"),
        ("Notiz", None, "dropped"),
    ]
    assert plan.notices == ()


def test_rename_collision_is_a_profile_error() -> None:
    profile = profile_of(
        {"wave": {"type": "integer"}}, headers={"rename": {"welle": "wave"}}, keep_unlisted=True
    )
    with pytest.raises(ProfileError, match="both become 'wave'"):
        plan_headers(["Welle", "Wave"], profile)


def test_absent_required_column_is_an_input_error() -> None:
    profile = profile_of({"email": {"type": "string"}, "mode": {"type": "string", "optional": True}})
    with pytest.raises(InputError, match="no column 'email'"):
        plan_headers(["Name"], profile)


def test_optional_absent_column_and_unlisted_warning() -> None:
    profile = profile_of({"name": {"type": "string"}, "mode": {"type": "string", "optional": True}})
    plan = plan_headers(["Name", "Extra"], profile)
    assert plan.columns == ["name", "mode"]
    codes = {notice.code: notice.level for notice in plan.notices}
    assert codes == {"structure.unlisted_dropped": "warning", "structure.optional_absent": "info"}


def test_keep_unlisted_and_sheet_column_order() -> None:
    profile = profile_of(
        {"name": {"type": "string"}}, keep_unlisted=True, input={"sheet_column": "source_sheet"}
    )
    plan = plan_headers(["Extra", "Name"], profile)
    assert plan.columns == ["name", "extra", "source_sheet"]


def test_reserved_underscore_header_is_refused_unless_dropped_or_renamed() -> None:
    listed = {"name": {"type": "string"}}
    keep = {"normalise": "keep"}
    refused = profile_of(listed, headers=keep, keep_unlisted=True)
    with pytest.raises(ProfileError, match=r"'_source_row'.*headers\.drop.*headers\.rename"):
        plan_headers(["name", "_source_row"], refused)
    dropped = profile_of(listed, headers={**keep, "drop": ["_source_row"]}, keep_unlisted=True)
    assert plan_headers(["name", "_source_row"], dropped).columns == ["name"]
    renamed = profile_of(listed, headers={**keep, "rename": {"_source_sheet": "origin"}}, keep_unlisted=True)
    assert plan_headers(["name", "_source_sheet"], renamed).columns == ["name", "origin"]
    unlisted_dropped = profile_of(listed, headers=keep)
    assert plan_headers(["name", "_source_row"], unlisted_dropped).columns == ["name"]


def test_structure_drops_empty_records_and_keeps_cell_values() -> None:
    profile = profile_of({"name": {"type": "string"}, "city": {"type": "string"}})
    raw = frame_of([positional(0), positional(1)], [[" Ann ", "Leeds"], ["", "  "], [None, "York"]])
    plan = plan_headers(["Name", "City"], profile)
    frame, events = structure_stage(raw, plan, 2, drop_empty_rows=True)
    assert events.counts["empty"] == 1
    assert columns_of(frame) == ["name", "city", "_source_row", "_source_sheet"]
    assert column(frame, "name") == [" Ann ", None]
    assert column(frame, "_source_row") == [2, 4]


def test_drop_empty_columns_only_drops_columns_the_profile_does_not_list() -> None:
    profile = profile_of(
        {"name": {"type": "string"}, "note": {"type": "string"}}, input={"drop_empty_columns": True}
    )
    plan = plan_headers(["Name", "Note", ""], profile, frozenset({1, 2}))
    assert plan.columns == ["name", "note"]
    assert [m.action for m in plan.mappings] == ["kept", "kept", "dropped_empty"]
