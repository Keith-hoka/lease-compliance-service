import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.dates import sydney_today
from app.core.db import get_session
from app.models import Audit
from app.rules import ENGINE_VERSION
from app.rules.engine import run_audit
from app.schemas.audit import AuditCreate, AuditInfo

router = APIRouter(prefix="/v1")

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ClientDep = Annotated[str, Depends(require_api_key)]


@router.post("/audits", status_code=201, response_model=AuditInfo)
async def create_audit(body: AuditCreate, client_id: ClientDep, session: SessionDep) -> AuditInfo:
    as_at = body.as_at or sydney_today()
    findings = await run_audit(session, body.jurisdiction, as_at, body.lease)
    audit = Audit(
        jurisdiction=body.jurisdiction,
        as_at=as_at,
        input=body.lease.model_dump(mode="json"),
        findings=[f.model_dump(mode="json") for f in findings],
        engine_version=ENGINE_VERSION,
        client_id=client_id,
        client_ref=body.client_ref,
    )
    session.add(audit)
    await session.commit()
    await session.refresh(audit)
    return AuditInfo(
        id=audit.id,
        jurisdiction=audit.jurisdiction,
        as_at=audit.as_at,
        engine_version=audit.engine_version,
        client_ref=audit.client_ref,
        findings=findings,
        created_at=audit.created_at,
    )


@router.get("/audits/{audit_id}", response_model=AuditInfo)
async def get_audit(audit_id: uuid.UUID, client_id: ClientDep, session: SessionDep) -> AuditInfo:
    audit = await session.get(Audit, audit_id)
    if audit is None or audit.client_id != client_id:
        raise HTTPException(status_code=404, detail="Audit not found")
    return AuditInfo(
        id=audit.id,
        jurisdiction=audit.jurisdiction,
        as_at=audit.as_at,
        engine_version=audit.engine_version,
        client_ref=audit.client_ref,
        findings=audit.findings,
        created_at=audit.created_at,
    )
