from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class RentStatPoint(BaseModel):
    period: str
    median: Decimal
    p25: Decimal | None
    p75: Decimal | None
    sample_size: int


class RentStatSource(BaseModel):
    name: str
    url: str
    licence: str
    fetched_at: datetime | None


class RentStatisticsResponse(BaseModel):
    jurisdiction: Literal["NSW", "VIC"]
    area: str
    area_label: str | None
    dwelling_type: str
    bedrooms: int | None
    series: list[RentStatPoint]
    source: RentStatSource
