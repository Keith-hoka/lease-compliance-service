from fastapi import FastAPI

from app.routers.audits import router as audits_router
from app.routers.changes import router as changes_router
from app.routers.legislation import router as legislation_router

app = FastAPI(
    title="Lease Compliance Service",
    description="General information, not legal advice.",
)
app.include_router(audits_router)
app.include_router(changes_router)
app.include_router(legislation_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
