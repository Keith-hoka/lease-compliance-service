import pytest

from app.core.config import settings

ADMIN = {"X-Admin-Key": "test-admin-key"}


@pytest.fixture(autouse=True)
def admin_key(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "test-admin-key")


async def test_admin_404_when_key_unset(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "")
    response = await client.post("/admin/tenants", json={"client_id": "a"}, headers=ADMIN)
    assert response.status_code == 404


async def test_admin_401_on_wrong_key(client):
    response = await client.post(
        "/admin/tenants", json={"client_id": "a"}, headers={"X-Admin-Key": "wrong"}
    )
    assert response.status_code == 401


async def test_create_tenant_returns_key_once(client):
    response = await client.post(
        "/admin/tenants", json={"client_id": "acme", "name": "Acme"}, headers=ADMIN
    )
    assert response.status_code == 201
    body = response.json()
    assert body["client_id"] == "acme"
    assert body["api_key"].startswith("lk_")


async def test_duplicate_client_id_is_409(client):
    await client.post("/admin/tenants", json={"client_id": "acme"}, headers=ADMIN)
    response = await client.post("/admin/tenants", json={"client_id": "acme"}, headers=ADMIN)
    assert response.status_code == 409


async def test_new_key_and_revoke(client):
    await client.post("/admin/tenants", json={"client_id": "acme"}, headers=ADMIN)
    created = await client.post("/admin/tenants/acme/keys", headers=ADMIN)
    assert created.status_code == 201
    key = created.json()["api_key"]

    revoked = await client.delete(f"/admin/keys/{key[:8]}", headers=ADMIN)
    assert revoked.status_code == 204

    missing = await client.delete("/admin/keys/lk_nope0", headers=ADMIN)
    assert missing.status_code == 404


async def test_new_key_unknown_tenant_is_404(client):
    response = await client.post("/admin/tenants/ghost/keys", headers=ADMIN)
    assert response.status_code == 404


async def test_patch_limits_and_status(client):
    await client.post("/admin/tenants", json={"client_id": "acme"}, headers=ADMIN)
    response = await client.patch(
        "/admin/tenants/acme",
        json={"rpm": 120, "clause_per_day": 50, "status": "suspended"},
        headers=ADMIN,
    )
    assert response.status_code == 200

    info = (await client.get("/admin/tenants/acme", headers=ADMIN)).json()
    assert info["rpm_limit"] == 120
    assert info["clause_audits_per_day"] == 50
    assert info["status"] == "suspended"


async def test_patch_invalid_rpm_is_422(client):
    await client.post("/admin/tenants", json={"client_id": "acme"}, headers=ADMIN)
    response = await client.patch("/admin/tenants/acme", json={"rpm": 0}, headers=ADMIN)
    assert response.status_code == 422


async def test_tenant_info_includes_keys_and_today(client):
    created = await client.post(
        "/admin/tenants", json={"client_id": "acme", "name": "Acme"}, headers=ADMIN
    )
    prefix = created.json()["api_key"][:8]
    info = (await client.get("/admin/tenants/acme", headers=ADMIN)).json()
    assert info["name"] == "Acme"
    assert info["keys"][0]["prefix"] == prefix
    assert info["keys"][0]["status"] == "active"
    assert info["today"] == {"audit": 0, "clause_audit": 0, "legislation": 0}


async def test_tenant_info_unknown_is_404(client):
    response = await client.get("/admin/tenants/ghost", headers=ADMIN)
    assert response.status_code == 404


async def test_usage_rows(client, db_session):
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.models import Tenant, UsageCounter

    await client.post("/admin/tenants", json={"client_id": "acme"}, headers=ADMIN)
    tenant = (
        await db_session.execute(select(Tenant).where(Tenant.client_id == "acme"))
    ).scalar_one()
    db_session.add(
        UsageCounter(
            tenant_id=tenant.id,
            day=datetime.now(UTC).date(),
            endpoint_class="audit",
            count=4,
        )
    )
    await db_session.commit()

    rows = (await client.get("/admin/tenants/acme/usage?days=7", headers=ADMIN)).json()
    assert rows == [
        {"day": datetime.now(UTC).date().isoformat(), "endpoint_class": "audit", "count": 4}
    ]
