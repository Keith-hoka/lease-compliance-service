import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clause_audit.worker import sweep_stale, worker_loop
from app.core.config import clause_audit_enabled, settings
from app.core.db import async_session_factory, get_session
from app.core.logs import configure_logging
from app.llm.client import make_judge
from app.models import ClauseAuditJob
from app.routers.admin import router as admin_router
from app.routers.audits import router as audits_router
from app.routers.changes import router as changes_router
from app.routers.clause_audits import router as clause_audits_router
from app.routers.legislation import router as legislation_router
from app.tenants import import_env_keys


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    if settings.api_keys:
        async with async_session_factory() as session:
            imported = await import_env_keys(session)
        if imported:
            logging.getLogger(__name__).info("imported %d api keys from env", imported)
    await sweep_stale()
    task = None
    if clause_audit_enabled():
        judge = make_judge()
        app.state.judge = judge
        task = asyncio.create_task(worker_loop(judge))
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
app.include_router(admin_router)
app.include_router(audits_router)
app.include_router(changes_router)
app.include_router(clause_audits_router)
app.include_router(legislation_router)


@app.api_route("/health", methods=["GET", "HEAD"])
async def health(request: Request, session: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    """Liveness plus the cheapest dead-worker detector: the pending queue.

    HEAD is allowed because uptime monitors probe with it. llm_failover
    appears only when the clause-audit worker is running.
    """
    count, oldest = (
        await session.execute(
            select(func.count(), func.min(ClauseAuditJob.created_at)).where(
                ClauseAuditJob.status == "pending"
            )
        )
    ).one()
    age = (datetime.now(UTC) - oldest).total_seconds() if oldest is not None else None
    payload = {"status": "ok", "clause_audit": {"pending": count, "oldest_pending_seconds": age}}
    judge = getattr(request.app.state, "judge", None)
    if judge is not None:
        payload["llm_failover"] = {"state": judge.state, "active_model": judge.active_model}
    return payload
