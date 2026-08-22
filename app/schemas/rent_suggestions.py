from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.rules.base import Finding
from app.schemas.lease import LeaseInput


class SuggestionProperty(BaseModel):
    area_key: str
    dwelling_type: Literal["house", "unit", "townhouse", "other"]
    bedrooms: int | None = None


class RentSuggestionRequest(BaseModel):
    jurisdiction: Literal["NSW", "VIC"]
    as_at: date | None = None
    property: SuggestionProperty
    lease: LeaseInput
    renewal_start: date


class SuggestionRange(BaseModel):
    low: Decimal
    high: Decimal


class SuggestionSource(BaseModel):
    name: str
    url: str
    licence: str


class SuggestionMarket(BaseModel):
    period: str
    period_end: date
    stale: bool
    median: Decimal
    p25: Decimal | None
    p75: Decimal | None
    sample_size: int
    fallback: str | None
    area_label: str
    source: SuggestionSource


class RentSuggestionResponse(BaseModel):
    current_weekly: Decimal
    suggested_weekly: Decimal
    range: SuggestionRange
    market_gap: Literal["within", "above_cap", "below_current", "no_data"]
    market: SuggestionMarket | None
    law_card: list[Finding]
    law_blocked: bool
    reasoning: str
    model: str | None
    engine_version: str
    disclaimer: str
