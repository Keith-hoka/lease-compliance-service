"""Database-backed API key authentication with a short process-local cache."""

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.keys import hash_key
from app.models import ApiKey, Tenant

CACHE_TTL_SECONDS = 60


@dataclass(frozen=True)
class TenantContext:
    tenant_id: uuid.UUID
    client_id: str
    status: str
    rpm_limit: int
    clause_audits_per_day: int


_cache: dict[str, tuple[TenantContext, float]] = {}


def clear_auth_cache() -> None:
    _cache.clear()


def time_now() -> datetime:
    return datetime.now(UTC)


async def require_tenant(
    session: Annotated[AsyncSession, Depends(get_session)],
    x_api_key: str = Header(default=""),
) -> TenantContext:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    key_hash = hash_key(x_api_key)
    now = time.monotonic()
    cached = _cache.get(key_hash)
    if cached is not None and cached[1] > now:
        ctx = cached[0]
    else:
        row = (
            await session.execute(
                select(ApiKey, Tenant)
                .join(Tenant, ApiKey.tenant_id == Tenant.id)
                .where(ApiKey.key_hash == key_hash, ApiKey.status == "active")
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        api_key, tenant = row
        await session.execute(
            update(ApiKey).where(ApiKey.id == api_key.id).values(last_used_at=time_now())
        )
        await session.commit()
        ctx = TenantContext(
            tenant_id=tenant.id,
            client_id=tenant.client_id,
            status=tenant.status,
            rpm_limit=tenant.rpm_limit,
            clause_audits_per_day=tenant.clause_audits_per_day,
        )
        _cache[key_hash] = (ctx, now + CACHE_TTL_SECONDS)
    if ctx.status != "active":
        raise HTTPException(status_code=403, detail="Tenant suspended")
    return ctx


TenantDep = Annotated[TenantContext, Depends(require_tenant)]


async def require_api_key(tenant: TenantDep) -> str:
    return tenant.client_id
