from datetime import date

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models import Act
from app.services.legislation import section_at

REG = "sl-2019-0629"
FIRST_VERSION = date(2019, 12, 16)


@pytest.fixture
async def regulation_session():
    """A session against the dev store; skip when the Regulation is not loaded.

    Mirrors corpus_session in tests/test_rules_nsw.py with its own slug guard.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            act = (await session.execute(select(Act).where(Act.slug == REG))).scalar_one_or_none()
        except (OSError, SQLAlchemyError, asyncpg.PostgresError):
            pytest.skip("corpus store not reachable")
        if act is None:
            pytest.skip("Regulation corpus not ingested")
        yield session
    await engine.dispose()


async def test_regulation_clause_resolves_across_time(regulation_session):
    early = await section_at(regulation_session, REG, "1", date(2020, 1, 1))
    now = await section_at(regulation_session, REG, "1", date(2026, 7, 28))
    assert early is not None
    assert now is not None
    assert early.valid_from == FIRST_VERSION


async def test_regulation_before_first_version_is_none(regulation_session):
    assert (await section_at(regulation_session, REG, "1", date(2019, 1, 1))) is None


async def test_standard_form_term_resolves(regulation_session):
    """The dump-restored corpus must carry schedule rows, not just body sections."""
    term = await section_at(regulation_session, REG, "S1-T1", date(2026, 8, 7))
    assert term is not None
    assert term.heading == "RIGHT TO OCCUPY THE PREMISES"
    assert term.division == "Schedule 1 Standard Form Agreement"
