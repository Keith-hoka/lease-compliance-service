from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import UsageCounter


@pytest.fixture(autouse=True)
async def keys(seeded_tenants):
    """Run against the seeded tenants."""


async def _counters(db_session, tenant_id):
    rows = (
        await db_session.execute(select(UsageCounter).where(UsageCounter.tenant_id == tenant_id))
    ).scalars()
    return {(c.day, c.endpoint_class): c.count for c in rows}


async def test_deterministic_audit_counts_once(client, seeded_tenants, db_session):
    body = {
        "jurisdiction": "NSW",
        "lease": {
            "rent_amount": "600",
            "rent_frequency": "weekly",
            "start_date": "2026-01-01",
        },
    }
    for _ in range(2):
        response = await client.post("/v1/audits", json=body, headers={"X-API-Key": "test-key"})
        assert response.status_code == 201
    counters = await _counters(db_session, seeded_tenants["testco"].id)
    today = datetime.now(UTC).date()
    assert counters[(today, "audit")] == 2


async def test_legislation_miss_does_not_count(client, seeded_tenants, db_session):
    response = await client.get(
        "/v1/legislation/sections",
        params={"act": "act-2010-042", "section_no": "19", "as_at": "2026-07-29"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 404
    counters = await _counters(db_session, seeded_tenants["testco"].id)
    today = datetime.now(UTC).date()
    assert (today, "legislation") not in counters


async def test_clause_post_counts_and_daily_quota_blocks(
    client, seeded_tenants, db_session, monkeypatch
):
    from app.core.config import settings
    from app.models import Tenant

    monkeypatch.setattr(settings, "anthropic_api_key", "test")
    tenant = await db_session.get(Tenant, seeded_tenants["testco"].id)
    tenant.clause_audits_per_day = 1
    await db_session.commit()

    files = {"file": ("lease.pdf", b"%PDF-1.4 fake", "application/pdf")}
    data = {"payload": '{"jurisdiction": "NSW"}'}
    headers = {"X-API-Key": "test-key"}

    first = await client.post("/v1/clause-audits", data=data, files=files, headers=headers)
    assert first.status_code == 202
    second = await client.post("/v1/clause-audits", data=data, files=files, headers=headers)
    assert second.status_code == 429
    assert "quota" in second.json()["detail"].lower()
    assert "utc" in second.json()["detail"].lower()

    counters = await _counters(db_session, seeded_tenants["testco"].id)
    today = datetime.now(UTC).date()
    assert counters[(today, "clause_audit")] == 1


async def test_legislation_hit_counts(client, seeded_tenants, db_session):
    from datetime import date

    from app.ingest.loader import load_version
    from app.ingest.parser import ParsedSection
    from app.models import Act

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
        [ParsedSection("19", "Prohibited terms", "terms body", "Part 3", None)],
    )
    await db_session.commit()

    response = await client.get(
        "/v1/legislation/sections",
        params={"act": "act-2010-042", "section_no": "19", "as_at": "2026-07-29"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    counters = await _counters(db_session, seeded_tenants["testco"].id)
    today = datetime.now(UTC).date()
    assert counters[(today, "legislation")] == 1


async def test_concurrent_clause_posts_respect_quota(
    client, seeded_tenants, db_session, monkeypatch
):
    import asyncio

    from app.core.config import settings
    from app.models import Tenant

    monkeypatch.setattr(settings, "anthropic_api_key", "test")
    tenant = await db_session.get(Tenant, seeded_tenants["testco"].id)
    tenant.clause_audits_per_day = 2
    await db_session.commit()

    async def post():
        return await client.post(
            "/v1/clause-audits",
            data={"payload": '{"jurisdiction": "NSW"}'},
            files={"file": ("lease.pdf", b"%PDF-1.4 fake", "application/pdf")},
            headers={"X-API-Key": "test-key"},
        )

    responses = await asyncio.gather(*(post() for _ in range(5)))
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [202, 202, 429, 429, 429]
