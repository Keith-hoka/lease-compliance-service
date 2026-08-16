from pathlib import Path

from sqlalchemy import select

from app.models import UsageCounter
from app.rent_stats.loader import load_nsw_file, load_vic_file

FIXTURES = Path(__file__).parent / "fixtures" / "rent_stats"
KEY = {"X-API-Key": "test-key"}


async def _seed(session):
    await load_nsw_file(
        session, "nsw_july_2026.xlsx", (FIXTURES / "nsw_lodgements_sample.xlsx").read_bytes(), "u1"
    )
    await load_vic_file(
        session, "vic.xlsx", (FIXTURES / "vic_moving_annual_sample.xlsx").read_bytes(), "u2"
    )
    await session.commit()


async def test_requires_api_key(client):
    response = await client.get(
        "/v1/rent-statistics",
        params={"jurisdiction": "NSW", "area": "2000", "dwelling_type": "unit"},
    )
    assert response.status_code == 401


async def test_nsw_series_newest_first_with_percentiles(client, db_session, seeded_tenants):
    await _seed(db_session)
    response = await client.get(
        "/v1/rent-statistics",
        params={"jurisdiction": "NSW", "area": "2000", "dwelling_type": "unit"},
        headers=KEY,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["jurisdiction"] == "NSW" and body["bedrooms"] is None
    assert body["series"] == [
        {
            "period": "2026-07",
            "median": "760.00",
            "p25": "697.50",
            "p75": "886.25",
            "sample_size": 6,
        }
    ]
    assert body["source"]["name"].startswith("NSW Fair Trading")


async def test_vic_series_respects_periods_cap_and_order(client, db_session, seeded_tenants):
    await _seed(db_session)
    response = await client.get(
        "/v1/rent-statistics",
        params={
            "jurisdiction": "VIC",
            "area": "Albert Park-Middle Park-West St Kilda",
            "dwelling_type": "unit",
            "bedrooms": 2,
            "periods": 3,
        },
        headers=KEY,
    )
    body = response.json()
    assert [s["period"] for s in body["series"]] == ["2025-Q3", "2025-Q2", "2025-Q1"]
    assert body["series"][0]["p25"] is None and body["series"][0]["median"] == "643.00"


async def test_unknown_area_is_empty_series(client, db_session, seeded_tenants):
    await _seed(db_session)
    response = await client.get(
        "/v1/rent-statistics",
        params={"jurisdiction": "NSW", "area": "0000", "dwelling_type": "unit"},
        headers=KEY,
    )
    assert response.status_code == 200 and response.json()["series"] == []


async def test_periods_above_cap_is_422(client, seeded_tenants):
    response = await client.get(
        "/v1/rent-statistics",
        params={"jurisdiction": "NSW", "area": "2000", "dwelling_type": "unit", "periods": 41},
        headers=KEY,
    )
    assert response.status_code == 422


async def test_usage_is_recorded(client, db_session, seeded_tenants):
    await _seed(db_session)
    response = await client.get(
        "/v1/rent-statistics",
        params={"jurisdiction": "NSW", "area": "2000", "dwelling_type": "unit"},
        headers=KEY,
    )
    assert response.status_code == 200
    row = (
        await db_session.execute(
            select(UsageCounter).where(UsageCounter.endpoint_class == "rent_statistics")
        )
    ).scalar_one()
    assert row.count == 1
