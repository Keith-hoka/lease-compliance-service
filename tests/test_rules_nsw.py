from datetime import date
from decimal import Decimal

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models import Act
from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput

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
