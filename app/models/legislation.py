import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Act(Base):
    __tablename__ = "acts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    jurisdiction: Mapped[str] = mapped_column(String(3), index=True)
    slug: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(String(500))


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    act_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acts.id"), index=True)
    section_no: Mapped[str] = mapped_column(String(20))
    heading: Mapped[str] = mapped_column(String(300))
    body_text: Mapped[str] = mapped_column(Text)
    part: Mapped[str | None] = mapped_column(String(300), nullable=True)
    division: Mapped[str | None] = mapped_column(String(300), nullable=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_version_date: Mapped[date] = mapped_column(Date)
    content_hash: Mapped[str] = mapped_column(String(64))


class IngestedVersion(Base):
    __tablename__ = "ingested_versions"

    act_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acts.id"), primary_key=True)
    version_date: Mapped[date] = mapped_column(Date, primary_key=True)
