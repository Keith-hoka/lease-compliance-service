from datetime import date
from decimal import Decimal

import pytest

from app.clause_audit import rules as rules_module
from app.clause_audit.document import DocumentInput
from app.clause_audit.families import run_fields, run_prohibited
from app.clause_audit.rules import ClauseRule
from app.clause_audit.verify import quote_matches
from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act
from app.rules.base import SectionRef
from app.schemas.clause_audit import ClauseLeaseInput

AS_AT = date(2026, 7, 28)
CARPET = "The tenant must have the carpet professionally cleaned at the end of the tenancy."
DOC = DocumentInput(kind="text", text=f"AGREEMENT. {CARPET} Rent is payable weekly.")

RULE = ClauseRule(
    rule_id="nsw.clause.carpet_cleaning",
    jurisdiction="NSW",
    family="prohibited",
    ref=SectionRef("act-2010-042", "19"),
    applies_from=date(2011, 1, 31),
    applies_to=None,
    question="A term requiring professional carpet cleaning at the end of the tenancy.",
)


@pytest.fixture
async def seeded_s19(db_session):
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
        date(2011, 1, 31),
        [ParsedSection("19", "Prohibited terms", "terms must not be included", "Part 2", None)],
    )
    await db_session.commit()


@pytest.fixture(autouse=True)
def single_rule(monkeypatch):
    monkeypatch.setattr(rules_module, "PROHIBITED_RULES", [RULE])


def _item(verdict, quote):
    return {
        "items": [
            {
                "rule_id": "nsw.clause.carpet_cleaning",
                "verdict": verdict,
                "reasoning": "because",
                "clause_quote": quote,
            }
        ]
    }


async def test_red_with_matching_quote(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = _item("red", CARPET)
    findings = await run_prohibited(
        fake_judge, db_session, DOC, AS_AT, rules_module.PROHIBITED_RULES
    )
    assert findings[0].verdict == "red"
    assert findings[0].clause_quote == CARPET
    assert findings[0].citations[0].act == "Residential Tenancies Act 2010"


async def test_red_quote_not_in_document_downgrades(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = _item("red", "an invented sentence")
    findings = await run_prohibited(
        fake_judge, db_session, DOC, AS_AT, rules_module.PROHIBITED_RULES
    )
    assert findings[0].verdict == "yellow"
    assert "quote" in findings[0].summary


async def test_red_without_quote_downgrades(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = _item("red", None)
    findings = await run_prohibited(
        fake_judge, db_session, DOC, AS_AT, rules_module.PROHIBITED_RULES
    )
    assert findings[0].verdict == "yellow"


async def test_pdf_path_skips_quote_verification(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = _item("red", "anything at all")
    pdf_doc = DocumentInput(kind="pdf", pdf=b"%PDF-fake")
    findings = await run_prohibited(
        fake_judge, db_session, pdf_doc, AS_AT, rules_module.PROHIBITED_RULES
    )
    assert findings[0].verdict == "red"


async def test_missing_item_is_yellow(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = {"items": []}
    findings = await run_prohibited(
        fake_judge, db_session, DOC, AS_AT, rules_module.PROHIBITED_RULES
    )
    assert findings[0].verdict == "yellow"
    assert "did not report" in findings[0].summary


async def test_inactive_rule_is_skipped_without_judging(fake_judge, db_session, seeded_s19):
    early = await run_prohibited(
        fake_judge, db_session, DOC, date(2010, 1, 1), rules_module.PROHIBITED_RULES
    )
    assert early[0].verdict == "skipped"
    assert fake_judge.calls == []


async def test_unresolvable_section_is_skipped(fake_judge, db_session, seeded_s19, monkeypatch):
    ghost = ClauseRule(
        rule_id="nsw.clause.ghost",
        jurisdiction="NSW",
        family="prohibited",
        ref=SectionRef("act-2010-042", "999"),
        applies_from=date(2011, 1, 31),
        applies_to=None,
        question="x",
    )
    monkeypatch.setattr(rules_module, "PROHIBITED_RULES", [ghost])
    findings = await run_prohibited(
        fake_judge, db_session, DOC, AS_AT, rules_module.PROHIBITED_RULES
    )
    assert findings[0].verdict == "skipped"
    assert fake_judge.calls == []


def _fields(items):
    return {"fields": items}


async def test_field_mismatch_reported(fake_judge):
    fake_judge.responses["FieldsOutput"] = _fields(
        [{"field": "rent_amount", "document_value": "$520 per week", "quote": "rent clause"}]
    )
    lease = ClauseLeaseInput(rent_amount=Decimal(560))
    result = await run_fields(fake_judge, DOC, lease)
    assert result[0].field == "rent_amount"
    assert result[0].document_value == "$520 per week"
    assert result[0].submitted_value == "560"


async def test_field_match_and_absent_are_silent(fake_judge):
    fake_judge.responses["FieldsOutput"] = _fields(
        [
            {"field": "rent_amount", "document_value": "$560.00", "quote": "x"},
            {"field": "bond_amount", "document_value": None, "quote": None},
        ]
    )
    lease = ClauseLeaseInput(rent_amount=Decimal(560), bond_amount=Decimal(2240))
    assert await run_fields(fake_judge, DOC, lease) == []


async def test_date_and_frequency_normalisation(fake_judge):
    fake_judge.responses["FieldsOutput"] = _fields(
        [
            {"field": "start_date", "document_value": "1 February 2026", "quote": "x"},
            {"field": "rent_frequency", "document_value": "per fortnight", "quote": "x"},
        ]
    )
    lease = ClauseLeaseInput(start_date=date(2026, 2, 1), rent_frequency="weekly")
    result = await run_fields(fake_judge, DOC, lease)
    assert [d.field for d in result] == ["rent_frequency"]


async def test_unparseable_document_value_is_silent(fake_judge):
    fake_judge.responses["FieldsOutput"] = _fields(
        [{"field": "start_date", "document_value": "the usual date", "quote": "x"}]
    )
    lease = ClauseLeaseInput(start_date=date(2026, 2, 1))
    assert await run_fields(fake_judge, DOC, lease) == []


def test_quote_matches_normalises_whitespace_and_case():
    assert quote_matches("Carpet   Professionally\ncleaned", "the carpet professionally cleaned.")
    assert not quote_matches("fumigated", "the carpet professionally cleaned.")
    assert quote_matches("anything", None)
