import calendar
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.lease import LeaseInput

CheckResult = tuple[Literal["red", "green", "skipped"], str, dict]


@dataclass(frozen=True)
class SectionRef:
    act_slug: str
    section_no: str


class Citation(BaseModel):
    act: str
    section_no: str
    as_at: date
    section_id: uuid.UUID
    label: str | None = None


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


def add_months(d: date, months: int) -> date:
    """The corresponding date `months` calendar months after `d`.

    Statutory periods of months are reckoned by the corresponding-date
    rule (Dodds v Walker): the period ends on the corresponding day of
    the target month, or its last day when no corresponding day exists
    (Jan 31 + 1 month is the end of February; Feb 29 + 12 months is
    Feb 28). The Interpretation Act 1987 (NSW) Schedule 4 defines
    calendar month as the period to the corresponding day of the next
    named month, or that month's end when no corresponding day exists;
    the Interpretation of Legislation Act 1984 (Vic) s 44(6)(b)
    construes a month as a calendar month (both verified against the
    current in-force text, 2026-08-06).
    """
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
