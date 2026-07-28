import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.lease import LeaseInput

CheckResult = tuple[Literal["red", "green"], str, dict]


@dataclass(frozen=True)
class SectionRef:
    act_slug: str
    section_no: str


class Citation(BaseModel):
    act: str
    section_no: str
    as_at: date
    section_id: uuid.UUID


class Finding(BaseModel):
    rule_id: str
    verdict: Literal["red", "green", "yellow", "skipped"]
    summary: str
    evidence: dict = {}
    citations: list[Citation] = []
    skip_reason: str | None = None


@dataclass(frozen=True)
class Rule:
    rule_id: str
    jurisdiction: str
    citations: list[SectionRef]
    applies_from: date | None
    applies_to: date | None
    required_inputs: list[str]
    check: Callable[[LeaseInput], CheckResult] = field(repr=False)


def to_weekly_rent(amount: Decimal, frequency: str) -> Decimal:
    """Convert a rent amount to its weekly equivalent, rounded to cents."""
    if frequency == "weekly":
        weekly = amount
    elif frequency == "fortnightly":
        weekly = amount / 2
    else:
        weekly = amount * 12 / 52
    return weekly.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
