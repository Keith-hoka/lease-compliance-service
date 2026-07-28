"""Run one claimed job end to end and wipe the document. Caller commits."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.clause_audit.document import document_input
from app.clause_audit.families import run_fields, run_mandatory, run_prohibited
from app.llm.client import JudgeFn
from app.models import ClauseAuditJob
from app.schemas.clause_audit import ClauseLeaseInput


async def process_job(session: AsyncSession, job: ClauseAuditJob, judge: JudgeFn) -> None:
    doc = document_input(job.document_kind, job.document)
    findings = await run_prohibited(judge, session, doc, job.as_at)
    findings += await run_mandatory(judge, session, doc, job.as_at)
    discrepancies = []
    if job.lease is not None:
        lease = ClauseLeaseInput.model_validate(job.lease)
        discrepancies = await run_fields(judge, doc, lease)
    job.findings = [f.model_dump(mode="json") for f in findings]
    job.discrepancies = [d.model_dump(mode="json") for d in discrepancies]
    job.status = "succeeded"
    job.completed_at = datetime.now(UTC)
    job.document = None
