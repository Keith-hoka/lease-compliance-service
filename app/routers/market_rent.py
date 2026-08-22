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
    common = {
        "jurisdiction": jurisdiction,
        "area": area,
        "dwelling_type": dwelling_type,
        "bedrooms": bedrooms,
        "basis": "median",
        "source": SOURCES[jurisdiction],
        "disclaimer": DISCLAIMER,
    }
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
