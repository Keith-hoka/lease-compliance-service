import asyncio
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.clause_audit import rules as rules_module
from app.clause_audit import worker
from app.clause_audit.rules import ClauseRule
from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act, ClauseAuditJob
from app.rules.base import SectionRef

AS_AT = date(2026, 7, 28)
CARPET = "The tenant must have the carpet professionally cleaned at the end of the tenancy."

RULE = ClauseRule(
    rule_id="nsw.clause.carpet_cleaning",
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
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def seeded_s19(session_factory):
    async with session_factory() as session:
        act = Act(
            jurisdiction="NSW",
            slug="act-2010-042",
            title="Residential Tenancies Act 2010",
            source_url="x",
        )
        session.add(act)
        await session.flush()
        await load_version(
            session,
            act.id,
            date(2011, 1, 31),
            [ParsedSection("19", "Prohibited terms", "terms body", "Part 2", None)],
        )
        await session.commit()


def _job(**overrides) -> ClauseAuditJob:
    values = {
        "client_id": "testco",
        "jurisdiction": "NSW",
        "as_at": AS_AT,
        "document": f"AGREEMENT. {CARPET}".encode(),
        "document_kind": "text",
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


async def _add(session_factory, job):
    async with session_factory() as session:
        session.add(job)
        await session.commit()
        return job.id


async def _fetch(session_factory, job_id):
    async with session_factory() as session:
        return await session.get(ClauseAuditJob, job_id)


async def test_run_once_processes_oldest_pending(fake_judge, session_factory, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = RED
    job_id = await _add(session_factory, _job())

    assert await worker.run_once(fake_judge, session_factory) is True
    row = await _fetch(session_factory, job_id)
    assert row.status == "succeeded" and row.document is None
    assert row.findings[0]["rule_id"] == "nsw.clause.carpet_cleaning"

    assert await worker.run_once(fake_judge, session_factory) is False


async def test_run_once_failure_marks_failed_and_wipes(session_factory, seeded_s19):
    async def broken_judge(doc, instruction, output_model):
        raise RuntimeError("model exploded")

    job_id = await _add(session_factory, _job())
    assert await worker.run_once(broken_judge, session_factory) is True
    row = await _fetch(session_factory, job_id)
    assert row.status == "failed" and row.document is None
    assert "model exploded" in row.error


async def test_run_once_timeout_marks_failed(session_factory, seeded_s19, monkeypatch):
    async def slow_judge(doc, instruction, output_model):
        await asyncio.sleep(1)

    monkeypatch.setattr(worker, "JOB_TIMEOUT_SECONDS", 0.01)
    job_id = await _add(session_factory, _job())
    assert await worker.run_once(slow_judge, session_factory) is True
    row = await _fetch(session_factory, job_id)
    assert row.status == "failed" and "timed out" in row.error


async def test_sweep_stale_fails_running_jobs(session_factory):
    job_id = await _add(session_factory, _job(status="running"))
    pending_id = await _add(session_factory, _job())
    await worker.sweep_stale(session_factory)
    stale = await _fetch(session_factory, job_id)
    assert stale.status == "failed" and stale.document is None
    assert "restart" in stale.error
    assert (await _fetch(session_factory, pending_id)).status == "pending"
