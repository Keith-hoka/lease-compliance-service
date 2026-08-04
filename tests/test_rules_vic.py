from datetime import date
from decimal import Decimal

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models import Act
from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput, RentIncrease

AS_AT = date(2026, 8, 4)


@pytest.fixture
async def corpus_session():
    """A session against the dev store; skip when the VIC corpus is not loaded."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            act = (
                await session.execute(
                    select(Act).where(Act.slug == "residential-tenancies-act-1997")
                )
            ).scalar_one_or_none()
        except (OSError, SQLAlchemyError, asyncpg.PostgresError):
            pytest.skip("VIC corpus store not reachable")
        if act is None:
            pytest.skip("VIC corpus not ingested")
        yield session
    await engine.dispose()


def lease(**overrides) -> LeaseInput:
    base = {
        "rent_amount": Decimal(2000),
        "rent_frequency": "monthly",
        "start_date": date(2026, 1, 1),
    }
    base.update(overrides)
    return LeaseInput(**base)


async def run(corpus_session, lease_input, as_at=AS_AT):
    findings = await run_audit(corpus_session, "VIC", as_at, lease_input)
    return {f.rule_id: f for f in findings}


async def test_bond_over_one_month_is_red(corpus_session):
    findings = await run(
        corpus_session,
        lease(rent_amount=Decimal(500), rent_frequency="weekly", bond_amount=Decimal(2500)),
    )
    f = findings["vic.bond_max_1_month"]
    assert f.verdict == "red"
    assert "2166.67" in f.summary
    assert any(c.section_no == "31" for c in f.citations)


async def test_bond_exactly_one_month_is_green(corpus_session):
    findings = await run(
        corpus_session,
        lease(rent_amount=Decimal(500), rent_frequency="weekly", bond_amount=Decimal("2166.67")),
    )
    assert findings["vic.bond_max_1_month"].verdict == "green"


async def test_bond_cap_skipped_above_rent_threshold(corpus_session):
    findings = await run(
        corpus_session,
        lease(rent_amount=Decimal(901), rent_frequency="weekly", bond_amount=Decimal(50000)),
    )
    f = findings["vic.bond_max_1_month"]
    assert f.verdict == "skipped"
    assert "does not apply" in f.summary
    assert "does not apply" in f.skip_reason


async def test_bond_cap_holds_at_exactly_900_weekly(corpus_session):
    findings = await run(
        corpus_session,
        lease(rent_amount=Decimal(900), rent_frequency="weekly", bond_amount=Decimal(5000)),
    )
    assert findings["vic.bond_max_1_month"].verdict == "red"


async def test_bond_monthly_lease_at_exact_cap_is_green(corpus_session):
    findings = await run(corpus_session, lease(bond_amount=Decimal(2000)))
    assert findings["vic.bond_max_1_month"].verdict == "green"


async def test_bond_monthly_lease_one_cent_over_cap_is_red(corpus_session):
    findings = await run(corpus_session, lease(bond_amount=Decimal("2000.01")))
    assert findings["vic.bond_max_1_month"].verdict == "red"


async def test_bond_fortnightly_lease_at_exact_cap_is_green(corpus_session):
    findings = await run(
        corpus_session,
        lease(
            rent_amount=Decimal(1000), rent_frequency="fortnightly", bond_amount=Decimal("2166.67")
        ),
    )
    assert findings["vic.bond_max_1_month"].verdict == "green"


async def test_bond_fortnightly_lease_one_cent_over_cap_is_red(corpus_session):
    findings = await run(
        corpus_session,
        lease(
            rent_amount=Decimal(1000), rent_frequency="fortnightly", bond_amount=Decimal("2166.68")
        ),
    )
    assert findings["vic.bond_max_1_month"].verdict == "red"


async def test_bond_missing_is_skipped(corpus_session):
    findings = await run(corpus_session, lease())
    assert findings["vic.bond_max_1_month"].verdict == "skipped"


async def test_bond_rule_skips_before_reg_17_exists(corpus_session):
    findings = await run(corpus_session, lease(bond_amount=Decimal(9999)), as_at=date(2020, 6, 1))
    f = findings["vic.bond_max_1_month"]
    assert f.verdict == "skipped"
    assert "not in force" in f.skip_reason


async def test_advance_over_one_month_is_red(corpus_session):
    findings = await run(corpus_session, lease(rent_in_advance_amount=Decimal(2500)))
    f = findings["vic.advance_max_1_month"]
    assert f.verdict == "red"
    assert any(c.section_no == "40" for c in f.citations)


async def test_advance_within_cap_is_green(corpus_session):
    findings = await run(corpus_session, lease(rent_in_advance_amount=Decimal(2000)))
    assert findings["vic.advance_max_1_month"].verdict == "green"


async def test_advance_skipped_above_threshold(corpus_session):
    findings = await run(
        corpus_session,
        lease(
            rent_amount=Decimal(1000),
            rent_frequency="weekly",
            rent_in_advance_amount=Decimal(50000),
        ),
    )
    f = findings["vic.advance_max_1_month"]
    assert f.verdict == "skipped"
    assert "does not apply" in f.skip_reason


async def test_increase_interval_below_12_months_is_red(corpus_session):
    findings = await run(
        corpus_session,
        lease(
            rent_increases=[
                RentIncrease(effective_on=date(2025, 1, 1), new_amount=Decimal(2000)),
                RentIncrease(effective_on=date(2025, 12, 1), new_amount=Decimal(2100)),
            ]
        ),
    )
    f = findings["vic.rent_increase_frequency"]
    assert f.verdict == "red"
    assert "334" in f.summary
    assert f.evidence["computed"]["gaps_days"] == [334]
    assert any(c.section_no == "44" for c in f.citations)


async def test_increase_interval_of_exactly_365_days_is_green(corpus_session):
    findings = await run(
        corpus_session,
        lease(
            rent_increases=[
                RentIncrease(effective_on=date(2025, 1, 1), new_amount=Decimal(2000)),
                RentIncrease(effective_on=date(2026, 1, 1), new_amount=Decimal(2100)),
            ]
        ),
    )
    f = findings["vic.rent_increase_frequency"]
    assert f.verdict == "green"
    assert f.evidence["computed"]["gaps_days"] == [365]


async def test_frequency_rule_skips_before_commencement(corpus_session):
    findings = await run(
        corpus_session,
        lease(
            rent_increases=[
                RentIncrease(effective_on=date(2020, 1, 1), new_amount=Decimal(2000)),
                RentIncrease(effective_on=date(2020, 6, 1), new_amount=Decimal(2100)),
            ]
        ),
        as_at=date(2020, 1, 1),
    )
    f = findings["vic.rent_increase_frequency"]
    assert f.verdict == "skipped"
    assert "not active" in f.skip_reason


async def test_frequency_check_green_with_no_rent_increases(corpus_session):
    findings = await run(corpus_session, lease(rent_increases=[]))
    assert findings["vic.rent_increase_frequency"].verdict == "green"


async def test_fixed_term_increase_without_provision_is_red(corpus_session):
    findings = await run(
        corpus_session,
        lease(
            end_date=date(2027, 1, 1),
            rent_increases=[RentIncrease(effective_on=date(2026, 6, 1), new_amount=Decimal(2100))],
            fixed_term_increase_in_agreement=False,
        ),
    )
    f = findings["vic.fixed_term_increase_provision"]
    assert f.verdict == "red"


async def test_fixed_term_increase_with_provision_is_green(corpus_session):
    findings = await run(
        corpus_session,
        lease(
            end_date=date(2027, 1, 1),
            rent_increases=[RentIncrease(effective_on=date(2026, 6, 1), new_amount=Decimal(2100))],
            fixed_term_increase_in_agreement=True,
        ),
    )
    assert findings["vic.fixed_term_increase_provision"].verdict == "green"


async def test_increase_outside_the_term_is_green(corpus_session):
    findings = await run(
        corpus_session,
        lease(
            end_date=date(2026, 3, 1),
            rent_increases=[RentIncrease(effective_on=date(2026, 6, 1), new_amount=Decimal(2100))],
            fixed_term_increase_in_agreement=False,
        ),
    )
    assert findings["vic.fixed_term_increase_provision"].verdict == "green"


async def test_nsw_audit_unchanged_and_vic_returns_only_vic_rules(corpus_session):
    vic = await run(corpus_session, lease(bond_amount=Decimal(2500)))
    assert len(vic) == 4
    assert all(rule_id.startswith("vic.") for rule_id in vic)
    nsw_findings = await run_audit(
        corpus_session,
        "NSW",
        date(2026, 7, 24),
        LeaseInput(
            rent_amount=Decimal(600),
            rent_frequency="weekly",
            start_date=date(2026, 1, 1),
            bond_amount=Decimal(3000),
        ),
    )
    by_id = {f.rule_id: f for f in nsw_findings}
    assert by_id["nsw.bond_max_4_weeks"].verdict == "red"
    assert all(rule_id.startswith("nsw.") for rule_id in by_id)
