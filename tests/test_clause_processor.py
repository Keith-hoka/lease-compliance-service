from datetime import date

import pytest

from app.clause_audit import rules as rules_module
from app.clause_audit.processor import process_job
from app.clause_audit.rules import ClauseRule
from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act, ClauseAuditJob
from app.rules.base import SectionRef

AS_AT = date(2026, 7, 28)
CARPET = "The tenant must have the carpet professionally cleaned at the end of the tenancy."

RULE = ClauseRule(
    rule_id="nsw.clause.carpet_cleaning",
    jurisdiction="NSW",
    family="prohibited",
    ref=SectionRef("act-2010-042", "19"),
    applies_from=date(2011, 1, 31),
    applies_to=None,
    question="A term requiring professional carpet cleaning at the end of the tenancy.",
)


@pytest.fixture(autouse=True)
def single_rule(monkeypatch):
    monkeypatch.setattr(rules_module, "PROHIBITED_RULES", [RULE])
    monkeypatch.setattr(rules_module, "MANDATORY_RULES", [])


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


def _job(**overrides) -> ClauseAuditJob:
    values = {
        "client_id": "testco",
        "jurisdiction": "NSW",
        "as_at": AS_AT,
        "document": f"AGREEMENT. {CARPET}".encode(),
        "document_kind": "text",
        "status": "running",
        "engine_version": "1.1.0",
        "model": "claude-opus-4-8",
    }
    values.update(overrides)
    return ClauseAuditJob(**values)


RED = {
    "items": [
        {
            "rule_id": "nsw.clause.carpet_cleaning",
            "verdict": "red",
            "reasoning": "found",
            "clause_quote": CARPET,
        }
    ]
}


async def test_process_job_succeeds_and_wipes(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = RED
    job = _job()
    db_session.add(job)
    await db_session.commit()

    await process_job(db_session, job, fake_judge)

    assert job.status == "succeeded"
    assert job.document is None
    assert job.completed_at is not None
    assert job.findings[0]["verdict"] == "red"
    assert job.discrepancies == []


async def test_process_job_runs_fields_only_with_lease(fake_judge, db_session, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = RED
    fake_judge.responses["FieldsOutput"] = {
        "fields": [{"field": "rent_amount", "document_value": "$520", "quote": "x"}]
    }
    job = _job(lease={"rent_amount": "560"})
    db_session.add(job)
    await db_session.commit()

    await process_job(db_session, job, fake_judge)

    assert job.discrepancies == [
        {"field": "rent_amount", "document_value": "$520", "submitted_value": "560"}
    ]
