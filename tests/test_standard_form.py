"""Deterministic layer of the standard-form comparison: no DB, no LLM."""

import uuid
from dataclasses import replace
from datetime import date

import asyncpg
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.clause_audit.document import DocumentInput
from app.clause_audit.standard_form import (
    CONTAINMENT_THRESHOLD,
    NSW_REG_SLUG,
    VIC_REGS_SLUG,
    FormTerm,
    containment,
    fetch_form_terms,
    normalize,
    run_standard_form,
    screen_terms,
)
from app.llm.prompts import STANDARD_FORM_GUIDANCE, standard_form_instruction
from app.llm.schemas import standard_form_output_model
from app.models import Act
from app.schemas.clause_audit import ClauseLeaseInput
from tests.golden.standard_form import ALTERATIONS, build_altered, build_verbatim, render_term


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
    tokens = cleaned.split()
    assert "agrees" in tokens and "to" in tokens
    assert "agrees-to" not in tokens


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
    assert containment(term, altered) < CONTAINMENT_THRESHOLD


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
    # Synthetic VIC-table-content case: heading (10 tokens) padding pushes
    # heading+body to 15 tokens, but the body alone (5 tokens) is under
    # MIN_SCREEN_TOKENS, so this must land in residual even though the
    # document contains its heading and body verbatim (a naive
    # heading+body-only length check would wrongly screen it green).
    table_heading = "LANDLORD AGREES TO PROVIDE CERTAIN ADDITIONAL FACILITIES AND SERVICES TODAY"
    table_body = "See the attached schedule table."
    table_like = make_term("4", table_heading, table_body)
    document = (
        f"1. {long_body} 2. Something entirely different about parking. "
        f"4. {table_heading} {table_body}"
    )
    green, residual = screen_terms([copied, missing, short, table_like], document)
    assert [t.section_no for t, _ in green] == ["S1-T1"]
    assert green[0][1] >= CONTAINMENT_THRESHOLD
    assert [t.section_no for t in residual] == ["S1-T2", "S1-T3", "S1-T4"]


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


async def test_fetch_orders_letter_suffixed_terms_next_to_their_base(corpus_session):
    """A letter-suffixed term (T30A) sorts next to its base number, not last."""
    terms, _ = await fetch_form_terms(corpus_session, "VIC", date(2026, 8, 9), None)
    nos = [t.section_no for t in terms]
    assert nos.index("S1-F1-T30") < nos.index("S1-F1-T30A") < nos.index("S1-F1-T31")


def test_standard_form_instruction_contains_terms_and_rubric():
    terms = [
        make_term("1", "RENT", "The tenant agrees to pay rent."),
        make_term("2", "POSSESSION", "Vacant possession on entry."),
    ]
    instruction = standard_form_instruction(date(2026, 8, 9), terms)
    assert "S1-T1" in instruction and "RENT" in instruction
    assert "nsw.clause.sf_t1" in instruction and "nsw.clause.sf_t2" in instruction
    assert "covered" in instruction and "altered_adverse" in instruction
    assert STANDARD_FORM_GUIDANCE in instruction


def test_standard_form_instruction_frames_empty_body_terms_as_table_content():
    """Terms whose prescribed body is empty or near-empty (the VIC table
    limitation, e.g. Form 1 term 6 "Rent") get a heading-driven note instead
    of a blank/near-blank body, so the judge has something to compare
    against rather than defaulting to a guess."""
    empty = make_term("6", "Rent", "")
    normal = make_term(
        "24",
        "Repairs",
        "Only a suitably qualified person may do repairs, both urgent and non-urgent.",
    )
    instruction = standard_form_instruction(date(2026, 8, 9), [empty, normal])
    assert "table or form field" in instruction
    assert "Rent" in instruction and "S1-T6" in instruction
    assert "Only a suitably qualified person may do repairs" in instruction
    # the normal-length term's actual body is quoted verbatim, not reframed
    assert instruction.count("table or form field") == 1


def test_standard_form_instruction_notes_act_duty_for_empty_body_terms():
    term = make_term("6", "Rent", "")
    term = replace(term, act_duty="33")
    instruction = standard_form_instruction(date(2026, 8, 9), [term])
    assert "Act section 33" in instruction


def test_standard_form_output_model_validates_outcomes():
    model = standard_form_output_model(["nsw.clause.sf_t1"])
    parsed = model.model_validate(
        {
            "items": [
                {
                    "rule_id": "nsw.clause.sf_t1",
                    "outcome": "missing",
                    "reasoning": "not found",
                    "lease_quote": None,
                    "departure": None,
                }
            ]
        }
    )
    assert parsed.items[0].outcome == "missing"


def test_standard_form_output_model_rejects_out_of_batch_rule_id():
    """A rule_id valid in general but foreign to this batch's model must fail."""
    model = standard_form_output_model(["nsw.clause.sf_t1", "nsw.clause.sf_t2"])
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "items": [
                    {
                        "rule_id": "nsw.clause.sf_t99",
                        "outcome": "missing",
                        "reasoning": "not in this batch",
                        "lease_quote": None,
                        "departure": None,
                    }
                ]
            }
        )


def make_fake_judge(outcomes: dict[str, dict]):
    """Route each outcome to the batch whose instruction lists its rule_id.

    Matches on "{rule_id} (" - the exact token boundary standard_form_instruction
    renders ("- {rule_id} ({section_no} ...)") - not bare substring containment:
    rule ids share numeric prefixes (sf_t1 is a prefix of sf_t10..sf_t19), so a
    plain `rule_id in instruction` check would leak sf_t1's outcome into any
    batch that happens to list sf_t10-sf_t19 too. Every call is recorded on
    judge.calls so tests can assert batch counts.
    """
    calls: list[tuple] = []

    async def judge(doc, instruction, output_model):
        calls.append((doc, instruction, output_model))
        items = []
        for rule_id, payload in outcomes.items():
            if f"{rule_id} (" in instruction:
                items.append({"rule_id": rule_id, **payload})
        return output_model.model_validate({"items": items})

    judge.calls = calls
    return judge


async def test_runner_screens_verbatim_and_judges_residual(corpus_session):
    terms, _ = await fetch_form_terms(corpus_session, "NSW", date(2026, 8, 9), None)
    t1 = terms[0]
    doc = DocumentInput(kind="text", text=f"1. {t1.heading} {t1.body} Nothing else.")
    judge = make_fake_judge(
        {
            t.rule_id: {
                "outcome": "missing",
                "reasoning": "absent",
                "lease_quote": None,
                "departure": None,
            }
            for t in terms[1:]
        }
    )
    findings = await run_standard_form(judge, corpus_session, doc, date(2026, 8, 9), "NSW", None)
    by_id = {f.rule_id: f for f in findings}
    assert len(findings) == 59
    assert by_id[t1.rule_id].verdict == "green"
    assert by_id[t1.rule_id].evidence["method"] == "verbatim"
    assert by_id[terms[1].rule_id].verdict == "red"
    assert by_id[terms[1].rule_id].evidence["outcome"] == "missing"
    assert all(f.citations and f.citations[0].label for f in findings)


async def test_runner_dual_citation_for_act_duties(corpus_session):
    doc = DocumentInput(kind="text", text="An empty lease.")
    judge = make_fake_judge({})
    findings = await run_standard_form(judge, corpus_session, doc, date(2026, 8, 9), "NSW", None)
    assert len(judge.calls) == 8  # 59 all-residual terms / BATCH_SIZE 8, rounded up
    t19 = next(f for f in findings if f.rule_id == "nsw.clause.sf_t19")
    assert [c.section_no for c in t19.citations] == ["S1-T19", "52"]
    assert t19.citations[1].act == "act-2010-042"


async def test_runner_altered_and_uncertain_and_quote_downgrade(corpus_session):
    terms, note = await fetch_form_terms(corpus_session, "VIC", date(2026, 8, 9), None)
    doc = DocumentInput(kind="text", text="A bespoke lease with its own words.")
    first, second, third, fourth = terms[0], terms[1], terms[2], terms[3]
    unreported = terms[4]
    judge = make_fake_judge(
        {
            first.rule_id: {
                "outcome": "altered_adverse",
                "reasoning": "notice cut",
                "lease_quote": "words not in the document",
                "departure": "notice period shortened",
            },
            second.rule_id: {
                "outcome": "uncertain",
                "reasoning": "cannot tell",
                "lease_quote": None,
                "departure": None,
            },
            third.rule_id: {
                "outcome": "covered",
                "reasoning": "found",
                "lease_quote": "own words",
                "departure": None,
            },
            fourth.rule_id: {
                "outcome": "covered",
                "reasoning": "found but unquoted",
                "lease_quote": None,
                "departure": None,
            },
        }
    )
    findings = await run_standard_form(judge, corpus_session, doc, date(2026, 8, 9), "VIC", None)
    by_id = {f.rule_id: f for f in findings}
    assert note is not None  # VIC + no lease defaults to Form 1 and notes the default
    assert all(note in f.summary for f in findings)

    assert by_id[first.rule_id].verdict == "yellow"
    assert "not found" in by_id[first.rule_id].summary
    assert "Departure: notice period shortened" in by_id[first.rule_id].summary
    assert by_id[second.rule_id].verdict == "yellow"
    assert by_id[third.rule_id].verdict == "green"
    assert by_id[third.rule_id].clause_quote == "own words"
    assert by_id[fourth.rule_id].verdict == "yellow"
    assert "Downgraded: covered outcome carried no quote." in by_id[fourth.rule_id].summary
    assert by_id[unreported.rule_id].verdict == "yellow"
    assert "did not report" in by_id[unreported.rule_id].summary


async def test_runner_pdf_document_skips_screen(corpus_session):
    doc = DocumentInput(kind="pdf", pdf=b"%PDF-fake")
    terms, _ = await fetch_form_terms(corpus_session, "VIC", date(2026, 8, 9), None)
    judge = make_fake_judge(
        {
            t.rule_id: {
                "outcome": "covered",
                "reasoning": "in the pdf",
                "lease_quote": None,
                "departure": None,
            }
            for t in terms
        }
    )
    findings = await run_standard_form(judge, corpus_session, doc, date(2026, 8, 9), "VIC", None)
    assert all(f.verdict in {"green", "yellow"} for f in findings)
    assert not any(f.evidence.get("method") == "verbatim" for f in findings)


def _screenable(terms) -> set[str]:
    """Terms the verbatim baseline is expected to screen green.

    Both length gates mirror screen_terms' own MIN_SCREEN_TOKENS check. A
    third gate excludes terms whose OWN rendered form does not self-match at
    CONTAINMENT_THRESHOLD: a handful of heavily [insert ...]-templated
    fields (contact-detail and signature blocks, mostly VIC) fill to
    realistic multi-word values at several points in a single term, and
    normalize() strips the corresponding placeholder to nothing in the
    prescribed text - no amount of realistic filling closes that gap, so
    these terms genuinely fall to the LLM residual even when "verbatim".
    """
    return {
        t.section_no
        for t in terms
        if len(normalize(f"{t.heading} {t.body}").split()) >= 12
        and len(normalize(t.body).split()) >= 12
        and containment(f"{t.heading} {t.body}", render_term(t)) >= CONTAINMENT_THRESHOLD
    }


async def test_verbatim_document_screens_all_screenable_terms_green(corpus_session):
    terms, _ = await fetch_form_terms(corpus_session, "NSW", date(2026, 8, 9), None)
    document = build_verbatim(terms)
    green, _residual = screen_terms(terms, document)
    assert {t.section_no for t, _ in green} == _screenable(terms)


async def test_altered_terms_fall_out_of_the_screen(corpus_session):
    terms, _ = await fetch_form_terms(corpus_session, "NSW", date(2026, 8, 9), None)
    document = build_altered(terms, ALTERATIONS)
    green, _residual = screen_terms(terms, document)
    altered = {t.section_no for t in terms if t.rule_id in ALTERATIONS}
    assert not ({t.section_no for t, _ in green} & altered)


async def test_verbatim_document_screens_all_screenable_vic_f1_terms_green(corpus_session):
    terms, _ = await fetch_form_terms(corpus_session, "VIC", date(2026, 8, 9), None)
    document = build_verbatim(terms)
    green, _residual = screen_terms(terms, document)
    assert {t.section_no for t, _ in green} == _screenable(terms)


async def test_altered_vic_f1_terms_fall_out_of_the_screen(corpus_session):
    terms, _ = await fetch_form_terms(corpus_session, "VIC", date(2026, 8, 9), None)
    document = build_altered(terms, ALTERATIONS)
    green, _residual = screen_terms(terms, document)
    altered = {t.section_no for t in terms if t.rule_id in ALTERATIONS}
    assert not ({t.section_no for t, _ in green} & altered)


async def test_verbatim_document_screens_all_screenable_vic_f2_terms_green(corpus_session):
    lease = ClauseLeaseInput(start_date=date(2020, 1, 1), end_date=date(2026, 1, 2))
    terms, _ = await fetch_form_terms(corpus_session, "VIC", date(2026, 8, 9), lease)
    document = build_verbatim(terms)
    green, _residual = screen_terms(terms, document)
    assert {t.section_no for t, _ in green} == _screenable(terms)


async def test_altered_vic_f2_terms_fall_out_of_the_screen(corpus_session):
    lease = ClauseLeaseInput(start_date=date(2020, 1, 1), end_date=date(2026, 1, 2))
    terms, _ = await fetch_form_terms(corpus_session, "VIC", date(2026, 8, 9), lease)
    document = build_altered(terms, ALTERATIONS)
    green, _residual = screen_terms(terms, document)
    altered = {t.section_no for t in terms if t.rule_id in ALTERATIONS}
    assert not ({t.section_no for t, _ in green} & altered)


async def test_matrix_passes_produce_distinct_partitions(corpus_session):
    """The three missing-matrix passes must partition terms differently -
    a rotation landing on a chunk boundary silently reproduces the same
    documents (the pre-fix NSW schedule wasted ~44 percent of paid volume
    on byte-identical repeats)."""
    from tests.golden.standard_form import plan_documents

    long_lease = ClauseLeaseInput(start_date=date(2020, 1, 1), end_date=date(2026, 1, 2))
    for jurisdiction, lease in (("NSW", None), ("VIC", None), ("VIC", long_lease)):
        terms, _ = await fetch_form_terms(corpus_session, jurisdiction, date(2026, 8, 9), lease)
        docs = plan_documents(terms)
        partitions = []
        for pass_no in range(3):
            chunks = frozenset(
                frozenset(rid for rid, label in d.expected.items() if label == "red")
                for d in docs
                if d.doc_id.startswith(f"missing-{pass_no}-")
            )
            partitions.append(chunks)
        assert len(set(partitions)) == 3, f"{jurisdiction}: matrix passes not distinct"
