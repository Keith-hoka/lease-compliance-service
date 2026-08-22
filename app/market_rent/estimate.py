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
