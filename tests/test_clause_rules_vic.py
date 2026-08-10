from datetime import date

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.clause_audit.rules import resolve_rule, rule_active
from app.clause_audit.rules_vic import VIC_COMMENCED, VIC_PROHIBITED_RULES
from app.models import Act

AS_AT = date(2026, 8, 5)


@pytest.fixture
async def corpus_session():
    """A session against the dev store; skip when the VIC corpus is absent."""
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


def test_sixteen_unique_vic_rules():
    ids = [r.rule_id for r in VIC_PROHIBITED_RULES]
    assert len(ids) == 16
    assert len(set(ids)) == 16
    assert all(rule_id.startswith("vic.clause.") for rule_id in ids)
    assert all(r.jurisdiction == "VIC" for r in VIC_PROHIBITED_RULES)
    assert all(r.family == "prohibited" for r in VIC_PROHIBITED_RULES)


def test_vic_rules_inactive_before_commencement():
    assert all(not rule_active(r, date(2021, 3, 28)) for r in VIC_PROHIBITED_RULES)
    assert all(rule_active(r, VIC_COMMENCED) for r in VIC_PROHIBITED_RULES)


def test_nsw_rules_carry_their_jurisdiction():
    from app.clause_audit.rules import PROHIBITED_RULES

    assert all(r.jurisdiction == "NSW" for r in PROHIBITED_RULES)


async def test_every_vic_rule_resolves_on_the_corpus(corpus_session):
    for rule in VIC_PROHIBITED_RULES:
        citation = await resolve_rule(corpus_session, rule, AS_AT)
        assert citation is not None, rule.rule_id
        assert citation.section_no == rule.ref.section_no


async def test_vic_rules_do_not_resolve_before_commencement(corpus_session):
    citation = await resolve_rule(corpus_session, VIC_PROHIBITED_RULES[0], date(2021, 3, 28))
    assert citation is None


def test_vic_golden_covers_every_rule():
    from tests.golden.clauses_vic import VIC_PROHIBITED_CASES

    by_rule: dict[str, list] = {}
    for case in VIC_PROHIBITED_CASES:
        by_rule.setdefault(case.rule_id, []).append(case)
    rule_ids = {r.rule_id for r in VIC_PROHIBITED_RULES}
    assert set(by_rule) == rule_ids
    for rule_id, cases in by_rule.items():
        reds = [c for c in cases if c.expected == "red"]
        assert len(reds) >= 3, rule_id
    greens_required = {
        "vic.clause.professional_cleaning_required",
        "vic.clause.professional_cleaning_cost",
        "vic.clause.third_party_services",
        "vic.clause.costly_payment_method",
        "vic.clause.fixed_break_fees",
        "vic.clause.breach_penalty",
    }
    for rule_id in greens_required:
        assert any(c.expected == "green" for c in by_rule[rule_id]), rule_id
    case_ids = [c.case_id for c in VIC_PROHIBITED_CASES]
    assert len(case_ids) == len(set(case_ids))
