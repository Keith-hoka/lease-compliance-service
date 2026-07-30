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


async def test_rejects_non_positive_limits(db_session):
    with pytest.raises(ValueError, match="rpm"):
        await create_tenant(db_session, "acme", rpm=0)
    await create_tenant(db_session, "acme")
    with pytest.raises(ValueError, match="rpm"):
        await set_limits(db_session, "acme", rpm=0)
    with pytest.raises(ValueError, match="clause_per_day"):
        await set_limits(db_session, "acme", clause_per_day=-1)
    await set_limits(db_session, "acme", clause_per_day=0)


async def test_revoke_key_with_ambiguous_prefix_names_owners(db_session):
    from app.core.keys import hash_key

    await create_tenant(db_session, "acme")
    await create_tenant(db_session, "zenco")
    tenants = {t.client_id: t for t in (await db_session.execute(select(Tenant))).scalars().all()}
    db_session.add_all(
        [
            ApiKey(tenant_id=tenants["acme"].id, key_hash=hash_key("k1"), prefix="lk_same0"),
            ApiKey(tenant_id=tenants["zenco"].id, key_hash=hash_key("k2"), prefix="lk_same0"),
        ]
    )
    await db_session.commit()
    with pytest.raises(ValueError, match="acme, zenco"):
        await revoke_key(db_session, "lk_same0")


async def test_tenant_lines_and_usage_lines(db_session):
    from datetime import UTC, datetime

    from app.models import UsageCounter
    from app.tenants import tenant_lines, usage_lines

    await create_tenant(db_session, "acme", name="Acme Pty")
    tenant = (
        await db_session.execute(select(Tenant).where(Tenant.client_id == "acme"))
    ).scalar_one()
    db_session.add(
        UsageCounter(
            tenant_id=tenant.id,
            day=datetime.now(UTC).date(),
            endpoint_class="audit",
            count=3,
        )
    )
    await db_session.commit()

    lines = await tenant_lines(db_session)
    assert len(lines) == 1
    assert "acme" in lines[0]
    assert "'audit': 3" in lines[0]

    rows = await usage_lines(db_session, "acme", days=7)
    assert len(rows) == 1
    assert "audit" in rows[0]

    with pytest.raises(ValueError, match="not found"):
        await usage_lines(db_session, "ghost", days=7)
