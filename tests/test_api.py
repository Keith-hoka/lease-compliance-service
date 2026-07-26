from datetime import date

import pytest

from app.core.config import settings
from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act, Audit

KEY = {"X-API-Key": "test-key"}


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "test-key:testco,other-key:otherco")


@pytest.fixture
async def seeded(db_session):
    act = Act(
        jurisdiction="NSW",
        slug="act-2010-042",
        title="Residential Tenancies Act 2010",
        source_url="x",
    )
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session,
        act.id,
        date(2011, 1, 31),
        [
            ParsedSection("159", "Payment of bonds", "4 weeks limit body", "Part 8", None),
            ParsedSection("33", "Payment of rent by tenant", "advance body", "Part 3", None),
        ],
    )
    await db_session.commit()


AUDIT_BODY = {
    "jurisdiction": "NSW",
    "lease": {
        "rent_amount": "600",
        "rent_frequency": "weekly",
        "start_date": "2026-01-01",
        "bond_amount": "3000",
    },
}


async def test_missing_key_is_401(client):
    assert (await client.post("/v1/audits", json=AUDIT_BODY)).status_code == 401


async def test_create_and_get_audit(client, seeded):
    created = await client.post("/v1/audits", json=AUDIT_BODY, headers=KEY)
    assert created.status_code == 201
    body = created.json()
    assert body["engine_version"]
    bond = next(f for f in body["findings"] if f["rule_id"] == "nsw.bond_max_4_weeks")
    assert bond["verdict"] == "red"

    fetched = await client.get(f"/v1/audits/{body['id']}", headers=KEY)
    assert fetched.status_code == 200
    assert fetched.json()["findings"] == body["findings"]


async def test_unknown_jurisdiction_is_422(client, seeded):
    bad = dict(AUDIT_BODY, jurisdiction="VIC")
    assert (await client.post("/v1/audits", json=bad, headers=KEY)).status_code == 422


OTHER = {"X-API-Key": "other-key"}


async def test_create_echoes_client_ref(client, seeded):
    body = dict(AUDIT_BODY, client_ref="lease-77")
    created = await client.post("/v1/audits", json=body, headers=KEY)
    assert created.status_code == 201
    assert created.json()["client_ref"] == "lease-77"


async def test_cross_tenant_audit_is_404(client, seeded):
    created = await client.post("/v1/audits", json=AUDIT_BODY, headers=KEY)
    audit_id = created.json()["id"]
    assert (await client.get(f"/v1/audits/{audit_id}", headers=KEY)).status_code == 200
    assert (await client.get(f"/v1/audits/{audit_id}", headers=OTHER)).status_code == 404


@pytest.fixture
async def seeded_changes(db_session):
    from app.models import AuditChange

    audits = {}
    for client_id in ("testco", "otherco"):
        audit = Audit(
            jurisdiction="NSW",
            as_at=date(2026, 1, 1),
            input={},
            findings=[],
            engine_version="1.0.0",
            client_id=client_id,
            client_ref="lease-1",
        )
        db_session.add(audit)
        await db_session.flush()
        audits[client_id] = audit
    for client_id, audit in audits.items():
        db_session.add(
            AuditChange(
                client_id=client_id,
                client_ref="lease-1",
                old_audit_id=audit.id,
                new_audit_id=audit.id,
                changes={"nsw.bond_max_4_weeks": {"from": "green", "to": "red"}},
            )
        )
    await db_session.commit()


async def test_changes_are_tenant_scoped(client, seeded_changes):
    listed = await client.get("/v1/audit-changes", headers=KEY)
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["client_ref"] == "lease-1"
    assert body[0]["changes"]["nsw.bond_max_4_weeks"]["to"] == "red"


async def test_changes_since_filter(client, seeded_changes):
    listed = (await client.get("/v1/audit-changes", headers=KEY)).json()
    cursor = listed[0]["created_at"]
    after = await client.get("/v1/audit-changes", params={"since": cursor}, headers=KEY)
    assert after.json() == []


async def test_changes_require_key(client):
    assert (await client.get("/v1/audit-changes")).status_code == 401


async def test_section_lookup(client, seeded):
    ok = await client.get(
        "/v1/legislation/sections",
        params={"act": "act-2010-042", "section_no": "159", "as_at": "2026-01-01"},
        headers=KEY,
    )
    assert ok.status_code == 200
    assert ok.json()["heading"] == "Payment of bonds"
    missing = await client.get(
        "/v1/legislation/sections",
        params={"act": "act-2010-042", "section_no": "159", "as_at": "2005-01-01"},
        headers=KEY,
    )
    assert missing.status_code == 404
