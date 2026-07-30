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
