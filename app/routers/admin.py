"""Admin API for the developer portal. Shared-secret auth, no tenant rate limit."""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.db import get_session
from app.tenants import (
    create_tenant,
    new_key,
    revoke_key,
    set_limits,
    set_status,
    tenant_info,
    usage_rows,
)

router = APIRouter(prefix="/admin")


def require_admin(x_admin_key: str = Header(default="")) -> None:
    if not settings.admin_api_key:
        raise HTTPException(status_code=404, detail="Not Found")
    if not secrets.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=401, detail="Invalid admin key")


AdminDep = Depends(require_admin)
SessionDep = Depends(get_session)


class TenantCreate(BaseModel):
    client_id: str
    name: str = ""
    rpm: int = 60
    clause_per_day: int = 10


@router.post("/tenants", status_code=201, dependencies=[AdminDep])
async def admin_create_tenant(body: TenantCreate, session=SessionDep) -> dict:
    try:
        key = await create_tenant(session, body.client_id, body.name, body.rpm, body.clause_per_day)
    except ValueError as exc:
        status = 409 if "already exists" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"client_id": body.client_id, "api_key": key}


@router.post("/tenants/{client_id}/keys", status_code=201, dependencies=[AdminDep])
async def admin_new_key(client_id: str, session=SessionDep) -> dict:
    try:
        key = await new_key(session, client_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"api_key": key}


@router.delete("/keys/{prefix}", status_code=204, dependencies=[AdminDep])
async def admin_revoke_key(prefix: str, session=SessionDep) -> None:
    try:
        await revoke_key(session, prefix)
    except ValueError as exc:
        status = 409 if "multiple" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc


class TenantPatch(BaseModel):
    rpm: int | None = None
    clause_per_day: int | None = None
    status: str | None = None


@router.patch("/tenants/{client_id}", dependencies=[AdminDep])
async def admin_patch_tenant(client_id: str, body: TenantPatch, session=SessionDep) -> dict:
    if body.rpm is None and body.clause_per_day is None and body.status is None:
        raise HTTPException(status_code=422, detail="Provide at least one field to update")
    try:
        if body.rpm is not None or body.clause_per_day is not None:
            await set_limits(session, client_id, body.rpm, body.clause_per_day)
        if body.status is not None:
            await set_status(session, client_id, body.status)
    except ValueError as exc:
        status = 404 if "not found" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"client_id": client_id}


@router.get("/tenants/{client_id}", dependencies=[AdminDep])
async def admin_tenant_info(client_id: str, session=SessionDep) -> dict:
    try:
        return await tenant_info(session, client_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tenants/{client_id}/usage", dependencies=[AdminDep])
async def admin_usage(client_id: str, days: int = 30, session=SessionDep) -> list[dict]:
    try:
        return await usage_rows(session, client_id, days)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
