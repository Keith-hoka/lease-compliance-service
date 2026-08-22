"""Deterministic market anchoring: a suggestion range from statistics and a cap."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RentStatistic
from app.rent_stats.areas import resolve_area

CAP_RATIO = Decimal("1.15")
VIC_BAND = Decimal("0.08")
THIN_SAMPLE = 10
SERIES_PERIODS = 4


@dataclass(frozen=True)
class MarketCell:
    period: str
    median: Decimal
    p25: Decimal | None
    p75: Decimal | None
    sample_size: int
    fallback: str | None
    series: list[RentStatistic]
    area_label: str


@dataclass(frozen=True)
class Anchor:
    current_weekly: Decimal
    low: Decimal
    high: Decimal
    gap: str
    market: MarketCell | None


def dollars(value: Decimal) -> Decimal:
    return value.quantize(Decimal(1), rounding=ROUND_HALF_UP)


async def _series(session, jurisdiction, area_label, dwelling_type, bedrooms):
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
        .limit(SERIES_PERIODS)
    )
    return list((await session.execute(stmt)).scalars().all())


def _candidates(jurisdiction, dwelling_type, bedrooms):
    """(dwelling_type, bedrooms, fallback label) in preference order."""
    exact = [(dwelling_type, bedrooms, None)]
    if jurisdiction == "NSW":
        return exact + [(dwelling_type, None, "bedrooms_all"), ("all", None, "dwelling_all")]
    return exact + [("all", None, "dwelling_all")]


async def market_cell(
    session: AsyncSession,
    jurisdiction: str,
    area_key: str,
    dwelling_type: str,
    bedrooms: int | None,
) -> MarketCell | None:
    area_label = await resolve_area(session, jurisdiction, area_key)
    if area_label is None:
        return None
    thin: MarketCell | None = None
    for dtype, beds, fallback in _candidates(jurisdiction, dwelling_type, bedrooms):
        if (dtype, beds) == (dwelling_type, bedrooms) and fallback is not None:
            continue
        rows = await _series(session, jurisdiction, area_label, dtype, beds)
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


def band_for(jurisdiction: str, cell: MarketCell) -> tuple[Decimal, Decimal]:
    if jurisdiction == "NSW" and cell.p25 is not None and cell.p75 is not None:
        return dollars(cell.p25), dollars(cell.p75)
    return dollars(cell.median * (1 - VIC_BAND)), dollars(cell.median * (1 + VIC_BAND))


def anchor(current_weekly: Decimal, jurisdiction: str, cell: MarketCell | None) -> Anchor:
    current = dollars(current_weekly)
    cap_high = dollars(current * CAP_RATIO)
    if cell is None:
        return Anchor(current, current, cap_high, "no_data", None)
    market_low, market_high = band_for(jurisdiction, cell)
    if market_low > cap_high:
        return Anchor(current, current, cap_high, "above_cap", cell)
    if market_high < current:
        return Anchor(current, current, current, "below_current", cell)
    return Anchor(current, max(current, market_low), min(cap_high, market_high), "within", cell)
