import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TenantDep, require_api_key
from app.core.config import clause_audit_enabled, settings
from app.core.dates import sydney_today
from app.core.db import get_session
from app.core.ratelimit import enforce_rate_limit
from app.core.usage import record_usage
from app.models import ClauseAuditJob
from app.rules import ENGINE_VERSION
from app.schemas.clause_audit import ClauseAuditCreate, ClauseAuditInfo

router = APIRouter(prefix="/v1", dependencies=[Depends(enforce_rate_limit)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ClientDep = Annotated[str, Depends(require_api_key)]

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_IN_FLIGHT_PER_TENANT = 10


def _info(job: ClauseAuditJob) -> ClauseAuditInfo:
    return ClauseAuditInfo(
        id=job.id,
        status=job.status,
        jurisdiction=job.jurisdiction,
        as_at=job.as_at,
        engine_version=job.engine_version,
        model=job.model,
        client_ref=job.client_ref,
        findings=job.findings,
        discrepancies=job.discrepancies,
        error=job.error,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.post("/clause-audits", status_code=202, response_model=ClauseAuditInfo)
async def create_clause_audit(
    tenant: TenantDep,
    session: SessionDep,
    payload: Annotated[str, Form()],
    file: Annotated[UploadFile | None, File()] = None,
    text: Annotated[str | None, Form()] = None,
) -> ClauseAuditInfo:
    if not clause_audit_enabled():
        raise HTTPException(status_code=503, detail="Clause audit is not configured")
    try:
        body = ClauseAuditCreate.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if (file is None) == (text is None):
        raise HTTPException(status_code=422, detail="Provide exactly one of file or text")
    in_flight = (
        await session.execute(
            select(func.count())
            .select_from(ClauseAuditJob)
            .where(
                ClauseAuditJob.client_id == tenant.client_id,
                ClauseAuditJob.status.in_(("pending", "running")),
            )
        )
    ).scalar_one()
    if in_flight >= MAX_IN_FLIGHT_PER_TENANT:
        raise HTTPException(status_code=429, detail="Too many clause audits in flight")
    midnight_utc = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = (
        await session.execute(
            select(func.count())
            .select_from(ClauseAuditJob)
            .where(
                ClauseAuditJob.client_id == tenant.client_id,
                ClauseAuditJob.created_at >= midnight_utc,
            )
        )
    ).scalar_one()
    if today_count >= tenant.clause_audits_per_day:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily clause audit quota of {tenant.clause_audits_per_day} "
                "reached; resets at midnight UTC"
            ),
        )
    if text is not None:
        if len(text) > MAX_TEXT_CHARS:
            raise HTTPException(status_code=413, detail="Text too large")
        document, kind = text.encode("utf-8"), "text"
    else:
        if file.size is not None and file.size > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail="File too large")
        document = await file.read()
        if len(document) > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail="File too large")
        kind = "pdf"
    job = ClauseAuditJob(
        client_id=tenant.client_id,
        client_ref=body.client_ref,
        jurisdiction=body.jurisdiction,
        as_at=body.as_at or sydney_today(),
        document=document,
        document_kind=kind,
        lease=body.lease.model_dump(mode="json") if body.lease else None,
        engine_version=ENGINE_VERSION,
        model=settings.clause_audit_model,
    )
    session.add(job)
    await record_usage(session, tenant.tenant_id, "clause_audit")
    await session.commit()
    await session.refresh(job)
    return _info(job)


@router.get("/clause-audits/{job_id}", response_model=ClauseAuditInfo)
async def get_clause_audit(
    job_id: uuid.UUID, client_id: ClientDep, session: SessionDep
) -> ClauseAuditInfo:
    job = await session.get(ClauseAuditJob, job_id)
    if job is None or job.client_id != client_id:
        raise HTTPException(status_code=404, detail="Clause audit not found")
    return _info(job)


@router.get("/clause-audits", response_model=list[ClauseAuditInfo])
async def list_clause_audits(
    client_ref: str, client_id: ClientDep, session: SessionDep, limit: int = 20
) -> list[ClauseAuditInfo]:
    query = (
        select(ClauseAuditJob)
        .where(ClauseAuditJob.client_id == client_id, ClauseAuditJob.client_ref == client_ref)
        .order_by(ClauseAuditJob.created_at.desc(), ClauseAuditJob.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(query)).scalars().all()
    return [_info(row) for row in rows]
