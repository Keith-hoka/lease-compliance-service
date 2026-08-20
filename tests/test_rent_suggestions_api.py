from decimal import Decimal

from app.llm.failover import FailoverJudge
from app.models import RentStatistic, UsageCounter
from app.rent_suggest import service as suggest_service
from tests.test_rent_suggest_law import _seed_nsw_corpus

KEY = {"X-API-Key": "test-key"}
BODY = {
    "jurisdiction": "NSW",
    "as_at": "2026-08-17",
    "property": {"area_key": "2000", "dwelling_type": "unit", "bedrooms": 2},
    "lease": {
        "rent_amount": "600",
        "rent_frequency": "weekly",
        "start_date": "2024-10-01",
        "end_date": "2026-09-30",
    },
    "renewal_start": "2026-10-01",
}


async def _seed_market(session):
    session.add(
        RentStatistic(
            jurisdiction="NSW",
            period="2026-07",
            area_code="2000",
            dwelling_type="unit",
            bedrooms=2,
            median=Decimal(760),
            p25=Decimal("697.5"),
            p75=Decimal("886.25"),
            sample_size=170,
            source_url="u",
        )
    )
    await session.commit()


def _fake_judge(monkeypatch, weekly="720", reasoning="Median 760 supports 720."):
    async def fake(doc, instruction, output_model):
        return output_model.model_validate({"suggested_weekly": weekly, "reasoning": reasoning})

    monkeypatch.setattr(
        suggest_service, "make_judge", lambda: FailoverJudge(primary=fake, primary_ref="fake-model")
    )


async def test_requires_api_key(client):
    assert (await client.post("/v1/rent-suggestions", json=BODY)).status_code == 401


async def test_full_response_shape(client, db_session, seeded_tenants, monkeypatch):
    await _seed_market(db_session)
    _fake_judge(monkeypatch, weekly="650", reasoning="Cap 690 binds below the market band.")
    response = await client.post("/v1/rent-suggestions", json=BODY, headers=KEY)
    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["current_weekly"]) == Decimal(600)
    assert (Decimal(body["range"]["low"]), Decimal(body["range"]["high"])) == (
        Decimal(600),
        Decimal(690),
    )
    assert body["market_gap"] == "above_cap"
    assert Decimal(body["suggested_weekly"]) == Decimal(650)
    assert body["market"]["period"] == "2026-07" and body["market"]["sample_size"] == 170
    assert body["law_blocked"] is False and body["law_card"]
    assert (
        body["model"] == "fake-model"
        and body["disclaimer"] == "General information, not legal advice."
    )
    assert body["engine_version"]


async def test_no_market_data_uses_cap_band(client, db_session, seeded_tenants, monkeypatch):
    _fake_judge(monkeypatch, weekly="650")
    response = await client.post("/v1/rent-suggestions", json=BODY, headers=KEY)
    body = response.json()
    assert body["market_gap"] == "no_data" and body["market"] is None
    assert (Decimal(body["range"]["low"]), Decimal(body["range"]["high"])) == (
        Decimal(600),
        Decimal(690),
    )


async def test_blocked_by_law_skips_model(client, db_session, seeded_tenants, monkeypatch):
    called = []

    async def fake(doc, instruction, output_model):
        called.append(1)

    await _seed_nsw_corpus(db_session)
    await db_session.commit()
    monkeypatch.setattr(
        suggest_service, "make_judge", lambda: FailoverJudge(primary=fake, primary_ref="fake")
    )
    body = dict(
        BODY,
        lease=dict(
            BODY["lease"], rent_increases=[{"effective_on": "2026-04-01", "new_amount": "600"}]
        ),
    )
    response = await client.post("/v1/rent-suggestions", json=body, headers=KEY)
    data = response.json()
    assert data["law_blocked"] is True and Decimal(data["suggested_weekly"]) == Decimal(600)
    assert data["model"] is None and called == []


async def test_judge_failure_is_502(client, db_session, seeded_tenants, monkeypatch):
    from app.llm.failover import ProviderDown

    async def down(doc, instruction, output_model):
        raise ProviderDown("x")

    await _seed_market(db_session)
    monkeypatch.setattr(
        suggest_service, "make_judge", lambda: FailoverJudge(primary=down, primary_ref="p")
    )
    response = await client.post("/v1/rent-suggestions", json=BODY, headers=KEY)
    assert response.status_code == 502 and response.json()["detail"] == {
        "code": "judge_unavailable"
    }


async def test_usage_recorded(client, db_session, seeded_tenants, monkeypatch):
    from sqlalchemy import select

    _fake_judge(monkeypatch, weekly="650")
    await client.post("/v1/rent-suggestions", json=BODY, headers=KEY)
    row = (
        await db_session.execute(
            select(UsageCounter).where(UsageCounter.endpoint_class == "rent_suggestions")
        )
    ).scalar_one()
    assert row.count == 1
