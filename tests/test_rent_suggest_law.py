from datetime import date
from decimal import Decimal

from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act
from app.rent_suggest.law import RENT_RULE_MARKERS, from_weekly, law_card
from app.schemas.lease import LeaseInput, RentIncrease


def test_from_weekly_inverts_to_weekly():
    assert from_weekly(Decimal(600), "weekly") == Decimal(600)
    assert from_weekly(Decimal(600), "fortnightly") == Decimal(1200)
    assert from_weekly(Decimal(600), "monthly") == Decimal(2600)


async def test_law_card_green_when_increase_is_lawful(db_session):
    await _seed_nsw_corpus(db_session)
    lease = LeaseInput(
        rent_amount=Decimal(600),
        rent_frequency="weekly",
        start_date=date(2024, 10, 1),
        end_date=date(2026, 9, 30),
    )
    card = await law_card(
        db_session, "NSW", date(2026, 1, 1), lease, date(2026, 10, 1), Decimal(630)
    )
    assert card.blocked is False
    assert card.findings and all(
        any(m in f.rule_id for m in RENT_RULE_MARKERS) for f in card.findings
    )
    assert {f.verdict for f in card.findings} <= {"green", "skipped"}


async def test_law_card_drops_notice_findings(db_session):
    """The hypothetical increase never carries notice_given_on, so a notice
    rule is always vacuously green - drop it rather than show a misleading
    green row next to a rent the landlord has not actually noticed yet.
    """
    await _seed_nsw_corpus(db_session)
    lease = LeaseInput(
        rent_amount=Decimal(600),
        rent_frequency="weekly",
        start_date=date(2024, 10, 1),
        end_date=date(2026, 9, 30),
    )
    card = await law_card(
        db_session, "NSW", date(2026, 1, 1), lease, date(2026, 10, 1), Decimal(630)
    )
    assert all(not f.rule_id.endswith("_notice") for f in card.findings)


async def test_law_card_blocked_by_frequency_rule(db_session):
    await _seed_nsw_corpus(db_session)
    lease = LeaseInput(
        rent_amount=Decimal(600),
        rent_frequency="weekly",
        start_date=date(2024, 10, 1),
        end_date=date(2026, 9, 30),
        rent_increases=[RentIncrease(effective_on=date(2026, 4, 1), new_amount=Decimal(600))],
    )
    card = await law_card(
        db_session, "NSW", date(2026, 1, 1), lease, date(2026, 10, 1), Decimal(630)
    )
    assert card.blocked is True
    red = [f for f in card.findings if f.verdict == "red"]
    assert red and red[0].rule_id == "nsw.rent_increase_frequency"


async def _seed_nsw_corpus(db_session):
    act = Act(
        jurisdiction="NSW",
        slug="act-2010-042",
        title="Residential Tenancies Act 2010",
        source_url="x",
    )
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session,
        act.id,
        date(2026, 1, 1),
        [
            ParsedSection("41", "Rent", "Body", None, None),
        ],
    )
