# Market Rent Estimation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET /v1/market-rent` — a deterministic market-rent estimate
(median, band, trend, staleness) for a property type in an area — and a
"Market rent" card on the SaaS property page with the gap to the
current rent.

**Architecture:** New `app/market_rent/` package with one composition
function `estimate()` built entirely on (b)'s reviewed market anchoring
(`resolve_area`, `market_cell`, `band_for`, `period_end`, `is_stale`)
plus a pure `trend()`; a thin schema + router. `market_cell` gains a
`periods` parameter so the estimate can fetch a year-deep series while
(b) keeps its default. SaaS: a proxy that wraps the service body with
the current lease's weekly rent and the gap, and a card loaded on page
open. No LLM, no consent gate.

**Tech Stack:** FastAPI, async SQLAlchemy, pydantic v2, pytest; SaaS:
FastAPI proxy + Next.js property page + Playwright.

**Spec:** `docs/superpowers/specs/2026-08-23-market-rent-estimation-design.md`

## Global Constraints

- `uv` only; ruff sequence in exact order before every push (`uv run ruff format .` -> `uv run ruff check --fix .` -> `uv run ruff check .` -> `uv run ruff format --check .`); TDD; no emojis; docstrings over comments; do not program defensively; commit trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Service full suite `uv run pytest -m "not llm_eval" -q`; SaaS backend `uv run pytest -q`, frontend `npm run lint` + `npx tsc --noEmit`.
- Money is `Decimal`; `estimate_weekly` and the band are whole dollars via (b)'s `dollars()`; `change_pct` is one decimal, `ROUND_HALF_UP`; pydantic serialises Decimals as JSON strings (`"760"`, `"-1.1"`); tests assert on parsed `Decimal(...)`/string equality exactly as written here.
- Endpoint: `GET /v1/market-rent?jurisdiction=NSW|VIC&area=<key>&dwelling_type=<t>&bedrooms=<n>[&as_at=YYYY-MM-DD]`; `TenantDep` + router-level `enforce_rate_limit`; usage class `market_rent` recorded once per request; `as_at` defaults to `sydney_today()`.
- Estimate = median of the cell `market_cell()` picks (its existing thin-sample fallback chain); band = NSW `[p25, p75]` / VIC `[median x 0.92, median x 1.08]` via `band_for()`; `basis` is the constant `"median"`; `fallback` `null | "bedrooms_all" | "dwelling_all"`; `area_label`, `period_end`, `stale` exactly as (b) defines them.
- Series: the estimate fetches 13 periods (so the period one year before the newest is available for both monthly NSW and quarterly VIC) and the response returns the newest 8. Trend compares the newest period with the same period one year earlier (`2026-07` -> `2025-07`, `2025-Q3` -> `2024-Q3`), matched exactly in the fetched series; absent -> `trend` null. `change_pct = (latest - from) / from x 100`.
- No data (area unresolvable or no rows at any fallback level) -> HTTP 200 with `area_label`, `estimate_weekly`, `band`, `period`, `period_end`, `sample_size`, `fallback`, `trend` null, `stale` false, `series` empty; `jurisdiction`, `area`, `dwelling_type`, `bedrooms` echo the request; `basis`, `source`, `disclaimer` constant. Never 404.
- No LLM anywhere; no `-m llm_eval`; no consent gate in the SaaS; the AI disclosure copy is unchanged.
- SaaS proxy `GET /api/v1/properties/{property_id}/market-rent` (landlord + property_manager roles, `get_owned_property`, `property_jurisdiction` unresolved -> 422 `"Property state unresolved: <reason>"`, compliance disabled -> 503, httpx error -> 502) returns `{"market": <service body verbatim>, "current_weekly": "600" | null, "gap_pct": "-6.7" | null}`; `current_weekly` = the active lease's rent converted to weekly (weekly as is, fortnightly / 2, monthly x 12 / 52, whole dollars); `gap_pct = (current_weekly - estimate_weekly) / estimate_weekly x 100`, one decimal; null without an active lease or estimate. `dwelling_type_for` maps `apartment` and `condo` to `unit`.
- Card copy (verbatim): title "Market rent"; no-data line "No market data for this area"; incomplete line "Add the property's state and postcode or suburb to see market rent."; failure line "Market data unavailable"; stale line "Market data runs to {period_end}, more than six months before today - treat the comparison as indicative."; gap line "Current rent ${current_weekly}/week, {abs gap}% {below|above} the market median"; VIC attribution "Data: {source.name}, {source.licence}".
- Tasks 1-3 service (subagent), Tasks 4-5 SaaS (subagent), Task 6 rollout (controller).

---

### Task 1: Year-deep series and the trend function

**Files:**
- Modify: `app/rent_suggest/anchor.py` (`_series`, `market_cell` gain `periods`)
- Create: `app/market_rent/__init__.py` (empty), `app/market_rent/estimate.py` (trend only in this task)
- Test: `tests/test_rent_suggest_anchor.py` (one new test), `tests/test_market_rent_estimate.py` (trend tests)

**Interfaces:**
- Consumes: `market_cell`, `SERIES_PERIODS`, `MarketCell` (`app.rent_suggest.anchor`), `RentStatistic` (`app.models`).
- Produces:
  ```python
  # app/rent_suggest/anchor.py
  async def market_cell(session, jurisdiction, area_key, dwelling_type, bedrooms, periods: int = SERIES_PERIODS) -> MarketCell | None
  # app/market_rent/estimate.py
  FETCH_PERIODS = 13; SERIES_PERIODS_OUT = 8
  @dataclass(frozen=True)
  class Trend: from_period: str; from_median: Decimal; change_pct: Decimal
  def year_earlier(period: str) -> str
  def trend(series: list[RentStatistic]) -> Trend | None   # series newest first
  ```

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rent_suggest_anchor.py` (it already imports `Decimal`, `RentStatistic`, `market_cell`; add nothing else):

```python
def _monthly_rows(periods: list[str]) -> list[RentStatistic]:
    return [
        RentStatistic(
            jurisdiction="NSW",
            period=period,
            area_code="2000",
            dwelling_type="unit",
            bedrooms=2,
            median=Decimal(700),
            p25=Decimal(650),
            p75=Decimal(750),
            sample_size=20,
            source_url="u",
        )
        for period in periods
    ]


async def test_market_cell_periods_parameter_controls_series_depth(db_session):
    db_session.add_all(
        _monthly_rows(["2026-07", "2026-06", "2026-05", "2026-04", "2026-03", "2026-02"])
    )
    await db_session.flush()
    default = await market_cell(db_session, "NSW", "2000", "unit", 2)
    deep = await market_cell(db_session, "NSW", "2000", "unit", 2, periods=6)
    assert [r.period for r in default.series] == ["2026-07", "2026-06", "2026-05", "2026-04"]
    assert [r.period for r in deep.series] == [
        "2026-07",
        "2026-06",
        "2026-05",
        "2026-04",
        "2026-03",
        "2026-02",
    ]
```

Create `tests/test_market_rent_estimate.py`:

```python
from decimal import Decimal

from app.market_rent.estimate import Trend, trend, year_earlier
from app.models import RentStatistic


def _row(jurisdiction, period, median, **kw) -> RentStatistic:
    base = dict(
        jurisdiction=jurisdiction,
        period=period,
        area_code="2000" if jurisdiction == "NSW" else "Albert Park-Middle Park-West St Kilda",
        dwelling_type="unit",
        bedrooms=2,
        median=Decimal(median),
        p25=None,
        p75=None,
        sample_size=20,
        source_url="u",
    )
    base.update(kw)
    return RentStatistic(**base)


def test_year_earlier_for_month_and_quarter():
    assert year_earlier("2026-07") == "2025-07"
    assert year_earlier("2025-Q3") == "2024-Q3"


def test_trend_compares_with_the_same_period_a_year_earlier():
    series = [_row("NSW", "2026-07", 760), _row("NSW", "2026-01", 740), _row("NSW", "2025-07", 700)]
    assert trend(series) == Trend("2025-07", Decimal(700), Decimal("8.6"))


def test_trend_handles_a_fall_and_quarters():
    series = [_row("VIC", "2025-Q3", 643), _row("VIC", "2025-Q2", 638), _row("VIC", "2024-Q3", 650)]
    assert trend(series) == Trend("2024-Q3", Decimal(650), Decimal("-1.1"))


def test_trend_is_none_when_the_comparison_period_is_absent():
    assert trend([_row("NSW", "2026-07", 760), _row("NSW", "2026-06", 750)]) is None
    assert trend([]) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_market_rent_estimate.py tests/test_rent_suggest_anchor.py -q`
Expected: `ModuleNotFoundError: No module named 'app.market_rent'` for the new file; the anchor test fails with `TypeError: market_cell() got an unexpected keyword argument 'periods'`.

- [ ] **Step 3: Parameterise the series depth**

In `app/rent_suggest/anchor.py` change `_series` and `market_cell`:

```python
async def _series(session, jurisdiction, area_label, dwelling_type, bedrooms, periods):
    stmt = (
        select(RentStatistic)
        .where(
            RentStatistic.jurisdiction == jurisdiction,
            RentStatistic.area_code == area_label,
            RentStatistic.dwelling_type == dwelling_type,
            RentStatistic.bedrooms.is_(None)
            if bedrooms is None
            else RentStatistic.bedrooms == bedrooms,
        )
        .order_by(RentStatistic.period.desc())
        .limit(periods)
    )
    return list((await session.execute(stmt)).scalars().all())
```

```python
async def market_cell(
    session: AsyncSession,
    jurisdiction: str,
    area_key: str,
    dwelling_type: str,
    bedrooms: int | None,
    periods: int = SERIES_PERIODS,
) -> MarketCell | None:
    """The newest statistics cell for the property, with up to `periods` rows of history."""
    area_label = await resolve_area(session, jurisdiction, area_key)
    if area_label is None:
        return None
    thin: MarketCell | None = None
    for dtype, beds, fallback in _candidates(jurisdiction, dwelling_type, bedrooms):
        if (dtype, beds) == (dwelling_type, bedrooms) and fallback is not None:
            continue
        rows = await _series(session, jurisdiction, area_label, dtype, beds, periods)
        if not rows:
            continue
        newest = rows[0]
        cell = MarketCell(
            period=newest.period,
            median=newest.median,
            p25=newest.p25,
            p75=newest.p75,
            sample_size=newest.sample_size,
            fallback=fallback,
            series=rows,
            area_label=area_label,
        )
        if newest.sample_size >= THIN_SAMPLE:
            return cell
        thin = thin or cell
    return thin
```

(The loop body is unchanged apart from passing `periods`; keep the rest of the file as it is.)

- [ ] **Step 4: Write the trend module**

Create `app/market_rent/__init__.py` (empty) and `app/market_rent/estimate.py`:

```python
"""Market rent estimate: (b)'s market cell read as an estimate with a band and a trend."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.models import RentStatistic

FETCH_PERIODS = 13
SERIES_PERIODS_OUT = 8


@dataclass(frozen=True)
class Trend:
    from_period: str
    from_median: Decimal
    change_pct: Decimal


def year_earlier(period: str) -> str:
    """The same period one year earlier: '2026-07' -> '2025-07', '2025-Q3' -> '2024-Q3'."""
    year, unit = period.split("-")
    return f"{int(year) - 1}-{unit}"


def trend(series: list[RentStatistic]) -> Trend | None:
    """Median change from the period one year before the newest, when the series has it."""
    if not series:
        return None
    newest = series[0]
    target = year_earlier(newest.period)
    earlier = next((row for row in series if row.period == target), None)
    if earlier is None:
        return None
    change = (newest.median - earlier.median) / earlier.median * 100
    return Trend(
        earlier.period, earlier.median, change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    )
```

- [ ] **Step 5: Run the tests, the full suite, ruff, commit**

Run: `uv run pytest tests/test_market_rent_estimate.py tests/test_rent_suggest_anchor.py -q` — Expected: PASS.
Run: `uv run pytest -m "not llm_eval" -q` — Expected: PASS, no regressions.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/rent_suggest/anchor.py app/market_rent tests/test_rent_suggest_anchor.py tests/test_market_rent_estimate.py
git commit -m "Add the market-rent trend and a year-deep series option"
```

---

### Task 2: The estimate composition

**Files:**
- Modify: `app/market_rent/estimate.py`
- Test: `tests/test_market_rent_estimate.py`

**Interfaces:**
- Consumes: `market_cell`, `band_for`, `dollars`, `period_end`, `is_stale`, `MarketCell` (`app.rent_suggest.anchor`); `trend`, `FETCH_PERIODS` (Task 1).
- Produces:
  ```python
  @dataclass(frozen=True)
  class MarketEstimate:
      cell: MarketCell; estimate_weekly: Decimal; band_low: Decimal; band_high: Decimal
      period_end: date; stale: bool; trend: Trend | None
  async def estimate(session, jurisdiction: str, area_key: str, dwelling_type: str, bedrooms: int | None, as_at: date) -> MarketEstimate | None
  ```

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_market_rent_estimate.py` (add `from datetime import date` and extend the import line to `from app.market_rent.estimate import MarketEstimate, Trend, estimate, trend, year_earlier`):

```python
AS_AT = date(2026, 8, 21)


def _nsw_year(area="2000", bedrooms=2, sample=170):
    rows = [
        _row("NSW", "2026-07", 760, p25=Decimal("697.5"), p75=Decimal("886.25"), sample_size=sample)
    ]
    rows += [
        _row("NSW", f"2026-{m:02d}", 750, p25=Decimal(690), p75=Decimal(880), sample_size=sample)
        for m in range(6, 0, -1)
    ]
    rows += [
        _row("NSW", f"2025-{m:02d}", 700, p25=Decimal(650), p75=Decimal(800), sample_size=sample)
        for m in range(12, 6, -1)
    ]
    for r in rows:
        r.area_code = area
        r.bedrooms = bedrooms
    return rows


async def test_nsw_estimate_band_trend_and_series(db_session):
    db_session.add_all(_nsw_year())
    await db_session.flush()
    result = await estimate(db_session, "NSW", "2000", "unit", 2, AS_AT)
    assert isinstance(result, MarketEstimate)
    assert result.estimate_weekly == Decimal(760)
    assert (result.band_low, result.band_high) == (Decimal(698), Decimal(886))
    assert result.period_end == date(2026, 7, 31)
    assert result.stale is False
    assert result.trend == Trend("2025-07", Decimal(700), Decimal("8.6"))
    assert len(result.cell.series) == 13
    assert result.cell.fallback is None


async def test_thin_exact_cell_falls_back_like_the_anchor(db_session):
    db_session.add_all(_nsw_year(bedrooms=2, sample=4) + _nsw_year(bedrooms=None, sample=170))
    await db_session.flush()
    result = await estimate(db_session, "NSW", "2000", "unit", 2, AS_AT)
    assert result.cell.fallback == "bedrooms_all"
    assert result.estimate_weekly == Decimal(760)


async def test_vic_estimate_resolves_the_grouped_label_and_is_stale(db_session):
    db_session.add_all(
        [
            _row("VIC", "2025-Q3", 643, sample_size=144),
            _row("VIC", "2025-Q2", 638, sample_size=140),
            _row("VIC", "2024-Q3", 650, sample_size=150),
        ]
    )
    await db_session.flush()
    result = await estimate(db_session, "VIC", "albert park", "unit", 2, AS_AT)
    assert result.cell.area_label == "Albert Park-Middle Park-West St Kilda"
    assert result.estimate_weekly == Decimal(643)
    assert (result.band_low, result.band_high) == (Decimal(592), Decimal(694))
    assert result.stale is True
    assert result.period_end == date(2025, 9, 30)
    assert result.trend == Trend("2024-Q3", Decimal(650), Decimal("-1.1"))


async def test_unresolvable_area_and_missing_rows_give_none(db_session):
    assert await estimate(db_session, "VIC", "Nowhere", "unit", 2, AS_AT) is None
    assert await estimate(db_session, "NSW", "2999", "unit", 2, AS_AT) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_market_rent_estimate.py -q`
Expected: `ImportError: cannot import name 'MarketEstimate'`.

- [ ] **Step 3: Implement `estimate`**

Add to `app/market_rent/estimate.py` (imports first):

```python
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.rent_suggest.anchor import MarketCell, band_for, dollars, is_stale, market_cell, period_end
```

```python
@dataclass(frozen=True)
class MarketEstimate:
    cell: MarketCell
    estimate_weekly: Decimal
    band_low: Decimal
    band_high: Decimal
    period_end: date
    stale: bool
    trend: Trend | None


async def estimate(
    session: AsyncSession,
    jurisdiction: str,
    area_key: str,
    dwelling_type: str,
    bedrooms: int | None,
    as_at: date,
) -> MarketEstimate | None:
    """The market cell (b) would anchor on, as an estimate; None when there is no data."""
    cell = await market_cell(
        session, jurisdiction, area_key, dwelling_type, bedrooms, periods=FETCH_PERIODS
    )
    if cell is None:
        return None
    low, high = band_for(jurisdiction, cell)
    return MarketEstimate(
        cell=cell,
        estimate_weekly=dollars(cell.median),
        band_low=low,
        band_high=high,
        period_end=period_end(cell.period),
        stale=is_stale(cell.period, as_at),
        trend=trend(cell.series),
    )
```

- [ ] **Step 4: Run the tests, the full suite, ruff, commit**

Run: `uv run pytest tests/test_market_rent_estimate.py -q` — Expected: PASS.
Run: `uv run pytest -m "not llm_eval" -q` — Expected: PASS.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/market_rent/estimate.py tests/test_market_rent_estimate.py
git commit -m "Compose the market rent estimate from the anchored cell"
```

---

### Task 3: Schema, endpoint, README

**Files:**
- Create: `app/schemas/market_rent.py`, `app/routers/market_rent.py`
- Modify: `app/rent_suggest/service.py` (rename `_SOURCES` -> `SOURCES`, its one use), `app/main.py` (register the router)
- Modify: `deploy/README.md` (new "Market rent" section)
- Test: `tests/test_market_rent_api.py`

**Interfaces:**
- Consumes: `estimate`, `MarketEstimate`, `SERIES_PERIODS_OUT` (Tasks 1-2); `SOURCES`, `DISCLAIMER` (`app.rent_suggest.service`); `RentStatPoint` (`app.schemas.rent_statistics`); `SuggestionSource` (`app.schemas.rent_suggestions`); `TenantDep`, `enforce_rate_limit`, `record_usage`, `sydney_today`, `get_session`.
- Produces: `GET /v1/market-rent` with the response shape in Global Constraints.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_market_rent_api.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_market_rent_api.py -q`
Expected: the key-less test passes only if the route exists — expect 404s: `assert 404 == 401` / `assert 404 == 200`.

- [ ] **Step 3: Expose the source table and write the schema**

In `app/rent_suggest/service.py` rename `_SOURCES` to `SOURCES` (definition and its use in `_market()`).

Create `app/schemas/market_rent.py`:

```python
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.rent_statistics import RentStatPoint
from app.schemas.rent_suggestions import SuggestionSource


class MarketBand(BaseModel):
    low: Decimal
    high: Decimal


class MarketTrend(BaseModel):
    from_period: str
    from_median: Decimal
    change_pct: Decimal


class MarketRentResponse(BaseModel):
    jurisdiction: Literal["NSW", "VIC"]
    area: str
    area_label: str | None
    dwelling_type: str
    bedrooms: int | None
    estimate_weekly: Decimal | None
    band: MarketBand | None
    basis: Literal["median"]
    period: str | None
    period_end: date | None
    stale: bool
    sample_size: int | None
    fallback: str | None
    series: list[RentStatPoint]
    trend: MarketTrend | None
    source: SuggestionSource
    disclaimer: str
```

- [ ] **Step 4: Write the router and register it**

Create `app/routers/market_rent.py`:

```python
"""Market rent estimate for a property type in an area, from the official statistics."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TenantDep
from app.core.dates import sydney_today
from app.core.db import get_session
from app.core.ratelimit import enforce_rate_limit
from app.core.usage import record_usage
from app.market_rent.estimate import SERIES_PERIODS_OUT, MarketEstimate, estimate
from app.rent_suggest.service import DISCLAIMER, SOURCES
from app.schemas.market_rent import MarketBand, MarketRentResponse, MarketTrend
from app.schemas.rent_statistics import RentStatPoint

router = APIRouter(prefix="/v1", dependencies=[Depends(enforce_rate_limit)])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _response(jurisdiction, area, dwelling_type, bedrooms, result: MarketEstimate | None):
    common = dict(
        jurisdiction=jurisdiction,
        area=area,
        dwelling_type=dwelling_type,
        bedrooms=bedrooms,
        basis="median",
        source=SOURCES[jurisdiction],
        disclaimer=DISCLAIMER,
    )
    if result is None:
        return MarketRentResponse(
            **common,
            area_label=None,
            estimate_weekly=None,
            band=None,
            period=None,
            period_end=None,
            stale=False,
            sample_size=None,
            fallback=None,
            series=[],
            trend=None,
        )
    cell = result.cell
    return MarketRentResponse(
        **common,
        area_label=cell.area_label,
        estimate_weekly=result.estimate_weekly,
        band=MarketBand(low=result.band_low, high=result.band_high),
        period=cell.period,
        period_end=result.period_end,
        stale=result.stale,
        sample_size=cell.sample_size,
        fallback=cell.fallback,
        series=[
            RentStatPoint(
                period=r.period, median=r.median, p25=r.p25, p75=r.p75, sample_size=r.sample_size
            )
            for r in cell.series[:SERIES_PERIODS_OUT]
        ],
        trend=result.trend
        and MarketTrend(
            from_period=result.trend.from_period,
            from_median=result.trend.from_median,
            change_pct=result.trend.change_pct,
        ),
    )


@router.get("/market-rent", response_model=MarketRentResponse)
async def get_market_rent(
    tenant: TenantDep,
    session: SessionDep,
    jurisdiction: Literal["NSW", "VIC"],
    area: str,
    dwelling_type: str,
    bedrooms: Annotated[int | None, Query(ge=0)] = None,
    as_at: date | None = None,
) -> MarketRentResponse:
    result = await estimate(
        session, jurisdiction, area, dwelling_type, bedrooms, as_at or sydney_today()
    )
    await record_usage(session, tenant.tenant_id, "market_rent")
    await session.commit()
    return _response(jurisdiction, area, dwelling_type, bedrooms, result)
```

In `app/main.py` add `from app.routers.market_rent import router as market_rent_router` beside the other router imports and `app.include_router(market_rent_router)` beside `rent_statistics_router`.

- [ ] **Step 5: Run the tests, the full suite, ruff**

Run: `uv run pytest tests/test_market_rent_api.py -q` — Expected: PASS.
Run: `uv run pytest -m "not llm_eval" -q` — Expected: PASS.

- [ ] **Step 6: README section**

Insert into `deploy/README.md` after the "Rent suggestions" section and before "## Backups":

```markdown
## Market rent

`GET /v1/market-rent` returns a deterministic market-rent estimate for a
property type in an area: the median of the same statistics cell the
rent-suggestion endpoint anchors on (same thin-sample fallback chain,
same VIC label resolution), the band (NSW p25-p75, VIC +-8%), the newest
eight periods, a trend against the same period a year earlier, and the
staleness flag. No model call, so no failover and no eval gate; calls
are recorded under the usage class `market_rent`. Missing data is a 200
with null estimate fields, never a 404. Any UI showing a VIC result must
render the Homes Victoria CC BY 4.0 attribution (`source.licence`).
```

- [ ] **Step 7: Ruff, commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/schemas/market_rent.py app/routers/market_rent.py app/rent_suggest/service.py app/main.py deploy/README.md tests/test_market_rent_api.py
git commit -m "Serve market rent estimates from a tenant endpoint"
```

---

### Task 4: SaaS proxy with the current-rent gap

**Files (SaaS repo `/Users/keithho/LLMProjects/rental_management_app`):**
- Modify: `backend/app/services/compliance.py` (`dwelling_type_for` mapping, `area_key_for`, `market_rent_params`, `get_market_rent`)
- Create: `backend/app/services/rent_math.py`, `backend/app/routers/market_rent.py`
- Modify: `backend/app/main.py` (register)
- Test: `backend/tests/test_compliance_mapper.py` (mapping), `backend/tests/test_rent_math.py`, `backend/tests/test_market_rent_endpoint.py`

**Interfaces:**
- Consumes: `get_owned_property`, `active_leases_by_property` (`app.routers.properties`), `property_jurisdiction` + `JurisdictionUnresolved` (`app.services.jurisdiction`), `require_roles`, `compliance.enabled()`, `_headers()`, `settings.compliance_api_url`, `LeaseFrequency`, `PropertyType`.
- Produces: `GET /api/v1/properties/{property_id}/market-rent` -> `{"market": <service body>, "current_weekly": str | null, "gap_pct": str | null}`; `weekly_rent(amount: Decimal, frequency: LeaseFrequency) -> Decimal`; `dwelling_type_for(PropertyType) -> str` with apartment/condo -> unit.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_compliance_mapper.py`, find the existing `dwelling_type_for` tests and change the apartment/condo expectations to `"unit"`; add if absent:

```python
@pytest.mark.parametrize(
    ("ptype", "expected"),
    [
        (PropertyType.apartment, "unit"),
        (PropertyType.condo, "unit"),
        (PropertyType.house, "house"),
        (PropertyType.townhouse, "townhouse"),
        (PropertyType.other, "other"),
    ],
)
def test_dwelling_type_for_maps_flats_to_unit(ptype, expected):
    assert compliance.dwelling_type_for(ptype) == expected
```

Create `backend/tests/test_rent_math.py`:

```python
from decimal import Decimal

from app.models import LeaseFrequency
from app.services.rent_math import weekly_rent


def test_weekly_rent_conversions_round_to_whole_dollars():
    assert weekly_rent(Decimal(600), LeaseFrequency.weekly) == Decimal(600)
    assert weekly_rent(Decimal(1200), LeaseFrequency.fortnightly) == Decimal(600)
    assert weekly_rent(Decimal(1500), LeaseFrequency.monthly) == Decimal(346)
    assert weekly_rent(Decimal(2610), LeaseFrequency.monthly) == Decimal(602)
```

Create `backend/tests/test_market_rent_endpoint.py`:

```python
import uuid

import httpx

from app.services import compliance
from tests.test_leases import lease_body
from tests.test_properties_crud import landlord_headers

SERVICE_BODY = {
    "jurisdiction": "NSW",
    "area": "2000",
    "area_label": "2000",
    "dwelling_type": "unit",
    "bedrooms": 2,
    "estimate_weekly": "760",
    "band": {"low": "698", "high": "886"},
    "basis": "median",
    "period": "2026-07",
    "period_end": "2026-07-31",
    "stale": False,
    "sample_size": 170,
    "fallback": None,
    "series": [],
    "trend": {"from_period": "2025-07", "from_median": "700.00", "change_pct": "8.6"},
    "source": {
        "name": "NSW Fair Trading rental bond lodgements",
        "url": "https://www.nsw.gov.au/housing-and-construction/rental-forms-surveys-and-data/rental-bond-data",
        "licence": "NSW Government open data (terms on the source page)",
    },
    "disclaimer": "General information, not legal advice.",
}

EMPTY_BODY = {
    **SERVICE_BODY,
    "area_label": None,
    "estimate_weekly": None,
    "band": None,
    "period": None,
    "period_end": None,
    "sample_size": None,
    "trend": None,
}


async def _property(
    client,
    email,
    state="NSW",
    postcode="2000",
    city=None,
    ptype="apartment",
    bedrooms=2,
    with_lease=True,
):
    headers = await landlord_headers(client, email)
    prop = (
        await client.post(
            "/api/v1/properties",
            json={
                "address": "7 Market St",
                "state": state,
                "postcode": postcode,
                "city": city,
                "type": ptype,
                "bedrooms": bedrooms,
            },
            headers=headers,
        )
    ).json()
    if with_lease:
        await client.post(
            f"/api/v1/properties/{prop['id']}/leases",
            json=lease_body(start_date="2026-01-01", end_date="2027-12-31"),
            headers=headers,
        )
    return headers, uuid.UUID(prop["id"])


def _fake_get(monkeypatch, captured, response=None, raises=None):
    async def _fake(params):
        captured.append(params)
        if raises is not None:
            raise raises
        return dict(response or SERVICE_BODY)

    monkeypatch.setattr("app.services.compliance.get_market_rent", _fake)
    monkeypatch.setattr(compliance.settings, "compliance_api_url", "http://service")
    monkeypatch.setattr(compliance.settings, "compliance_api_key", "k")


async def test_requires_auth(client, db_session):
    _, property_id = await _property(client, "mr401@example.com")
    response = await client.get(f"/api/v1/properties/{property_id}/market-rent")
    assert response.status_code == 401


async def test_missing_postcode_is_422(client, db_session, monkeypatch):
    _fake_get(monkeypatch, [])
    headers, property_id = await _property(client, "mrnopc@example.com", postcode=None)
    response = await client.get(f"/api/v1/properties/{property_id}/market-rent", headers=headers)
    assert response.status_code == 422


async def test_returns_market_with_current_rent_gap(client, db_session, monkeypatch):
    captured = []
    _fake_get(monkeypatch, captured)
    headers, property_id = await _property(client, "mr200@example.com")
    response = await client.get(f"/api/v1/properties/{property_id}/market-rent", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["market"] == SERVICE_BODY
    assert body["current_weekly"] == "346"
    assert body["gap_pct"] == "-54.5"
    assert captured == [
        {"jurisdiction": "NSW", "area": "2000", "dwelling_type": "unit", "bedrooms": 2}
    ]


async def test_vacant_property_has_null_gap(client, db_session, monkeypatch):
    _fake_get(monkeypatch, [])
    headers, property_id = await _property(client, "mrvacant@example.com", with_lease=False)
    body = (
        await client.get(f"/api/v1/properties/{property_id}/market-rent", headers=headers)
    ).json()
    assert body["current_weekly"] is None and body["gap_pct"] is None


async def test_no_estimate_has_null_gap(client, db_session, monkeypatch):
    _fake_get(monkeypatch, [], response=EMPTY_BODY)
    headers, property_id = await _property(client, "mrempty@example.com")
    body = (
        await client.get(f"/api/v1/properties/{property_id}/market-rent", headers=headers)
    ).json()
    assert body["current_weekly"] == "346" and body["gap_pct"] is None


async def test_vic_uses_city_and_unresolved_state_is_422(client, db_session, monkeypatch):
    captured = []
    _fake_get(monkeypatch, captured)
    headers, property_id = await _property(
        client, "mrvic@example.com", state="VIC", postcode=None, city="Albert Park"
    )
    await client.get(f"/api/v1/properties/{property_id}/market-rent", headers=headers)
    assert captured[0]["area"] == "Albert Park" and captured[0]["jurisdiction"] == "VIC"
    headers, property_id = await _property(client, "mrnostate@example.com", state=None)
    response = await client.get(f"/api/v1/properties/{property_id}/market-rent", headers=headers)
    assert response.status_code == 422


async def test_service_error_is_502_and_disabled_is_503(client, db_session, monkeypatch):
    _fake_get(monkeypatch, [], raises=httpx.ConnectError("down"))
    headers, property_id = await _property(client, "mr502@example.com")
    assert (
        await client.get(f"/api/v1/properties/{property_id}/market-rent", headers=headers)
    ).status_code == 502
    monkeypatch.setattr(compliance.settings, "compliance_api_url", "")
    assert (
        await client.get(f"/api/v1/properties/{property_id}/market-rent", headers=headers)
    ).status_code == 503
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `backend/`): `uv run pytest tests/test_compliance_mapper.py tests/test_rent_math.py tests/test_market_rent_endpoint.py -q`
Expected: mapper assertions fail with `'other' != 'unit'`; `ModuleNotFoundError: app.services.rent_math`; endpoint tests 404.

- [ ] **Step 3: Mapping, rent math, client**

In `backend/app/services/compliance.py` replace `_SUGGESTION_DWELLING_TYPES` and `dwelling_type_for` with:

```python
_DWELLING_TYPES = {
    PropertyType.apartment: "unit",
    PropertyType.condo: "unit",
    PropertyType.house: "house",
    PropertyType.townhouse: "townhouse",
    PropertyType.other: "other",
}


def dwelling_type_for(property_type: PropertyType) -> str:
    """The compliance service's dwelling_type for a property type; flats are units."""
    return _DWELLING_TYPES[property_type]


def area_key_for(property_row: Property, jurisdiction: str) -> str:
    """The jurisdiction-native area key: VIC reports by suburb, NSW by postcode."""
    return (property_row.city if jurisdiction == "VIC" else property_row.postcode) or ""
```

Make `rent_suggestion_payload` use `area_key_for(property_row, jurisdiction)` instead of its inline expression (keep its docstring). Add:

```python
def market_rent_params(property_row: Property, jurisdiction: str) -> dict:
    """Query parameters for the compliance market-rent endpoint."""
    return {
        "jurisdiction": jurisdiction,
        "area": area_key_for(property_row, jurisdiction),
        "dwelling_type": dwelling_type_for(property_row.type),
        "bedrooms": property_row.bedrooms,
    }


async def get_market_rent(params: dict) -> dict:
    """GET the compliance market-rent estimate and return its body."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.get(
            f"{settings.compliance_api_url}/v1/market-rent", params=params, headers=_headers()
        )
        response.raise_for_status()
        return response.json()
```

Create `backend/app/services/rent_math.py`:

```python
"""Rent arithmetic shared by the rent features."""

from decimal import ROUND_HALF_UP, Decimal

from app.models import LeaseFrequency

_WEEKS_PER_PERIOD = {
    LeaseFrequency.weekly: Decimal(1),
    LeaseFrequency.fortnightly: Decimal(2),
    LeaseFrequency.monthly: Decimal(52) / Decimal(12),
}


def weekly_rent(amount: Decimal, frequency: LeaseFrequency) -> Decimal:
    """A rent amount per period as whole weekly dollars."""
    return (Decimal(amount) / _WEEKS_PER_PERIOD[frequency]).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    )


def gap_pct(current_weekly: Decimal, estimate_weekly: Decimal) -> Decimal:
    """How far the current rent sits from the estimate, in percent, one decimal."""
    change = (current_weekly - estimate_weekly) / estimate_weekly * 100
    return change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
```

- [ ] **Step 4: The proxy router**

Create `backend/app/routers/market_rent.py`:

```python
"""Proxy the compliance market-rent estimate for a property, with the current-rent gap."""

import logging
import uuid
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_roles
from app.models import Membership, Role
from app.routers.properties import active_leases_by_property, get_owned_property
from app.services import compliance
from app.services.jurisdiction import property_jurisdiction
from app.services.rent_math import gap_pct, weekly_rent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["market-rent"])

manager = require_roles(Role.landlord, Role.property_manager)


@router.get("/properties/{property_id}/market-rent")
async def get_market_rent(
    property_id: uuid.UUID,
    membership: Membership = Depends(manager),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """The compliance estimate for the property plus the active lease's weekly rent and gap."""
    if not compliance.enabled():
        raise HTTPException(status_code=503, detail="Compliance integration is not configured")
    prop = await get_owned_property(property_id, membership, session)
    jurisdiction, reason = await property_jurisdiction(session, prop.id)
    if jurisdiction is None:
        raise HTTPException(status_code=422, detail=f"Property state unresolved: {reason}")
    params = compliance.market_rent_params(prop, jurisdiction)
    if not params["area"]:
        raise HTTPException(status_code=422, detail="Property postcode or suburb missing")
    try:
        market = await compliance.get_market_rent(params)
    except httpx.HTTPError as exc:
        logger.warning("Market rent failed for property %s: %s", property_id, exc)
        raise HTTPException(status_code=502, detail={"code": "market_unavailable"}) from exc
    lease = (await active_leases_by_property(session, membership.organization_id, [prop.id])).get(
        prop.id
    )
    current = weekly_rent(Decimal(lease.rent_amount), lease.rent_frequency) if lease else None
    estimate = market.get("estimate_weekly")
    gap = (
        gap_pct(current, Decimal(estimate))
        if current is not None and estimate is not None
        else None
    )
    return JSONResponse(
        content={
            "market": market,
            "current_weekly": None if current is None else str(current),
            "gap_pct": None if gap is None else str(gap),
        }
    )
```

Register in `backend/app/main.py`: `from app.routers.market_rent import router as market_rent_router` and `app.include_router(market_rent_router)` beside `rent_suggestions_router`.

- [ ] **Step 5: Run the tests, the full suite, ruff, commit**

Run (from `backend/`): `uv run pytest tests/test_compliance_mapper.py tests/test_rent_math.py tests/test_market_rent_endpoint.py tests/test_rent_suggestion_endpoint.py -q` — Expected: PASS (the rent-suggestion tests that assert the captured payload's `dwelling_type` for an apartment property now expect `unit`; update those assertions in `tests/test_rent_suggestion_endpoint.py` if they encoded `other`).
Run: `uv run pytest -q` — Expected: PASS.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add backend/app/services/compliance.py backend/app/services/rent_math.py backend/app/routers/market_rent.py backend/app/main.py backend/tests/test_compliance_mapper.py backend/tests/test_rent_math.py backend/tests/test_market_rent_endpoint.py backend/tests/test_rent_suggestion_endpoint.py
git commit -m "Proxy market rent estimates with the current-rent gap"
```

---

### Task 5: The property-page card and e2e

**Files (SaaS repo):**
- Create: `frontend/src/lib/marketRent.ts`, `frontend/src/app/app/properties/[id]/MarketRentCard.tsx`, `frontend/e2e/market-rent.spec.ts`
- Modify: `frontend/src/app/app/properties/[id]/page.tsx`

**Interfaces:**
- Consumes: the Task 4 envelope; `apiFetch`, `ApiError` (`@/lib/api`, `ApiError.status`); `Card`, `Badge` (`@/components/ui`); the page's existing `prop`/`useEffect` pattern.
- Produces: `getMarketRent(propertyId): Promise<MarketRentEnvelope>`; `<MarketRentCard state={...} data={...} />`.

- [ ] **Step 1: Types and client**

Create `frontend/src/lib/marketRent.ts`:

```ts
import { apiFetch } from "@/lib/api";
import type { SuggestionSource } from "@/lib/rentSuggestion";

export interface MarketSeriesPoint {
  period: string;
  median: string;
  p25: string | null;
  p75: string | null;
  sample_size: number;
}

export interface MarketTrend {
  from_period: string;
  from_median: string;
  change_pct: string;
}

export interface MarketRent {
  jurisdiction: "NSW" | "VIC";
  area: string;
  area_label: string | null;
  dwelling_type: string;
  bedrooms: number | null;
  estimate_weekly: string | null;
  band: { low: string; high: string } | null;
  basis: "median";
  period: string | null;
  period_end: string | null;
  stale: boolean;
  sample_size: number | null;
  fallback: string | null;
  series: MarketSeriesPoint[];
  trend: MarketTrend | null;
  source: SuggestionSource;
  disclaimer: string;
}

export interface MarketRentEnvelope {
  market: MarketRent;
  current_weekly: string | null;
  gap_pct: string | null;
}

export function getMarketRent(propertyId: string): Promise<MarketRentEnvelope> {
  return apiFetch<MarketRentEnvelope>(`/api/v1/properties/${propertyId}/market-rent`);
}
```

- [ ] **Step 2: The card**

Create `frontend/src/app/app/properties/[id]/MarketRentCard.tsx`:

```tsx
"use client";

import { Card } from "@/components/ui";
import type { MarketRent, MarketRentEnvelope } from "@/lib/marketRent";

export type MarketRentState = "loading" | "ok" | "incomplete" | "unavailable";

const FALLBACK_LABELS: Record<string, string> = {
  bedrooms_all: "all bedrooms",
  dwelling_all: "all dwelling types",
};

function cellLine(market: MarketRent): string {
  const beds = market.bedrooms === null ? "" : `, ${market.bedrooms} bedrooms`;
  const fallback = market.fallback ? ` (${FALLBACK_LABELS[market.fallback] ?? market.fallback})` : "";
  return `${market.dwelling_type}${beds} - ${market.area_label}${fallback}`;
}

function gapLine(currentWeekly: string, gapPct: string): string {
  const gap = Number(gapPct);
  const direction = gap < 0 ? "below" : "above";
  return `Current rent $${currentWeekly}/week, ${Math.abs(gap).toFixed(1)}% ${direction} the market median`;
}

export function MarketRentCard({ state, data }: { state: MarketRentState; data: MarketRentEnvelope | null }) {
  if (state === "loading") {
    return (
      <Card title="Market rent" className="mb-5">
        <p className="text-sm text-muted">Loading market data...</p>
      </Card>
    );
  }
  if (state === "incomplete") {
    return (
      <Card title="Market rent" className="mb-5">
        <p className="text-sm text-muted">
          Add the property&apos;s state and postcode or suburb to see market rent.
        </p>
      </Card>
    );
  }
  if (state === "unavailable" || !data) {
    return (
      <Card title="Market rent" className="mb-5">
        <p className="text-sm text-muted">Market data unavailable</p>
      </Card>
    );
  }
  const market = data.market;
  if (market.estimate_weekly === null || market.band === null) {
    return (
      <Card title="Market rent" className="mb-5">
        <p className="text-sm text-muted">No market data for this area</p>
        <p className="mt-2 text-xs text-muted">{market.disclaimer}</p>
      </Card>
    );
  }
  return (
    <Card title="Market rent" className="mb-5">
      <p className="text-2xl font-semibold text-text" data-testid="market-estimate">
        ${market.estimate_weekly}
        <span className="text-sm font-normal text-muted"> / week</span>
      </p>
      <p className="text-sm text-muted">
        Band ${market.band.low} to ${market.band.high} ({market.basis} of {cellLine(market)})
      </p>
      <p className="mt-2 text-sm text-text">
        {market.period}: n={market.sample_size}
        {market.trend && ` · ${market.trend.change_pct}% vs ${market.trend.from_period}`}
      </p>
      {market.stale && (
        <p className="mt-2 text-sm text-warning">
          Market data runs to {market.period_end}, more than six months before today - treat the
          comparison as indicative.
        </p>
      )}
      {data.current_weekly !== null && data.gap_pct !== null && (
        <p className="mt-2 text-sm text-text" data-testid="market-gap">
          {gapLine(data.current_weekly, data.gap_pct)}
        </p>
      )}
      <p className="mt-3 text-xs text-muted">
        Data: {market.source.name}
        {market.source.licence === "CC BY 4.0" && `, ${market.source.licence}`}
      </p>
      <p className="mt-1 text-xs text-muted">{market.disclaimer}</p>
    </Card>
  );
}
```

(`text-warning`/`text-muted`/`text-text` are the tokens the renew card uses; `<Card title=... className=...>` is how the property page already renders its cards.)

- [ ] **Step 3: Load it on the property page**

In `frontend/src/app/app/properties/[id]/page.tsx`: import `{ ApiError } from "@/lib/api"`, `{ getMarketRent, type MarketRentEnvelope } from "@/lib/marketRent"`, `{ MarketRentCard, type MarketRentState } from "./MarketRentCard"`; add state:

```tsx
const [marketRent, setMarketRent] = useState<MarketRentEnvelope | null>(null);
const [marketState, setMarketState] = useState<MarketRentState>("loading");
```

Inside the existing `useEffect`, after `setProp(p)` and independent of the lease branch:

```tsx
getMarketRent(id)
  .then((m) => {
    if (!active) return;
    setMarketRent(m);
    setMarketState("ok");
  })
  .catch((err) => {
    if (!active) return;
    setMarketState(err instanceof ApiError && err.status === 422 ? "incomplete" : "unavailable");
  });
```

Render `<MarketRentCard state={marketState} data={marketRent} />` directly after the "Tenancy" card.

- [ ] **Step 4: e2e**

Create `frontend/e2e/market-rent.spec.ts`:

```ts
import { expect, test } from "@playwright/test";

const LIVE = !!process.env.MARKET_RENT_E2E;

function isoDate(offsetDays: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}

const MARKET = {
  jurisdiction: "NSW",
  area: "2000",
  area_label: "2000",
  dwelling_type: "unit",
  bedrooms: 2,
  estimate_weekly: "760",
  band: { low: "698", high: "886" },
  basis: "median",
  period: "2026-07",
  period_end: "2026-07-31",
  stale: false,
  sample_size: 170,
  fallback: null,
  series: [],
  trend: { from_period: "2025-07", from_median: "700.00", change_pct: "8.6" },
  source: {
    name: "NSW Fair Trading rental bond lodgements",
    url: "https://www.nsw.gov.au/housing-and-construction/rental-forms-surveys-and-data/rental-bond-data",
    licence: "NSW Government open data (terms on the source page)",
  },
  disclaimer: "General information, not legal advice.",
};

const ENVELOPE = { market: MARKET, current_weekly: "346", gap_pct: "-54.5" };
const NO_DATA = {
  market: { ...MARKET, area_label: null, estimate_weekly: null, band: null, period: null, period_end: null, sample_size: null, trend: null },
  current_weekly: "346",
  gap_pct: null,
};

/** Signs up a fresh landlord, creates an NSW property (postcode 2000) and a monthly $1500 lease,
 * and returns the property page URL. */
async function createProperty(page: import("@playwright/test").Page): Promise<string> {
  await page.goto("/signup");
  await page.getByPlaceholder("Your name").fill("Market Owner");
  await page.getByPlaceholder("Organization name").fill("Market Org");
  await page.getByPlaceholder("Email").fill(`market-${Date.now()}@example.com`);
  await page.getByPlaceholder("Password (min 8 chars)").fill("secret123");
  await page.getByRole("button", { name: "Sign up" }).click();
  await expect(page.getByTestId("welcome")).toBeVisible();

  await page.goto("/app/properties/new");
  await page.getByPlaceholder("Address", { exact: true }).fill("9 Market Way");
  await page.getByLabel("State").selectOption("NSW");
  await page.getByPlaceholder("Postcode").fill("2000");
  await page.getByRole("button", { name: "Create property" }).click();
  await expect(page).toHaveURL(/\/app\/properties$/);

  await page.goto("/app/leases/new");
  await page.getByLabel("Property").selectOption({ label: "9 Market Way (vacant)" });
  await page.getByPlaceholder("Tenant name").fill("Mia Market");
  await page.getByPlaceholder("Tenant email").fill(`tenant-${Date.now()}@example.com`);
  await page.getByLabel("Rent", { exact: true }).fill("1500");
  await page.getByLabel("Start").fill(isoDate(-1));
  await page.getByLabel("End").fill(isoDate(300));
  await page.getByRole("button", { name: "Add lease" }).click();
  await expect(page).toHaveURL(/\/app\/leases$/);

  await page.goto("/app/properties");
  await page.getByRole("link", { name: "9 Market Way" }).click();
  await expect(page).toHaveURL(/\/app\/properties\/[0-9a-f-]+$/);
  return page.url();
}

test("property page shows the market estimate and the current-rent gap", async ({ page }) => {
  await page.route("**/market-rent", (route) => route.fulfill({ json: ENVELOPE }));
  await createProperty(page);
  await expect(page.getByTestId("market-estimate")).toContainText("$760");
  await expect(page.getByText("Band $698 to $886", { exact: false })).toBeVisible();
  await expect(page.getByText("8.6% vs 2025-07", { exact: false })).toBeVisible();
  await expect(page.getByTestId("market-gap")).toHaveText(
    "Current rent $346/week, 54.5% below the market median",
  );
});

test("property page says when there is no market data", async ({ page }) => {
  await page.route("**/market-rent", (route) => route.fulfill({ json: NO_DATA }));
  await createProperty(page);
  await expect(page.getByText("No market data for this area")).toBeVisible();
  await expect(page.getByTestId("market-gap")).toHaveCount(0);
});

test("live: the real compliance service estimates postcode 2000", async ({ page }) => {
  test.skip(!LIVE, "set MARKET_RENT_E2E=1 to hit the real service");
  await createProperty(page);
  await expect(page.getByTestId("market-estimate")).toContainText("$");
  await expect(page.getByTestId("market-gap")).toContainText("the market median");
});
```

(The signup, property and lease steps mirror `rent-suggestion.spec.ts`; the properties list renders each address through `formatAddress`, and `getByRole("link", { name })` matches by substring, so the address alone finds the link.)

- [ ] **Step 5: Run lint, tsc, e2e, commit**

Run (from `frontend/`): `npm run lint`, `npx tsc --noEmit`, `npx playwright test e2e/market-rent.spec.ts` — Expected: clean; 2 passed, 1 skipped.

```bash
git add frontend/src/lib/marketRent.ts "frontend/src/app/app/properties/[id]/MarketRentCard.tsx" "frontend/src/app/app/properties/[id]/page.tsx" frontend/e2e/market-rent.spec.ts
git commit -m "Show market rent on the property page"
```

---

### Task 6: Rollout (controller-run)

- [ ] **Step 1: Deploy the service** at the Task 3 head (`./deploy/deploy.sh sha-<short>` with `LEASE_DEPLOY_SERVER`/`LEASE_DEPLOY_DOMAIN` exported), verify `/health` and `docker inspect` shows the tag.
- [ ] **Step 2: Production smoke**: `GET /v1/market-rent` with the SaaS tenant key for `NSW/2000/unit/2` (expect estimate near the published 2026-07 median, band, series 8, trend vs 2025-07) and `VIC/albert park/unit/2` (expect the grouped label, stale true, trend vs 2024-Q3, licence CC BY 4.0); one unknown area (200 empty shape).
- [ ] **Step 3: SaaS** local dev: run the e2e with `MARKET_RENT_E2E=1` once against production; confirm the AI disclosure page is unchanged.
- [ ] **Step 4: Ledger + memory**; final whole-branch review across both repos (opus) with the two review packages.
