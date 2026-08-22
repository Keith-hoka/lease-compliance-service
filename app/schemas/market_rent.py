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
