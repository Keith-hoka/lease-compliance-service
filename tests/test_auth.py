import pytest
from sqlalchemy import select, update

from app.core.auth import clear_auth_cache
from app.models import ApiKey, Tenant


@pytest.fixture
async def api(client, seeded_tenants):
    return client


async def test_valid_key_reaches_endpoint(api):
    response = await api.get("/v1/audit-changes", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200


async def test_unknown_key_is_401(api):
    response = await api.get("/v1/audit-changes", headers={"X-API-Key": "nope"})
    assert response.status_code == 401


async def test_missing_key_is_401(api):
    response = await api.get("/v1/audit-changes")
    assert response.status_code == 401


async def test_suspended_tenant_is_403(api, seeded_tenants, db_session):
    tenant = await db_session.get(Tenant, seeded_tenants["testco"].id)
    tenant.status = "suspended"
    await db_session.commit()
    clear_auth_cache()
    response = await api.get("/v1/audit-changes", headers={"X-API-Key": "test-key"})
    assert response.status_code == 403


async def test_revoked_key_is_401_after_cache_clear(api, db_session):
    ok = await api.get("/v1/audit-changes", headers={"X-API-Key": "test-key"})
    assert ok.status_code == 200

    await db_session.execute(update(ApiKey).values(status="revoked"))
    await db_session.commit()
    clear_auth_cache()
    response = await api.get("/v1/audit-changes", headers={"X-API-Key": "test-key"})
    assert response.status_code == 401


async def test_last_used_at_set_on_cache_miss(api, db_session):
    response = await api.get("/v1/audit-changes", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    from app.core.keys import hash_key

    key = (
        await db_session.execute(select(ApiKey).where(ApiKey.key_hash == hash_key("test-key")))
    ).scalar_one()
    assert key.last_used_at is not None


async def test_ttl_expiry_honours_revocation(api, db_session, monkeypatch):
    from app.core import auth

    monkeypatch.setattr(auth, "CACHE_TTL_SECONDS", 0)
    ok = await api.get("/v1/audit-changes", headers={"X-API-Key": "test-key"})
    assert ok.status_code == 200

    await db_session.execute(update(ApiKey).values(status="revoked"))
    await db_session.commit()
    response = await api.get("/v1/audit-changes", headers={"X-API-Key": "test-key"})
    assert response.status_code == 401
