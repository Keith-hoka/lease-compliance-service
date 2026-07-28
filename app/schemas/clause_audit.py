import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.rules.base import Finding


class ClauseFinding(Finding):
    """A cited LLM finding; clause_quote is the lease text it rests on."""

    clause_quote: str | None = None


class Discrepancy(BaseModel):
    """A field cross-check mismatch. Data integrity, not law: no citation."""

    field: str
    document_value: str
    submitted_value: str


class ClauseLeaseInput(BaseModel):
    """Money/date subset of LeaseInput; all optional, presence gates family 2."""

    rent_amount: Decimal | None = None
    rent_frequency: Literal["weekly", "fortnightly", "monthly"] | None = None
    start_date: date | None = None
    end_date: date | None = None
    bond_amount: Decimal | None = None
    rent_in_advance_amount: Decimal | None = None
    holding_deposit_amount: Decimal | None = None
    other_security_amount: Decimal | None = None
    break_fee_amount: Decimal | None = None


class ClauseAuditCreate(BaseModel):
    """The JSON `payload` part of the multipart POST."""

    jurisdiction: Literal["NSW"]
    as_at: date | None = None
    client_ref: str | None = None
    lease: ClauseLeaseInput | None = None


class ClauseAuditInfo(BaseModel):
    id: uuid.UUID
    status: Literal["pending", "running", "succeeded", "failed"]
    jurisdiction: str
    as_at: date
    engine_version: str
    model: str
    client_ref: str | None = None
    findings: list[ClauseFinding] = []
    discrepancies: list[Discrepancy] = []
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
