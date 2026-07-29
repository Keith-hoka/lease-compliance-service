import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clause_audit.worker import sweep_stale, worker_loop
from app.core.config import clause_audit_enabled
from app.core.db import get_session
from app.core.logs import configure_logging
from app.llm.client import make_judge
from app.models import ClauseAuditJob
from app.routers.audits import router as audits_router
from app.routers.changes import router as changes_router
from app.routers.clause_audits import router as clause_audits_router
from app.routers.legislation import router as legislation_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    await sweep_stale()
    task = None
    if clause_audit_enabled():
        task = asyncio.create_task(worker_loop(make_judge()))
    yield
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="Lease Compliance Service",
    description="General information, not legal advice.",
    lifespan=lifespan,
)
app.include_router(audits_router)
app.include_router(changes_router)
app.include_router(clause_audits_router)
app.include_router(legislation_router)


@app.api_route("/health", methods=["GET", "HEAD"])
async def health(session: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    """Liveness plus the cheapest dead-worker detector: the pending queue.

    HEAD is allowed because uptime monitors probe with it.
    """
    count, oldest = (
        await session.execute(
            select(func.count(), func.min(ClauseAuditJob.created_at)).where(
                ClauseAuditJob.status == "pending"
            )
        )
    ).one()
    age = (datetime.now(UTC) - oldest).total_seconds() if oldest is not None else None
    return {"status": "ok", "clause_audit": {"pending": count, "oldest_pending_seconds": age}}
