"""Run one claimed job end to end and wipe the document. Caller commits."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.clause_audit import rules as clause_rules
from app.clause_audit import rules_vic
from app.clause_audit.document import document_input
from app.clause_audit.families import run_fields, run_prohibited
from app.clause_audit.standard_form import run_standard_form
from app.llm.client import JudgeFn
from app.models import ClauseAuditJob
from app.schemas.clause_audit import ClauseLeaseInput


async def process_job(session: AsyncSession, job: ClauseAuditJob, judge: JudgeFn) -> None:
    doc = document_input(job.document_kind, job.document)
    lease = ClauseLeaseInput.model_validate(job.lease) if job.lease is not None else None
    if job.jurisdiction == "VIC":
        findings = await run_prohibited(
            judge, session, doc, job.as_at, rules_vic.VIC_PROHIBITED_RULES
        )
    else:
        findings = await run_prohibited(
            judge, session, doc, job.as_at, clause_rules.PROHIBITED_RULES
        )
    findings += await run_standard_form(judge, session, doc, job.as_at, job.jurisdiction, lease)
    discrepancies = []
    if lease is not None:
        discrepancies = await run_fields(judge, doc, lease)
    job.findings = [f.model_dump(mode="json") for f in findings]
    job.discrepancies = [d.model_dump(mode="json") for d in discrepancies]
    job.status = "succeeded"
    job.completed_at = datetime.now(UTC)
    job.document = None
