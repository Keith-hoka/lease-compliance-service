import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Audit(Base):
    __tablename__ = "audits"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    jurisdiction: Mapped[str] = mapped_column(String(3))
    as_at: Mapped[date] = mapped_column(Date)
    input: Mapped[dict] = mapped_column(JSON)
    findings: Mapped[list] = mapped_column(JSON)
    engine_version: Mapped[str] = mapped_column(String(20))
    client_id: Mapped[str] = mapped_column(String(50), index=True, server_default="legacy")
    client_ref: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditChange(Base):
    __tablename__ = "audit_changes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[str] = mapped_column(String(50), index=True)
    client_ref: Mapped[str] = mapped_column(String(100), index=True)
    old_audit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audits.id"))
    new_audit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audits.id"))
    changes: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
