from datetime import date
from decimal import Decimal

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models import Act
from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput, RentIncrease

AS_AT = date(2026, 7, 24)


@pytest.fixture
async def corpus_session():
    """A session against the dev store; skip when the corpus is not loaded.

    Uses a per-test engine because each test runs in its own event loop.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            act = (
                await session.execute(select(Act).where(Act.slug == "act-2010-042"))
            ).scalar_one_or_none()
        except (OSError, SQLAlchemyError, asyncpg.PostgresError):
            pytest.skip("NSW corpus store not reachable")
        if act is None:
            pytest.skip("NSW corpus not ingested")
        yield session
    await engine.dispose()


def lease(**kw) -> LeaseInput:
    base = {
        "rent_amount": Decimal(600),
        "rent_frequency": "weekly",
        "start_date": date(2026, 1, 1),
    }
    base.update(kw)
    return LeaseInput(**base)


async def test_bond_over_four_weeks_is_red(corpus_session):
    findings = await run_audit(corpus_session, "NSW", AS_AT, lease(bond_amount=Decimal(3000)))
    finding = next(f for f in findings if f.rule_id == "nsw.bond_max_4_weeks")
    assert finding.verdict == "red"
    assert finding.citations[0].section_no == "159"
    assert finding.evidence["computed"]["max_bond"] == "2400.00"


async def test_bond_at_cap_is_green(corpus_session):
    findings = await run_audit(corpus_session, "NSW", AS_AT, lease(bond_amount=Decimal(2400)))
    assert next(f for f in findings if f.rule_id == "nsw.bond_max_4_weeks").verdict == "green"


async def test_missing_bond_is_skipped(corpus_session):
    findings = await run_audit(corpus_session, "NSW", AS_AT, lease())
    finding = next(f for f in findings if f.rule_id == "nsw.bond_max_4_weeks")
    assert finding.verdict == "skipped"
    assert "bond_amount" in finding.skip_reason


async def _verdict(corpus_session, rule_id, as_at=AS_AT, **lease_kw):
    findings = await run_audit(corpus_session, "NSW", as_at, lease(**lease_kw))
    return next(f for f in findings if f.rule_id == rule_id)


async def test_holding_fee_over_one_week_is_red(corpus_session):
    finding = await _verdict(
        corpus_session, "nsw.holding_fee_max_1_week", holding_deposit_amount=Decimal(700)
    )
    assert finding.verdict == "red"
    assert finding.citations[0].section_no == "24"


async def test_holding_fee_at_cap_is_green(corpus_session):
    finding = await _verdict(
        corpus_session, "nsw.holding_fee_max_1_week", holding_deposit_amount=Decimal(600)
    )
    assert finding.verdict == "green"


async def test_missing_holding_fee_is_skipped(corpus_session):
    finding = await _verdict(corpus_session, "nsw.holding_fee_max_1_week")
    assert finding.verdict == "skipped"


async def test_two_increases_eight_months_apart_is_red(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.rent_increase_frequency",
        rent_increases=[
            {"effective_on": "2027-02-01", "new_amount": "620"},
            {"effective_on": "2027-10-01", "new_amount": "640"},
        ],
    )
    assert finding.verdict == "red"
    assert finding.citations[0].section_no == "41"


async def test_increases_thirteen_months_apart_is_green(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.rent_increase_frequency",
        rent_increases=[
            {"effective_on": "2027-02-01", "new_amount": "620"},
            {"effective_on": "2028-03-01", "new_amount": "640"},
        ],
    )
    assert finding.verdict == "green"


async def test_no_increases_frequency_skipped(corpus_session):
    finding = await _verdict(corpus_session, "nsw.rent_increase_frequency")
    assert finding.verdict == "skipped"


async def test_first_increase_within_first_year_is_red(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.rent_increase_first_year",
        rent_increases=[{"effective_on": "2026-07-01", "new_amount": "620"}],
    )
    assert finding.verdict == "red"


async def test_first_increase_after_first_year_is_green(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.rent_increase_first_year",
        rent_increases=[{"effective_on": "2027-02-01", "new_amount": "620"}],
    )
    assert finding.verdict == "green"


async def test_increase_with_59_days_notice_is_red(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.rent_increase_notice",
        rent_increases=[
            {"effective_on": "2027-06-01", "new_amount": "620", "notice_given_on": "2027-04-04"}
        ],
    )
    assert finding.verdict == "red"


async def test_increase_with_60_days_notice_is_green(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.rent_increase_notice",
        rent_increases=[
            {"effective_on": "2027-06-01", "new_amount": "620", "notice_given_on": "2027-04-02"}
        ],
    )
    assert finding.verdict == "green"


async def test_increase_without_notice_date_is_green(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.rent_increase_notice",
        rent_increases=[{"effective_on": "2027-06-01", "new_amount": "620"}],
    )
    assert finding.verdict == "green"


async def test_fixed_term_increase_without_disclosure_is_red(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.fixed_term_increase_disclosure",
        as_at=date(2024, 6, 1),
        start_date=date(2024, 1, 1),
        end_date=date(2025, 1, 1),
        rent_increases=[{"effective_on": "2024-09-01", "new_amount": "620"}],
    )
    assert finding.verdict == "red"
    assert finding.citations[0].section_no == "42"


async def test_fixed_term_increase_with_disclosure_is_green(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.fixed_term_increase_disclosure",
        as_at=date(2024, 6, 1),
        start_date=date(2024, 1, 1),
        end_date=date(2025, 1, 1),
        rent_increases=[{"effective_on": "2024-09-01", "new_amount": "620"}],
        fixed_term_increase_in_agreement=True,
    )
    assert finding.verdict == "green"


async def test_two_year_fixed_term_needs_no_disclosure(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.fixed_term_increase_disclosure",
        as_at=date(2024, 6, 1),
        start_date=date(2024, 1, 1),
        end_date=date(2026, 1, 1),
        rent_increases=[{"effective_on": "2024-09-01", "new_amount": "620"}],
    )
    assert finding.verdict == "green"


async def test_disclosure_rule_inactive_after_repeal(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.fixed_term_increase_disclosure",
        start_date=date(2026, 1, 1),
        end_date=date(2027, 1, 1),
        rent_increases=[{"effective_on": "2026-09-01", "new_amount": "620"}],
    )
    assert finding.verdict == "skipped"
    assert "not active" in finding.skip_reason


async def test_other_security_present_is_red(corpus_session):
    finding = await _verdict(
        corpus_session, "nsw.no_other_security", other_security_amount=Decimal(500)
    )
    assert finding.verdict == "red"
    assert finding.citations[0].section_no == "160"


async def test_zero_other_security_is_green(corpus_session):
    finding = await _verdict(
        corpus_session, "nsw.no_other_security", other_security_amount=Decimal(0)
    )
    assert finding.verdict == "green"


async def test_break_fee_over_four_weeks_is_red(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.break_fee_cap",
        end_date=date(2027, 1, 1),
        break_fee_amount=Decimal(2500),
    )
    assert finding.verdict == "red"
    assert finding.citations[0].section_no == "107"


async def test_break_fee_at_four_weeks_is_green(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.break_fee_cap",
        end_date=date(2027, 1, 1),
        break_fee_amount=Decimal(2400),
    )
    assert finding.verdict == "green"


async def test_break_fee_scale_not_applied_over_three_years(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.break_fee_cap",
        end_date=date(2030, 1, 1),
        break_fee_amount=Decimal(5000),
    )
    assert finding.verdict == "green"


async def test_break_fee_without_end_date_is_skipped(corpus_session):
    finding = await _verdict(corpus_session, "nsw.break_fee_cap", break_fee_amount=Decimal(2500))
    assert finding.verdict == "skipped"
    assert "end_date" in finding.skip_reason


async def test_leap_spanning_365_day_gap_is_red(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.rent_increase_frequency",
        rent_increases=[
            RentIncrease(effective_on=date(2023, 3, 1), new_amount=Decimal(650)),
            RentIncrease(effective_on=date(2024, 2, 29), new_amount=Decimal(700)),
        ],
    )
    assert finding.verdict == "red"
    assert "365" in finding.summary


async def test_first_year_leap_boundary(corpus_session):
    red = await _verdict(
        corpus_session,
        "nsw.rent_increase_first_year",
        start_date=date(2023, 3, 1),
        rent_increases=[RentIncrease(effective_on=date(2024, 2, 29), new_amount=Decimal(650))],
    )
    assert red.verdict == "red"
    green = await _verdict(
        corpus_session,
        "nsw.rent_increase_first_year",
        start_date=date(2023, 3, 1),
        rent_increases=[RentIncrease(effective_on=date(2024, 3, 1), new_amount=Decimal(650))],
    )
    assert green.verdict == "green"


async def test_disclosure_leap_term_is_under_two_years(corpus_session):
    red = await _verdict(
        corpus_session,
        "nsw.fixed_term_increase_disclosure",
        as_at=date(2024, 6, 1),
        start_date=date(2023, 3, 1),
        end_date=date(2025, 2, 28),
        rent_increases=[RentIncrease(effective_on=date(2024, 1, 15), new_amount=Decimal(650))],
        fixed_term_increase_in_agreement=False,
    )
    assert red.verdict == "red"
    green = await _verdict(
        corpus_session,
        "nsw.fixed_term_increase_disclosure",
        as_at=date(2024, 6, 1),
        start_date=date(2023, 3, 1),
        end_date=date(2025, 3, 1),
        rent_increases=[RentIncrease(effective_on=date(2024, 1, 15), new_amount=Decimal(650))],
        fixed_term_increase_in_agreement=False,
    )
    assert green.verdict == "green"


async def test_break_fee_scale_applies_on_the_exact_anniversary(corpus_session):
    red = await _verdict(
        corpus_session,
        "nsw.break_fee_cap",
        start_date=date(2023, 3, 1),
        end_date=date(2026, 3, 1),
        break_fee_amount=Decimal(5000),
    )
    assert red.verdict == "red"
    green = await _verdict(
        corpus_session,
        "nsw.break_fee_cap",
        start_date=date(2023, 3, 1),
        end_date=date(2026, 3, 2),
        break_fee_amount=Decimal(5000),
    )
    assert green.verdict == "green"
