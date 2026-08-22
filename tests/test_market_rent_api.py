from decimal import Decimal

from sqlalchemy import select

from app.models import RentStatistic, UsageCounter

KEY = {"X-API-Key": "test-key"}
PARAMS = {"jurisdiction": "NSW", "area": "2000", "dwelling_type": "unit", "bedrooms": 2}


def _row(jurisdiction, period, area, median, p25, p75, sample) -> RentStatistic:
    return RentStatistic(
        jurisdiction=jurisdiction,
        period=period,
        area_code=area,
        dwelling_type="unit",
        bedrooms=2,
        median=Decimal(median),
        p25=None if p25 is None else Decimal(p25),
        p75=None if p75 is None else Decimal(p75),
        sample_size=sample,
        source_url="u",
    )


async def _seed(session):
    rows = [_row("NSW", "2026-07", "2000", 760, "697.5", "886.25", 170)]
    rows += [_row("NSW", f"2026-{m:02d}", "2000", 750, 690, 880, 160) for m in range(6, 0, -1)]
    rows += [_row("NSW", f"2025-{m:02d}", "2000", 700, 650, 800, 150) for m in range(12, 6, -1)]
    label = "Albert Park-Middle Park-West St Kilda"
    rows += [
        _row("VIC", "2025-Q3", label, 643, None, None, 144),
        _row("VIC", "2024-Q3", label, 650, None, None, 150),
    ]
    session.add_all(rows)
    await session.commit()


async def test_requires_api_key(client):
    response = await client.get("/v1/market-rent", params=PARAMS)
    assert response.status_code == 401


async def test_nsw_estimate_shape(client, db_session, seeded_tenants):
    await _seed(db_session)
    response = await client.get(
        "/v1/market-rent", params={**PARAMS, "as_at": "2026-08-21"}, headers=KEY
    )
    assert response.status_code == 200
    body = response.json()
    assert body["area"] == "2000" and body["area_label"] == "2000"
    assert body["dwelling_type"] == "unit" and body["bedrooms"] == 2
    assert body["estimate_weekly"] == "760"
    assert body["band"] == {"low": "698", "high": "886"}
    assert body["basis"] == "median"
    assert body["period"] == "2026-07" and body["period_end"] == "2026-07-31"
    assert body["stale"] is False and body["sample_size"] == 170 and body["fallback"] is None
    assert [p["period"] for p in body["series"]][:2] == ["2026-07", "2026-06"]
    assert len(body["series"]) == 8
    assert body["trend"] == {"from_period": "2025-07", "from_median": "700.00", "change_pct": "8.6"}
    assert body["source"]["name"].startswith("NSW Fair Trading")
    assert body["disclaimer"] == "General information, not legal advice."


async def test_vic_raw_suburb_resolves_and_is_stale(client, db_session, seeded_tenants):
    await _seed(db_session)
    response = await client.get(
        "/v1/market-rent",
        params={
            "jurisdiction": "VIC",
            "area": "albert park",
            "dwelling_type": "unit",
            "bedrooms": 2,
            "as_at": "2026-08-21",
        },
        headers=KEY,
    )
    body = response.json()
    assert body["area"] == "albert park"
    assert body["area_label"] == "Albert Park-Middle Park-West St Kilda"
    assert body["estimate_weekly"] == "643" and body["band"] == {"low": "592", "high": "694"}
    assert body["stale"] is True and body["period_end"] == "2025-09-30"
    assert body["trend"] == {
        "from_period": "2024-Q3",
        "from_median": "650.00",
        "change_pct": "-1.1",
    }
    assert body["source"]["licence"] == "CC BY 4.0"


async def test_unknown_area_is_the_empty_shape(client, db_session, seeded_tenants):
    await _seed(db_session)
    response = await client.get("/v1/market-rent", params={**PARAMS, "area": "2999"}, headers=KEY)
    assert response.status_code == 200
    body = response.json()
    assert body["area"] == "2999" and body["area_label"] is None
    assert body["estimate_weekly"] is None and body["band"] is None and body["trend"] is None
    assert body["period"] is None and body["period_end"] is None and body["sample_size"] is None
    assert body["fallback"] is None and body["stale"] is False and body["series"] == []
    assert body["basis"] == "median" and body["source"]["name"].startswith("NSW Fair Trading")


async def test_usage_recorded_once(client, db_session, seeded_tenants):
    await _seed(db_session)
    response = await client.get("/v1/market-rent", params=PARAMS, headers=KEY)
    assert response.status_code == 200
    row = (
        await db_session.execute(
            select(UsageCounter).where(UsageCounter.endpoint_class == "market_rent")
        )
    ).scalar_one()
    assert row.count == 1
