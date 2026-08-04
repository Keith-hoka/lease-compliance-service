import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.rules.base import Finding
from app.schemas.lease import LeaseInput


class AuditCreate(BaseModel):
    jurisdiction: Literal["NSW", "VIC"]
    as_at: date | None = None
    client_ref: str | None = None
    lease: LeaseInput


class AuditInfo(BaseModel):
    id: uuid.UUID
    jurisdiction: str
    as_at: date
    engine_version: str
    client_ref: str | None = None
    findings: list[Finding]
    created_at: datetime


class AuditChangeInfo(BaseModel):
    id: uuid.UUID
    client_ref: str
    old_audit_id: uuid.UUID
    new_audit_id: uuid.UUID
    changes: dict
    created_at: datetime
