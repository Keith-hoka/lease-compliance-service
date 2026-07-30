"""Tenant administration commands, shared by the CLI and startup import."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.keys import generate_key, hash_key, key_prefix
from app.models import ApiKey, Tenant


async def _tenant_by_client_id(session: AsyncSession, client_id: str) -> Tenant:
    tenant = (
        await session.execute(select(Tenant).where(Tenant.client_id == client_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise ValueError(f"tenant {client_id!r} not found")
    return tenant


async def create_tenant(
    session: AsyncSession,
    client_id: str,
    name: str = "",
    rpm: int = 60,
    clause_per_day: int = 10,
) -> str:
    existing = (
        await session.execute(select(Tenant).where(Tenant.client_id == client_id))
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"tenant {client_id!r} already exists")
    tenant = Tenant(
        client_id=client_id, name=name, rpm_limit=rpm, clause_audits_per_day=clause_per_day
    )
    session.add(tenant)
    await session.flush()
    key = generate_key()
    session.add(ApiKey(tenant_id=tenant.id, key_hash=hash_key(key), prefix=key_prefix(key)))
    await session.commit()
    return key


async def new_key(session: AsyncSession, client_id: str) -> str:
    tenant = await _tenant_by_client_id(session, client_id)
    key = generate_key()
    session.add(ApiKey(tenant_id=tenant.id, key_hash=hash_key(key), prefix=key_prefix(key)))
    await session.commit()
    return key


async def revoke_key(session: AsyncSession, prefix: str) -> None:
    api_key = (
        await session.execute(select(ApiKey).where(ApiKey.prefix == prefix))
    ).scalar_one_or_none()
    if api_key is None:
        raise ValueError(f"no key with prefix {prefix!r}")
    api_key.status = "revoked"
    await session.commit()


async def set_limits(
    session: AsyncSession,
    client_id: str,
    rpm: int | None = None,
    clause_per_day: int | None = None,
) -> None:
    tenant = await _tenant_by_client_id(session, client_id)
    if rpm is not None:
        tenant.rpm_limit = rpm
    if clause_per_day is not None:
        tenant.clause_audits_per_day = clause_per_day
    await session.commit()


async def set_status(session: AsyncSession, client_id: str, status: str) -> None:
    tenant = await _tenant_by_client_id(session, client_id)
    tenant.status = status
    await session.commit()


async def import_env_keys(session: AsyncSession) -> int:
    """Seed tenants and keys from the API_KEYS env pairs. Idempotent."""
    imported = 0
    for entry in settings.api_keys.split(","):
        if ":" not in entry:
            continue
        key, client_id = (part.strip() for part in entry.split(":", 1))
        if not key or not client_id:
            continue
        exists = (
            await session.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(key)))
        ).scalar_one_or_none()
        if exists is not None:
            continue
        tenant = (
            await session.execute(select(Tenant).where(Tenant.client_id == client_id))
        ).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(client_id=client_id, name=client_id)
            session.add(tenant)
            await session.flush()
        session.add(ApiKey(tenant_id=tenant.id, key_hash=hash_key(key), prefix=key_prefix(key)))
        imported += 1
    await session.commit()
    return imported
