import uuid
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.db import get_session
from app.models import Audit
from app.rules import ENGINE_VERSION
from app.rules.engine import run_audit
from app.schemas.audit import AuditCreate, AuditInfo

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/audits", status_code=201, response_model=AuditInfo)
async def create_audit(body: AuditCreate, session: SessionDep) -> AuditInfo:
    as_at = body.as_at or datetime.now(tz=ZoneInfo("Australia/Sydney")).date()
    findings = await run_audit(session, body.jurisdiction, as_at, body.lease)
    audit = Audit(
        jurisdiction=body.jurisdiction,
        as_at=as_at,
        input=body.lease.model_dump(mode="json"),
        findings=[f.model_dump(mode="json") for f in findings],
        engine_version=ENGINE_VERSION,
    )
    session.add(audit)
    await session.commit()
    await session.refresh(audit)
    return AuditInfo(
        id=audit.id,
        jurisdiction=audit.jurisdiction,
        as_at=audit.as_at,
        engine_version=audit.engine_version,
        findings=findings,
        created_at=audit.created_at,
    )


@router.get("/audits/{audit_id}", response_model=AuditInfo)
async def get_audit(audit_id: uuid.UUID, session: SessionDep) -> AuditInfo:
    audit = await session.get(Audit, audit_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return AuditInfo(
        id=audit.id,
        jurisdiction=audit.jurisdiction,
        as_at=audit.as_at,
        engine_version=audit.engine_version,
        findings=audit.findings,
        created_at=audit.created_at,
    )
