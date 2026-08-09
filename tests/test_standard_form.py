"""Deterministic layer of the standard-form comparison: no DB, no LLM."""

import uuid
from datetime import date

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.clause_audit.standard_form import (
    CONTAINMENT_THRESHOLD,
    NSW_REG_SLUG,
    VIC_REGS_SLUG,
    FormTerm,
    containment,
    fetch_form_terms,
    normalize,
    screen_terms,
)
from app.models import Act
from app.schemas.clause_audit import ClauseLeaseInput


def make_term(no: str, heading: str, body: str) -> FormTerm:
    return FormTerm(
        rule_id=f"nsw.clause.sf_t{no.lower()}",
        section_no=f"S1-T{no}",
        heading=heading,
        body=body,
        section_id=uuid.uuid4(),
        act_slug="sl-2019-0629",
        act_duty=None,
    )


def test_normalize_strips_placeholders_and_unifies_punctuation():
    raw = "The tenant agrees—to pay rent of [insert amount] “on time” *weekly *fortnightly"
    cleaned = normalize(raw)
    assert "[insert" not in cleaned
    assert "—" not in cleaned and "“" not in cleaned
    assert "*" not in cleaned
    assert cleaned == cleaned.lower()


def test_containment_full_copy_is_high_and_reordering_immune():
    term = (
        "The landlord agrees to provide the residential premises in a "
        "reasonable state of cleanliness and fit for habitation by the tenant."
    )
    lease = (
        "CLAUSE 40. Unrelated preamble text here. "
        + term
        + " CLAUSE 41. More unrelated text follows the copied term."
    )
    assert containment(term, lease) >= CONTAINMENT_THRESHOLD


def test_containment_drops_on_alteration():
    term = (
        "The landlord agrees to give the tenant at least 7 days written "
        "notice before entering the premises for a routine inspection of "
        "the premises during the tenancy period."
    )
    altered = term.replace("7 days", "no")
    assert containment(term, altered) < 1.0


def test_screen_partitions_verbatim_from_residual_and_short_terms():
    long_body = (
        "The tenant agrees to pay the rent on time and in the manner "
        "stated in this agreement for the duration of the tenancy period."
    )
    copied = make_term("1", "RENT", long_body)
    missing = make_term(
        "2",
        "POSSESSION",
        (
            "The landlord agrees to give the tenant vacant possession of the "
            "premises on the day the tenant is entitled to enter into occupation."
        ),
    )
    short = make_term("3", "TERMINATION", "See the Act.")
    document = f"1. {long_body} 2. Something entirely different about parking."
    green, residual = screen_terms([copied, missing, short], document)
    assert [t.section_no for t, _ in green] == ["S1-T1"]
    assert green[0][1] >= CONTAINMENT_THRESHOLD
    assert [t.section_no for t in residual] == ["S1-T2", "S1-T3"]


@pytest.fixture
async def corpus_session():
    """A session against the dev store; skip when the form corpora aren't loaded."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            slugs = (
                (
                    await session.execute(
                        select(Act.slug).where(Act.slug.in_([NSW_REG_SLUG, VIC_REGS_SLUG]))
                    )
                )
                .scalars()
                .all()
            )
        except (OSError, SQLAlchemyError, asyncpg.PostgresError):
            pytest.skip("corpus store not reachable")
        if {NSW_REG_SLUG, VIC_REGS_SLUG} - set(slugs):
            pytest.skip("standard-form corpus not ingested")
        yield session
    await engine.dispose()


async def test_fetch_nsw_terms_today(corpus_session):
    terms, note = await fetch_form_terms(corpus_session, "NSW", date(2026, 8, 9), None)
    assert len(terms) == 59
    assert note is None
    assert terms[0].rule_id == "nsw.clause.sf_t1"
    assert terms[0].section_no == "S1-T1"
    by_no = {t.section_no: t for t in terms}
    assert by_no["S1-T19"].act_duty == "52"
    assert by_no["S1-T5"].act_duty is None


async def test_fetch_vic_form1_default_and_note(corpus_session):
    terms, note = await fetch_form_terms(corpus_session, "VIC", date(2026, 8, 9), None)
    assert len(terms) == 32
    assert terms[0].rule_id == "vic.clause.sf_f1_t1"
    assert note is not None and "Form 1" in note


async def test_fetch_vic_form2_for_long_lease(corpus_session):
    lease = ClauseLeaseInput(start_date=date(2020, 1, 1), end_date=date(2026, 1, 2))
    terms, note = await fetch_form_terms(corpus_session, "VIC", date(2026, 8, 9), lease)
    assert len(terms) == 40
    assert terms[0].rule_id == "vic.clause.sf_f2_t1"
    assert note is None


async def test_fetch_is_point_in_time(corpus_session):
    terms, _ = await fetch_form_terms(corpus_session, "VIC", date(2025, 11, 24), None)
    nos = {t.section_no for t in terms}
    assert "S1-F1-T30A" not in nos
    terms, _ = await fetch_form_terms(corpus_session, "VIC", date(2025, 11, 25), None)
    nos = {t.section_no for t in terms}
    assert "S1-F1-T30A" in nos
