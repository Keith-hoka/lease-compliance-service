"""Claim pending clause-audit jobs and process them one at a time."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.clause_audit.processor import process_job
from app.core.db import async_session_factory
from app.llm.client import JudgeFn
from app.models import ClauseAuditJob

POLL_SECONDS = 2
JOB_TIMEOUT_SECONDS = 900


async def sweep_stale(session_factory=async_session_factory) -> None:
    """Fail jobs left running by a dead process; pending jobs survive untouched."""
    async with session_factory() as session:
        query = select(ClauseAuditJob).where(ClauseAuditJob.status == "running")
        for job in (await session.execute(query)).scalars().all():
            job.status = "failed"
            job.error = "interrupted by restart"
            job.document = None
            job.completed_at = datetime.now(UTC)
        await session.commit()


async def claim_next(session) -> ClauseAuditJob | None:
    query = (
        select(ClauseAuditJob)
        .where(ClauseAuditJob.status == "pending")
        .order_by(ClauseAuditJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await session.execute(query)).scalar_one_or_none()
    if job is None:
        return None
    job.status = "running"
    job.started_at = datetime.now(UTC)
    await session.commit()
    return job


async def run_once(judge: JudgeFn, session_factory=async_session_factory) -> bool:
    """Process at most one job; True when a job was claimed."""
    async with session_factory() as session:
        job = await claim_next(session)
        if job is None:
            return False
        job_id = job.id
        try:
            await asyncio.wait_for(process_job(session, job, judge), JOB_TIMEOUT_SECONDS)
            await session.commit()
        except TimeoutError:
            await _fail(session, job_id, "job timed out")
        except Exception as exc:  # noqa: BLE001 - any failure must fail the job, not the worker
            await _fail(session, job_id, str(exc))
        return True


async def _fail(session, job_id, error: str) -> None:
    await session.rollback()
    job = await session.get(ClauseAuditJob, job_id)
    job.status = "failed"
    job.error = error
    job.document = None
    job.completed_at = datetime.now(UTC)
    await session.commit()


async def worker_loop(judge: JudgeFn, session_factory=async_session_factory) -> None:
    while True:
        processed = await run_once(judge, session_factory)
        if not processed:
            await asyncio.sleep(POLL_SECONDS)
