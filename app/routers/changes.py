from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.db import get_session
from app.models import AuditChange
from app.schemas.audit import AuditChangeInfo

router = APIRouter(prefix="/v1")

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ClientDep = Annotated[str, Depends(require_api_key)]


@router.get("/audit-changes", response_model=list[AuditChangeInfo])
async def list_audit_changes(
    client_id: ClientDep,
    session: SessionDep,
    since: datetime | None = None,
    client_ref: str | None = None,
    limit: int = 100,
) -> list[AuditChangeInfo]:
    query = (
        select(AuditChange)
        .where(AuditChange.client_id == client_id)
        .order_by(AuditChange.created_at.asc(), AuditChange.id.asc())
        .limit(limit)
    )
    if since is not None:
        query = query.where(AuditChange.created_at > since)
    if client_ref is not None:
        query = query.where(AuditChange.client_ref == client_ref)
    rows = (await session.execute(query)).scalars().all()
    return [
        AuditChangeInfo(
            id=row.id,
            client_ref=row.client_ref,
            old_audit_id=row.old_audit_id,
            new_audit_id=row.new_audit_id,
            changes=row.changes,
            created_at=row.created_at,
        )
        for row in rows
    ]
