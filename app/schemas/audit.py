import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.rules.base import Finding
from app.schemas.lease import LeaseInput


class AuditCreate(BaseModel):
    jurisdiction: Literal["NSW"]
    as_at: date | None = None
    lease: LeaseInput


class AuditInfo(BaseModel):
    id: uuid.UUID
    jurisdiction: str
    as_at: date
    engine_version: str
    findings: list[Finding]
    created_at: datetime
