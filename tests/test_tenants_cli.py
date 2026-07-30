import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models import ApiKey, Tenant
from app.tenants import (
    create_tenant,
    import_env_keys,
    new_key,
    revoke_key,
    set_limits,
    set_status,
)


async def test_create_tenant_returns_plaintext_key_once(db_session):
    key = await create_tenant(db_session, "acme", name="Acme Pty")
    assert key.startswith("lk_")
    tenant = (
        await db_session.execute(select(Tenant).where(Tenant.client_id == "acme"))
    ).scalar_one()
    assert tenant.rpm_limit == 60
    assert tenant.clause_audits_per_day == 10
    stored = (await db_session.execute(select(ApiKey))).scalar_one()
    assert stored.key_hash != key
    assert stored.prefix == key[:8]


async def test_create_duplicate_client_id_raises(db_session):
    await create_tenant(db_session, "acme")
    with pytest.raises(ValueError, match="already exists"):
        await create_tenant(db_session, "acme")


async def test_new_key_and_revoke(db_session):
    await create_tenant(db_session, "acme")
    second = await new_key(db_session, "acme")
    keys = (await db_session.execute(select(ApiKey))).scalars().all()
    assert len(keys) == 2
    await revoke_key(db_session, second[:8])
    revoked = (
        await db_session.execute(select(ApiKey).where(ApiKey.prefix == second[:8]))
    ).scalar_one()
    assert revoked.status == "revoked"


async def test_set_limits_and_status(db_session):
    await create_tenant(db_session, "acme")
    await set_limits(db_session, "acme", rpm=300, clause_per_day=200)
    await set_status(db_session, "acme", "suspended")
    tenant = (
        await db_session.execute(select(Tenant).where(Tenant.client_id == "acme"))
    ).scalar_one()
    assert tenant.rpm_limit == 300
    assert tenant.clause_audits_per_day == 200
    assert tenant.status == "suspended"


async def test_import_env_keys_is_idempotent(db_session, monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "legacy-key:rentalapp")
    first = await import_env_keys(db_session)
    second = await import_env_keys(db_session)
    assert first == 1
    assert second == 0
    tenant = (
        await db_session.execute(select(Tenant).where(Tenant.client_id == "rentalapp"))
    ).scalar_one()
    assert tenant.status == "active"


async def test_import_env_keys_noop_when_empty(db_session, monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "")
    assert await import_env_keys(db_session) == 0
