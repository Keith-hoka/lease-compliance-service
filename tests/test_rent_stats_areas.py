"""Tests for resolving a consumer's area key to a published area_code."""

import logging

from app.rent_stats.areas import match_label, normalise, resolve_area

GROUPED = "Albert Park-Middle Park-West St Kilda"


def test_normalise_collapses_whitespace_and_casefolds():
    assert normalise(" Albert  Park ") == "albert park"
    assert normalise("ALBERT PARK") == "albert park"


def test_match_label_exact_label():
    assert match_label("Carlton", ["Carlton", "Richmond"]) == "Carlton"


def test_match_label_matches_a_part_of_a_grouped_label():
    assert match_label("Albert Park", [GROUPED, "Richmond"]) == GROUPED


def test_match_label_is_case_and_space_insensitive():
    assert match_label(" albert  park ", [GROUPED]) == GROUPED
    assert match_label("MIDDLE PARK", [GROUPED]) == GROUPED


def test_match_label_no_match_returns_none():
    assert match_label("Nowhere", [GROUPED, "Richmond"]) is None


def test_match_label_multiple_matches_logs_warning_and_returns_first_sorted(caplog):
    labels = ["Zeta-Albert Park", "Albert Park-Alpha"]
    with caplog.at_level(logging.WARNING):
        result = match_label("Albert Park", labels)
    assert result == "Albert Park-Alpha"
    assert "Albert Park" in caplog.text


async def test_resolve_area_nsw_returns_the_stripped_key_without_a_db_hit():
    """Passing None as the session proves NSW never queries the database:
    a DB hit here would raise AttributeError on session.execute."""
    assert await resolve_area(None, "NSW", "  2000 ") == "2000"


async def test_resolve_area_vic_returns_none_on_an_empty_table(db_session):
    assert await resolve_area(db_session, "VIC", "Carlton") is None
