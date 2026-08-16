"""Point-in-time rent statistics from the official bond datasets."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TenantDep
from app.core.db import get_session
from app.core.ratelimit import enforce_rate_limit
from app.core.usage import record_usage
from app.models import RentStatistic
from app.rent_stats.loader import NSW_SOURCE_NAME, VIC_SOURCE_NAME
from app.schemas.rent_statistics import RentStatisticsResponse, RentStatPoint, RentStatSource

router = APIRouter(prefix="/v1", dependencies=[Depends(enforce_rate_limit)])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

_SOURCES = {
    "NSW": (
        NSW_SOURCE_NAME,
        "https://www.nsw.gov.au/housing-and-construction/rental-forms-surveys-and-data/rental-bond-data",
        "NSW Government open data (terms on the source page)",
    ),
    "VIC": (VIC_SOURCE_NAME, "https://www.dffh.vic.gov.au/publications/rental-report", "CC BY 4.0"),
}


@router.get("/rent-statistics", response_model=RentStatisticsResponse)
async def get_rent_statistics(
    tenant: TenantDep,
    session: SessionDep,
    jurisdiction: Literal["NSW", "VIC"],
    area: str,
    dwelling_type: str,
    bedrooms: int | None = None,
    periods: Annotated[int, Query(ge=1, le=40)] = 8,
) -> RentStatisticsResponse:
    stmt = (
        select(RentStatistic)
        .where(
            RentStatistic.jurisdiction == jurisdiction,
            RentStatistic.area_code == area,
            RentStatistic.dwelling_type == dwelling_type,
            RentStatistic.bedrooms.is_(None)
            if bedrooms is None
            else RentStatistic.bedrooms == bedrooms,
        )
        .order_by(RentStatistic.period.desc())
        .limit(periods)
    )
    rows = (await session.execute(stmt)).scalars().all()
    await record_usage(session, tenant.tenant_id, "rent_statistics")
    await session.commit()
    name, url, licence = _SOURCES[jurisdiction]
    return RentStatisticsResponse(
        jurisdiction=jurisdiction,
        area=area,
        dwelling_type=dwelling_type,
        bedrooms=bedrooms,
        series=[
            RentStatPoint(
                period=r.period, median=r.median, p25=r.p25, p75=r.p75, sample_size=r.sample_size
            )
            for r in rows
        ],
        source=RentStatSource(
            name=name, url=url, licence=licence, fetched_at=rows[0].fetched_at if rows else None
        ),
    )
