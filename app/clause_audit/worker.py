"""Claim pending clause-audit jobs and process them one at a time."""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.clause_audit.processor import process_job
from app.core.db import async_session_factory
from app.llm.client import JudgeError
from app.llm.failover import FailoverJudge
from app.models import ClauseAuditJob

logger = logging.getLogger("app.clause_audit")

POLL_SECONDS = 2
JOB_TIMEOUT_SECONDS = 900
INTERNAL_ERROR = "internal error while processing the job"


async def sweep_stale(session_factory=async_session_factory) -> None:
    """Fail jobs left running by a dead process; pending jobs survive untouched."""
    async with session_factory() as session:
        query = select(ClauseAuditJob).where(ClauseAuditJob.status == "running")
        stale = (await session.execute(query)).scalars().all()
        for job in stale:
            job.status = "failed"
            job.error = "interrupted by restart"
            job.document = None
            job.completed_at = datetime.now(UTC)
        await session.commit()
        if stale:
            logger.warning("swept %d stale running job(s)", len(stale))


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


async def run_once(judge: FailoverJudge, session_factory=async_session_factory) -> bool:
    """Process at most one job; True when a job was claimed."""
    async with session_factory() as session:
        job = await claim_next(session)
        if job is None:
            return False
        job_id = job.id
        try:
            await asyncio.wait_for(process_job(session, job, judge), JOB_TIMEOUT_SECONDS)
            used = judge.drain_models_used()
            if used:
                job.model = "+".join(used)
            await session.commit()
            logger.info("clause audit job %s succeeded", job_id)
        except TimeoutError:
            logger.warning("clause audit job %s timed out", job_id)
            await _fail(session, job_id, "job timed out")
        except JudgeError as exc:
            logger.warning("clause audit job %s judge error: %s", job_id, exc)
            await _fail(session, job_id, str(exc))
        except Exception:
            logger.exception("clause audit job %s failed", job_id)
            await _fail(session, job_id, INTERNAL_ERROR)
        leftover = judge.drain_models_used()
        if leftover:
            logger.info("clause audit job %s used %s before failing", job_id, "+".join(leftover))
        return True


async def _fail(session, job_id, error: str) -> None:
    await session.rollback()
    job = await session.get(ClauseAuditJob, job_id)
    job.status = "failed"
    job.error = error
    job.document = None
    job.completed_at = datetime.now(UTC)
    await session.commit()


async def worker_loop(judge: FailoverJudge, session_factory=async_session_factory) -> None:
    """Poll forever; survive any per-iteration failure so the worker never dies."""
    while True:
        try:
            processed = await run_once(judge, session_factory)
        except Exception:
            logger.exception("clause audit worker iteration failed")
            processed = False
        if not processed:
            await asyncio.sleep(POLL_SECONDS)
