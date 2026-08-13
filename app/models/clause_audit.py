import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ClauseAuditJob(Base):
    __tablename__ = "clause_audit_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[str] = mapped_column(String(50), index=True)
    client_ref: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    jurisdiction: Mapped[str] = mapped_column(String(3))
    as_at: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="pending", server_default="pending")
    document: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    document_kind: Mapped[str] = mapped_column(String(4))
    lease: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    findings: Mapped[list] = mapped_column(JSON, default=list)
    discrepancies: Mapped[list] = mapped_column(JSON, default=list)
    engine_version: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(120))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
