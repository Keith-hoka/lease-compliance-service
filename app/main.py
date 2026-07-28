import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.clause_audit.worker import sweep_stale, worker_loop
from app.core.config import clause_audit_enabled
from app.llm.client import make_judge
from app.routers.audits import router as audits_router
from app.routers.changes import router as changes_router
from app.routers.clause_audits import router as clause_audits_router
from app.routers.legislation import router as legislation_router


@asynccontextmanager
async def lifespan(app: FastAPI):
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
