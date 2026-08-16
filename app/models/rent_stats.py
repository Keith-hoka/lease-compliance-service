import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RentSourceFile(Base):
    """One row per ingested workbook; the content hash makes reloads idempotent."""

    __tablename__ = "rent_source_files"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    jurisdiction: Mapped[str] = mapped_column(Text)
    source_file: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("jurisdiction", "source_file"),)


class RentBondLodgement(Base):
    __tablename__ = "rent_bond_lodgements"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rent_source_files.id", ondelete="CASCADE"), index=True
    )
    jurisdiction: Mapped[str] = mapped_column(Text)
    period: Mapped[str] = mapped_column(Text)
    postcode: Mapped[str] = mapped_column(Text)
    dwelling_type: Mapped[str] = mapped_column(Text)
    bedrooms: Mapped[int] = mapped_column(Integer)
    weekly_rent: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    __table_args__ = (Index("ix_rent_bond_lodgements_postcode_period", "postcode", "period"),)


class RentStatistic(Base):
    __tablename__ = "rent_statistics"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    jurisdiction: Mapped[str] = mapped_column(Text)
    period: Mapped[str] = mapped_column(Text)
    area_code: Mapped[str] = mapped_column(Text)
    dwelling_type: Mapped[str] = mapped_column(Text)
    bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    median: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    p25: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    p75: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "jurisdiction",
            "period",
            "area_code",
            "dwelling_type",
            "bedrooms",
            name="uq_rent_statistics_key",
        ),
        Index(
            "ix_rent_statistics_lookup",
            "jurisdiction",
            "area_code",
            "dwelling_type",
            "bedrooms",
            "period",
        ),
    )
