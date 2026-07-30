"""Tenant administration commands, shared by the CLI and startup import."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.keys import generate_key, hash_key, key_prefix
from app.models import ApiKey, Tenant, UsageCounter


def _check_limits(rpm: int | None, clause_per_day: int | None) -> None:
    if rpm is not None and rpm < 1:
        raise ValueError("rpm must be at least 1; use suspend to block a tenant")
    if clause_per_day is not None and clause_per_day < 0:
        raise ValueError("clause_per_day must not be negative")


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
    _check_limits(rpm, clause_per_day)
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
    rows = (
        await session.execute(
            select(ApiKey, Tenant.client_id)
            .join(Tenant, ApiKey.tenant_id == Tenant.id)
            .where(ApiKey.prefix == prefix)
        )
    ).all()
    if not rows:
        raise ValueError(f"no key with prefix {prefix!r}")
    if len(rows) > 1:
        owners = ", ".join(sorted(client_id for _, client_id in rows))
        raise ValueError(f"prefix {prefix!r} matches keys of multiple tenants: {owners}")
    rows[0][0].status = "revoked"
    await session.commit()


async def set_limits(
    session: AsyncSession,
    client_id: str,
    rpm: int | None = None,
    clause_per_day: int | None = None,
) -> None:
    _check_limits(rpm, clause_per_day)
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


async def tenant_lines(session: AsyncSession) -> list[str]:
    """One formatted line per tenant with today's usage."""
    tenants = (await session.execute(select(Tenant))).scalars().all()
    today = datetime.now(UTC).date()
    counters = (
        (await session.execute(select(UsageCounter).where(UsageCounter.day == today)))
        .scalars()
        .all()
    )
    by_tenant: dict = {}
    for c in counters:
        by_tenant.setdefault(c.tenant_id, {})[c.endpoint_class] = c.count
    return [
        f"{t.client_id:20} {t.status:10} rpm={t.rpm_limit:<5} "
        f"clause/day={t.clause_audits_per_day:<5} today={by_tenant.get(t.id, {}) or '-'}"
        for t in tenants
    ]


async def usage_lines(session: AsyncSession, client_id: str, days: int) -> list[str]:
    """Per-day usage counter lines for one tenant."""
    tenant = await _tenant_by_client_id(session, client_id)
    since = datetime.now(UTC).date() - timedelta(days=days)
    rows = (
        (
            await session.execute(
                select(UsageCounter)
                .where(UsageCounter.tenant_id == tenant.id, UsageCounter.day >= since)
                .order_by(UsageCounter.day, UsageCounter.endpoint_class)
            )
        )
        .scalars()
        .all()
    )
    return [f"{r.day} {r.endpoint_class:14} {r.count}" for r in rows]
