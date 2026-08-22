"""Market rent estimate: (b)'s market cell read as an estimate with a band and a trend."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RentStatistic
from app.rent_suggest.anchor import (
    MarketCell,
    band_for,
    dollars,
    is_stale,
    market_cell,
    period_end,
)

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
