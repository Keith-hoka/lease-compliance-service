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


VIC_RULE = ClauseRule(
    rule_id="vic.clause.renter_insurance",
    jurisdiction="VIC",
    family="prohibited",
    ref=SectionRef("residential-tenancies-act-1997", "27B"),
    applies_from=date(2021, 3, 29),
    applies_to=None,
    question="A term requiring the renter to take out insurance.",
)


@pytest.fixture
async def seeded_s27b(db_session):
    act = Act(
        jurisdiction="VIC",
        slug="residential-tenancies-act-1997",
        title="Residential Tenancies Act 1997",
        source_url="x",
    )
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session,
        act.id,
        date(2021, 3, 29),
        [ParsedSection("27B", "Prohibited terms-general", "must not include", "Part 2", None)],
    )
    await db_session.commit()


async def test_vic_job_runs_prohibited_and_fields_only(db_session, seeded_s27b, monkeypatch):
    from app.clause_audit import rules_vic

    monkeypatch.setattr(rules_vic, "VIC_PROHIBITED_RULES", [VIC_RULE])
    called = []

    async def judge(doc, instruction, output_model):
        called.append(output_model.__name__)
        if output_model.__name__ == "FieldsOutput":
            return output_model(fields=[])
        return output_model(
            items=[
                {
                    "rule_id": "vic.clause.renter_insurance",
                    "verdict": "green",
                    "reasoning": "no such term",
                    "clause_quote": None,
                }
            ]
        )

    job = _job(
        jurisdiction="VIC",
        as_at=date(2026, 8, 5),
        engine_version="1.4.0",
        model="m",
        document=b"RESIDENTIAL RENTAL AGREEMENT. Rent is payable monthly.",
        lease={"rent_amount": "2000"},
    )
    db_session.add(job)
    await db_session.flush()

    await process_job(db_session, job, judge)

    assert [f["rule_id"] for f in job.findings] == ["vic.clause.renter_insurance"]
    assert called == ["ProhibitedOutput", "FieldsOutput"]


@pytest.fixture
async def seeded_sf_nsw_term(db_session):
    """One short NSW standard-form term - short body means it always lands in residual."""
    act = Act(
        jurisdiction="NSW",
        slug="sl-2019-0629",
        title="Residential Tenancies Regulation 2019",
        source_url="x",
    )
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session,
        act.id,
        date(2019, 12, 16),
        [ParsedSection("S1-T1", "RENT", "Pay the rent on time.", "Schedule 1", None)],
    )
    await db_session.commit()


@pytest.fixture
async def seeded_sf_vic_term(db_session):
    """One short VIC Form 1 standard-form term, same shape as seeded_sf_nsw_term."""
    act = Act(
        jurisdiction="VIC",
        slug="residential-tenancies-regulations-2021",
        title="Residential Tenancies Regulations 2021",
        source_url="x",
    )
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session,
        act.id,
        date(2021, 3, 29),
        [ParsedSection("S1-F1-T1", "RENT", "Pay the rent on time.", "Schedule 1", None)],
    )
    await db_session.commit()


async def test_process_job_runs_standard_form_both_jurisdictions(
    fake_judge, db_session, seeded_sf_nsw_term, seeded_sf_vic_term, monkeypatch
):
    from app.clause_audit import rules_vic

    monkeypatch.setattr(rules_module, "PROHIBITED_RULES", [])
    monkeypatch.setattr(rules_vic, "VIC_PROHIBITED_RULES", [])
    fake_judge.responses["StandardFormOutput1"] = {"items": []}

    nsw_job = _job()
    db_session.add(nsw_job)
    await db_session.commit()
    await process_job(db_session, nsw_job, fake_judge)
    nsw_ids = {f["rule_id"] for f in nsw_job.findings}
    assert any(r.startswith("nsw.clause.sf_t") for r in nsw_ids)
    assert "nsw.clause.states_rent_payment" not in nsw_ids

    vic_job = _job(jurisdiction="VIC")
    db_session.add(vic_job)
    await db_session.commit()
    await process_job(db_session, vic_job, fake_judge)
    vic_ids = {f["rule_id"] for f in vic_job.findings}
    assert any(r.startswith("vic.clause.sf_f1_t") for r in vic_ids)
