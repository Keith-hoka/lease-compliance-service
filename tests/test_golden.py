from datetime import date
from decimal import Decimal

import pytest

from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act
from app.rent_suggest.anchor import anchor, market_cell
from app.rent_suggest.law import law_card
from app.rules.base import to_weekly_rent
from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput
from tests.golden.leases import GOLDEN
from tests.golden.rent_suggestions import SCENARIOS, money_figures
from tests.test_rules_nsw import corpus_session  # noqa: F401  (reuse the skip-guard fixture)

AS_AT = date(2026, 7, 24)
_RS_CORPUS_FROM = date(2011, 1, 1)


@pytest.mark.parametrize("case_id,lease_kwargs,expected", GOLDEN, ids=[g[0] for g in GOLDEN])
async def test_golden_case(corpus_session, case_id, lease_kwargs, expected):  # noqa: F811
    findings = await run_audit(corpus_session, "NSW", AS_AT, LeaseInput(**lease_kwargs))
    actual = {f.rule_id: f.verdict for f in findings if f.verdict != "skipped"}
    assert actual == expected


async def test_same_lease_differs_across_reform(corpus_session):  # noqa: F811
    """The frequency rule is inactive before its commencement and red after."""
    from app.rules.nsw import FREQ_COMMENCED

    lease_kwargs = {
        "rent_amount": "600",
        "rent_frequency": "weekly",
        "start_date": "2000-01-01",
        "rent_increases": [
            {"effective_on": "2001-01-01", "new_amount": "620"},
            {"effective_on": "2001-06-01", "new_amount": "640"},
        ],
    }
    before = await run_audit(
        corpus_session,
        "NSW",
        FREQ_COMMENCED.replace(year=FREQ_COMMENCED.year - 1),
        LeaseInput(**lease_kwargs),
    )
    after = await run_audit(corpus_session, "NSW", AS_AT, LeaseInput(**lease_kwargs))
    freq_before = next(f for f in before if f.rule_id == "nsw.rent_increase_frequency")
    freq_after = next(f for f in after if f.rule_id == "nsw.rent_increase_frequency")
    assert freq_before.verdict == "skipped"
    assert freq_after.verdict == "red"


async def _seed_rent_suggestion_corpus(session):
    """Just enough Act/Section corpus for the rent-increase rules to resolve.

    Sections stay open (valid_to=None) from _RS_CORPUS_FROM: each rule's own
    applies_from/applies_to constants gate when it is actually active, so
    this only needs to satisfy the citation lookup in app.services.legislation.
    """
    nsw = Act(
        jurisdiction="NSW",
        slug="act-2010-042",
        title="Residential Tenancies Act 2010",
        source_url="x",
    )
    vic = Act(
        jurisdiction="VIC",
        slug="residential-tenancies-act-1997",
        title="Residential Tenancies Act 1997",
        source_url="x",
    )
    session.add_all([nsw, vic])
    await session.flush()
    await load_version(
        session,
        nsw.id,
        _RS_CORPUS_FROM,
        [
            ParsedSection("41", "Rent increases", "Body", None, None),
            ParsedSection("42", "Fixed term increases", "Body", None, None),
        ],
    )
    await load_version(
        session,
        vic.id,
        _RS_CORPUS_FROM,
        [ParsedSection("44", "Rent increases", "Body", None, None)],
    )


async def test_rent_suggestion_scenarios_are_deterministic(db_session):
    """anchor() and law_card() reproduce every golden scenario, with no model."""
    await _seed_rent_suggestion_corpus(db_session)
    for scenario in SCENARIOS:
        db_session.add_all(scenario.market_rows)
        await db_session.flush()
        current = to_weekly_rent(scenario.lease.rent_amount, scenario.lease.rent_frequency)
        cell = await market_cell(
            db_session,
            scenario.jurisdiction,
            scenario.property.area_key,
            scenario.property.dwelling_type,
            scenario.property.bedrooms,
        )
        anchored = anchor(current, scenario.jurisdiction, cell)
        assert anchored.gap == scenario.expected_gap, scenario.name
        assert (anchored.low, anchored.high) == scenario.expected_range, scenario.name

        midpoint = (anchored.low + anchored.high) / 2
        law = await law_card(
            db_session,
            scenario.jurisdiction,
            scenario.as_at,
            scenario.lease,
            scenario.renewal_start,
            midpoint,
        )
        is_hold = law.blocked or anchored.gap == "below_current"
        assert is_hold == (scenario.direction == "hold"), scenario.name


def test_money_figures_extracts_dollar_and_plain_numbers():
    text = "The market band is $650 to 700, based on a 2026 survey of 1,250 listings."
    assert money_figures(text) == {Decimal(650), Decimal(700), Decimal(2026), Decimal(1250)}


def test_money_figures_keeps_decimal_cents():
    text = "Suggested weekly rent of $715.50 sits within the median of $760."
    assert money_figures(text) == {Decimal("715.50"), Decimal(760)}
