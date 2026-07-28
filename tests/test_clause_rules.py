from datetime import date

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.clause_audit.rules import (
    MANDATORY_RULES,
    PROHIBITED_RULES,
    ClauseRule,
    resolve_rule,
    rule_active,
    statutory_texts,
)
from app.models import Act
from app.rules.base import SectionRef

AS_AT = date(2026, 7, 28)


def test_rule_lists_are_populated_and_distinct():
    ids = [r.rule_id for r in PROHIBITED_RULES + MANDATORY_RULES]
    assert len(ids) == len(set(ids))
    assert any(r.rule_id == "nsw.clause.carpet_cleaning" for r in PROHIBITED_RULES)
    assert all(r.family == "prohibited" for r in PROHIBITED_RULES)
    assert all(r.family == "mandatory" for r in MANDATORY_RULES)
    assert 5 <= len(MANDATORY_RULES) <= 8


def test_rule_active_windows():
    rule = ClauseRule(
        rule_id="nsw.clause.example",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=date(2020, 1, 1),
        applies_to=date(2021, 1, 1),
        question="x",
    )
    assert not rule_active(rule, date(2019, 12, 31))
    assert rule_active(rule, date(2020, 6, 1))
    assert not rule_active(rule, date(2021, 1, 1))


@pytest.fixture
async def corpus_session():
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
            pytest.skip("corpus store not reachable")
        if act is None:
            pytest.skip("corpus not ingested")
        yield session
    await engine.dispose()


async def test_every_rule_resolves_on_the_corpus(corpus_session):
    for rule in PROHIBITED_RULES + MANDATORY_RULES:
        citation = await resolve_rule(corpus_session, rule, AS_AT)
        assert citation is not None, rule.rule_id
        assert citation.as_at == AS_AT


async def test_statutory_texts_dedupes_shared_sections(corpus_session):
    texts = await statutory_texts(corpus_session, PROHIBITED_RULES, AS_AT)
    refs = {(r.ref.act_slug, r.ref.section_no) for r in PROHIBITED_RULES}
    assert set(texts) == refs
    assert "Prohibited terms" in texts[("act-2010-042", "19")]


def test_golden_covers_every_rule_active_at_eval_date():
    from tests.golden.clauses import MANDATORY_CASES, PROHIBITED_CASES

    covered = {c.rule_id for c in PROHIBITED_CASES}
    assert covered == {r.rule_id for r in PROHIBITED_RULES if rule_active(r, AS_AT)}
    mandatory_covered = {c.rule_id for c in MANDATORY_CASES}
    assert mandatory_covered == {r.rule_id for r in MANDATORY_RULES if rule_active(r, AS_AT)}
    for case in PROHIBITED_CASES + MANDATORY_CASES:
        assert case.expected in ("red", "green")


async def test_contractor_reg_rule_resolves_historical_text(corpus_session):
    reg_rule = next(
        r for r in PROHIBITED_RULES if r.rule_id == "nsw.clause.specified_contractor_reg"
    )
    act_rule = next(r for r in PROHIBITED_RULES if r.rule_id == "nsw.clause.specified_contractor")
    historical = date(2022, 1, 1)
    assert rule_active(reg_rule, historical) and not rule_active(act_rule, historical)
    assert not rule_active(reg_rule, AS_AT) and rule_active(act_rule, AS_AT)
    citation = await resolve_rule(corpus_session, reg_rule, historical)
    assert citation is not None
    texts = await statutory_texts(corpus_session, [reg_rule], historical)
    assert "specified person" in texts[("sl-2019-0629", "5")]
