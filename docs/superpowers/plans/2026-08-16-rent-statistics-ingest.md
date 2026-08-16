# Rent Statistics Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest NSW bond-lodgement rents and VIC published median rents
into the compliance service and serve them from a tenant endpoint.

**Architecture:** New `app/rent_stats/` package — `parser.py` (openpyxl,
per-source sheet mappers with header guards), `loader.py` (NSW detail
rows + SQL aggregation into a shared `rent_statistics` table; VIC medians
straight in; idempotent by file content hash), `fetcher.py` (httpx
downloads by known URL patterns), `__main__.py` CLI (`backfill` /
`update`), plus `GET /v1/rent-statistics` behind tenant auth. Spec:
`docs/superpowers/specs/2026-08-16-rent-statistics-ingest-design.md`.
Fixtures already committed under `tests/fixtures/rent_stats/` are trimmed
real workbooks; their exact values appear in the tests below.

**Tech Stack:** FastAPI + async SQLAlchemy 2.0 + Alembic + PostgreSQL,
openpyxl (new dependency), httpx, pytest.

## Global Constraints

- `uv` only; ruff sequence before every push in exact order: `uv run ruff format .` -> `uv run ruff check --fix .` -> `uv run ruff check .` -> `uv run ruff format --check .`; TDD (RED for the right reason first); no emojis; docstrings over comments; commit trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Full suite: `uv run pytest -m "not llm_eval" -q` (dev corpus DB running).
- Tables exactly: `rent_bond_lodgements` (id, jurisdiction, period YYYY-MM, postcode, dwelling_type, bedrooms, weekly_rent numeric, source_file, content_hash) and `rent_statistics` (jurisdiction, period, area_code, dwelling_type, bedrooms nullable, median, p25, p75 nullable, sample_size, source_url, fetched_at) with unique (jurisdiction, period, area_code, dwelling_type, bedrooms).
- Dwelling normalisation: NSW F->unit, H->house, T->townhouse, O->other, U->other, any other code->other (counted as `unknown_dwelling`); NSW rows with non-numeric bedrooms or rent are skipped (counted as `skipped_rows`). VIC sheets: "N bedroom flat"->(unit, N), "N bedroom house"->(house, N), "All properties"->(all, None).
- Header guards: NSW data sheet row 3 must equal `("Lodgement Date", "Postcode", "Dwelling Type", "Bedrooms", "Weekly Rent")`; VIC every expected sheet must exist, row 3 must alternate `Count`/`Median` from column C, row 2 carries period labels like `Sep 2025`. Mismatch raises `RentStatsFormatError` before any DB write.
- Idempotency: reloading a file whose content_hash is already recorded is a no-op; a changed hash replaces that file's rows and re-aggregates its periods.
- VIC period format `YYYY-Qn` (Mar->Q1, Jun->Q2, Sep->Q3, Dec->Q4); NSW period `YYYY-MM` from the lodgement date.
- VIC suppressed cells (`-` or None) produce no row; `Group Total` rows are skipped.
- Endpoint: `GET /v1/rent-statistics` with `jurisdiction` (NSW|VIC), `area`, `dwelling_type`, optional `bedrooms`, `periods` default 8 max 40; newest first; unknown area -> 200 empty series; auth via existing `TenantDep` + router-level `enforce_rate_limit`; usage recorded with endpoint class `rent_statistics`.
- Sources: NSW monthly `https://www.nsw.gov.au/sites/default/files/noindex/{YYYY}-{MM}/rentalbond_lodgements_{month}_{YYYY}.xlsx` (the folder is the month AFTER the data month, month name lowercase) and annual `.../noindex/{YYYY+1}-01/rentalbond_lodgements_year_{YYYY}.xlsx` for 2021-2025 (verify each annual folder at implementation — the 2025 file lives under `2026-01`); VIC `https://www.dffh.vic.gov.au/moving-annual-rent-suburb-{quarter}-quarter-{YYYY}-excel` (quarter in `march|june|september|december`), single workbook = full history.
- Tasks 1-4 are implementer tasks; Task 5 (production backfill + monitor wiring + docs) is controller-run.

---

### Task 1: Parsers with header guards

**Files:**
- Create: `app/rent_stats/__init__.py` (empty), `app/rent_stats/parser.py`
- Test: `tests/test_rent_stats_parser.py`
- Fixtures (already present): `tests/fixtures/rent_stats/nsw_lodgements_sample.xlsx`, `tests/fixtures/rent_stats/vic_moving_annual_sample.xlsx`

**Interfaces:**
- Produces:
  ```python
  class RentStatsFormatError(ValueError): ...
  @dataclass(frozen=True)
  class Lodgement: period: str; postcode: str; dwelling_type: str; bedrooms: int; weekly_rent: Decimal
  @dataclass(frozen=True)
  class NswParse: rows: list[Lodgement]; skipped_rows: int; unknown_dwelling: int
  @dataclass(frozen=True)
  class VicStat: period: str; area_code: str; dwelling_type: str; bedrooms: int | None; median: Decimal; sample_size: int
  def parse_nsw_lodgements(data: bytes) -> NswParse
  def parse_vic_moving_annual(data: bytes) -> list[VicStat]
  NSW_DWELLING = {"F": "unit", "H": "house", "T": "townhouse", "O": "other", "U": "other"}
  ```

- [ ] **Step 1: Add the dependency**

```bash
uv add openpyxl
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_rent_stats_parser.py`:

```python
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from app.rent_stats.parser import (
    RentStatsFormatError,
    parse_nsw_lodgements,
    parse_vic_moving_annual,
)

FIXTURES = Path(__file__).parent / "fixtures" / "rent_stats"
NSW = (FIXTURES / "nsw_lodgements_sample.xlsx").read_bytes()
VIC = (FIXTURES / "vic_moving_annual_sample.xlsx").read_bytes()


def test_nsw_parses_clean_rows_and_normalises_dwellings():
    parsed = parse_nsw_lodgements(NSW)
    assert len(parsed.rows) == 20
    first = parsed.rows[0]
    assert (first.period, first.postcode, first.dwelling_type) == ("2026-07", "2000", "unit")
    assert first.bedrooms == 0 and first.weekly_rent == Decimal("290")
    assert {r.dwelling_type for r in parsed.rows} == {"unit", "house", "townhouse", "other"}


def test_nsw_counts_dirty_rows():
    parsed = parse_nsw_lodgements(NSW)
    assert parsed.skipped_rows == 2
    assert parsed.unknown_dwelling == 1
    unknown = [r for r in parsed.rows if r.postcode == "2000" and r.dwelling_type == "other"]
    assert [r.weekly_rent for r in unknown] == [Decimal("800")]


def test_nsw_header_guard_trips():
    wb = openpyxl.load_workbook(FIXTURES / "nsw_lodgements_sample.xlsx")
    wb.worksheets[0].cell(row=3, column=5, value="Rent")
    mutated = _bytes(wb)
    with pytest.raises(RentStatsFormatError, match="header"):
        parse_nsw_lodgements(mutated)


def test_vic_parses_periods_sheets_and_suppressions():
    stats = parse_vic_moving_annual(VIC)
    two_bed = [s for s in stats if s.dwelling_type == "unit" and s.bedrooms == 2]
    albert = [s for s in two_bed if s.area_code == "Albert Park-Middle Park-West St Kilda"]
    periods = sorted(s.period for s in albert)
    assert periods[0] == "2024-Q2" and periods[-1] == "2025-Q3" and len(periods) == 6
    latest = next(s for s in albert if s.period == "2025-Q3")
    assert (latest.median, latest.sample_size) == (Decimal("643"), 144)
    assert all(s.area_code != "Group Total" for s in stats)
    all_props = [s for s in stats if s.dwelling_type == "all"]
    assert all_props and all(s.bedrooms is None for s in all_props)


def test_vic_missing_sheet_trips_guard():
    wb = openpyxl.load_workbook(FIXTURES / "vic_moving_annual_sample.xlsx")
    wb.remove(wb["3 bedroom house"])
    with pytest.raises(RentStatsFormatError, match="sheet"):
        parse_vic_moving_annual(_bytes(wb))


def _bytes(wb) -> bytes:
    import io

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_rent_stats_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: app.rent_stats`.

- [ ] **Step 4: Implement the parser**

Create `app/rent_stats/parser.py`:

```python
"""Workbook parsers for the official rent datasets, with fail-loud format guards."""

import io
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import openpyxl

NSW_HEADER = ("Lodgement Date", "Postcode", "Dwelling Type", "Bedrooms", "Weekly Rent")
NSW_DWELLING = {"F": "unit", "H": "house", "T": "townhouse", "O": "other", "U": "other"}
VIC_SHEETS = {
    "1 bedroom flat": ("unit", 1),
    "2 bedroom flat": ("unit", 2),
    "3 bedroom flat": ("unit", 3),
    "2 bedroom house": ("house", 2),
    "3 bedroom house": ("house", 3),
    "4 bedroom house": ("house", 4),
    "All properties": ("all", None),
}
_VIC_PERIOD = re.compile(r"^(Mar|Jun|Sep|Dec) (\d{4})$")
_QUARTER = {"Mar": "Q1", "Jun": "Q2", "Sep": "Q3", "Dec": "Q4"}


class RentStatsFormatError(ValueError):
    """The source workbook does not match the pinned layout."""


@dataclass(frozen=True)
class Lodgement:
    period: str
    postcode: str
    dwelling_type: str
    bedrooms: int
    weekly_rent: Decimal


@dataclass(frozen=True)
class NswParse:
    rows: list[Lodgement]
    skipped_rows: int
    unknown_dwelling: int


@dataclass(frozen=True)
class VicStat:
    period: str
    area_code: str
    dwelling_type: str
    bedrooms: int | None
    median: Decimal
    sample_size: int


def parse_nsw_lodgements(data: bytes) -> NswParse:
    sheet = openpyxl.load_workbook(io.BytesIO(data), read_only=True).worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    next(rows), next(rows)
    header = tuple(next(rows)[:5])
    if header != NSW_HEADER:
        raise RentStatsFormatError(f"NSW header mismatch: {header}")
    parsed: list[Lodgement] = []
    skipped = unknown = 0
    for row in rows:
        lodged, postcode, dwelling, bedrooms, rent = row[:5]
        if lodged is None:
            continue
        try:
            beds = int(str(bedrooms))
            weekly = Decimal(str(rent))
        except (ValueError, InvalidOperation):
            skipped += 1
            continue
        code = str(dwelling)
        if code not in NSW_DWELLING:
            unknown += 1
        parsed.append(
            Lodgement(
                period=lodged.strftime("%Y-%m"),
                postcode=str(postcode),
                dwelling_type=NSW_DWELLING.get(code, "other"),
                bedrooms=beds,
                weekly_rent=weekly,
            )
        )
    return NswParse(rows=parsed, skipped_rows=skipped, unknown_dwelling=unknown)


def parse_vic_moving_annual(data: bytes) -> list[VicStat]:
    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    missing = [name for name in VIC_SHEETS if name not in workbook.sheetnames]
    if missing:
        raise RentStatsFormatError(f"VIC sheet missing: {missing}")
    stats: list[VicStat] = []
    for name, (dwelling, bedrooms) in VIC_SHEETS.items():
        stats.extend(_parse_vic_sheet(workbook[name], dwelling, bedrooms))
    return stats


def _parse_vic_sheet(sheet, dwelling: str, bedrooms: int | None) -> list[VicStat]:
    rows = list(sheet.iter_rows(values_only=True))
    periods = _vic_periods(rows[1], rows[2])
    out: list[VicStat] = []
    for row in rows[3:]:
        area = row[1]
        if not area or area == "Group Total":
            continue
        for period, count_col in periods:
            count, median = row[count_col], row[count_col + 1]
            if count in (None, "-") or median in (None, "-"):
                continue
            out.append(
                VicStat(
                    period=period,
                    area_code=str(area),
                    dwelling_type=dwelling,
                    bedrooms=bedrooms,
                    median=Decimal(str(median)),
                    sample_size=int(count),
                )
            )
    return out


def _vic_periods(label_row, kind_row) -> list[tuple[str, int]]:
    """(period, count-column index) pairs; guards the Count/Median alternation."""
    periods: list[tuple[str, int]] = []
    for col in range(2, len(label_row), 2):
        label = label_row[col]
        if label is None:
            break
        match = _VIC_PERIOD.match(str(label).strip())
        if not match or (kind_row[col], kind_row[col + 1]) != ("Count", "Median"):
            raise RentStatsFormatError(f"VIC header mismatch at column {col}: {label}")
        periods.append((f"{match.group(2)}-{_QUARTER[match.group(1)]}", col))
    if not periods:
        raise RentStatsFormatError("VIC header carries no periods")
    return periods
```

- [ ] **Step 5: Run the tests, then the full suite, ruff, commit**

Run: `uv run pytest tests/test_rent_stats_parser.py -v` — Expected: 5 PASS.
Run: `uv run pytest -m "not llm_eval" -q` — Expected: PASS.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add pyproject.toml uv.lock app/rent_stats tests/test_rent_stats_parser.py
git commit -m "Add rent statistics workbook parsers with format guards"
```

---

### Task 2: Models, migration, loader with aggregation

**Files:**
- Create: `app/models/rent_stats.py`; Modify: `app/models/__init__.py` (export)
- Create: `alembic/versions/<generated>_rent_statistics.py`
- Create: `app/rent_stats/loader.py`
- Test: `tests/test_rent_stats_loader.py`

**Interfaces:**
- Consumes: Task 1 parsers.
- Produces: models `RentBondLodgement`, `RentStatistic`, `RentSourceFile`
  (id, jurisdiction, source_file, content_hash, fetched_at — the
  idempotency ledger); `async load_nsw_file(session, source_file: str,
  data: bytes, source_url: str) -> LoadResult`; `async load_vic_file(session,
  source_file, data, source_url) -> LoadResult`; `LoadResult(loaded_rows,
  skipped_rows, unknown_dwelling, periods, unchanged: bool)`;
  `async aggregate_nsw(session, periods: list[str], source_url: str) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rent_stats_loader.py`:

```python
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app.models import RentBondLodgement, RentStatistic
from app.rent_stats.loader import load_nsw_file, load_vic_file

FIXTURES = Path(__file__).parent / "fixtures" / "rent_stats"
NSW = (FIXTURES / "nsw_lodgements_sample.xlsx").read_bytes()
VIC = (FIXTURES / "vic_moving_annual_sample.xlsx").read_bytes()
URL = "https://example.test/src.xlsx"


async def _count(session, model):
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _stat(session, **where):
    stmt = select(RentStatistic).filter_by(**where)
    return (await session.execute(stmt)).scalar_one()


async def test_nsw_load_inserts_detail_and_aggregates(db_session):
    result = await load_nsw_file(db_session, "nsw_july_2026.xlsx", NSW, URL)
    await db_session.commit()
    assert (result.loaded_rows, result.skipped_rows, result.unknown_dwelling) == (20, 2, 1)
    assert result.periods == ["2026-07"] and result.unchanged is False
    assert await _count(db_session, RentBondLodgement) == 20

    unit_2000 = await _stat(
        db_session,
        jurisdiction="NSW",
        period="2026-07",
        area_code="2000",
        dwelling_type="unit",
        bedrooms=None,
    )
    assert unit_2000.sample_size == 6
    assert unit_2000.median == Decimal("760")
    assert unit_2000.p25 == Decimal("697.5") and unit_2000.p75 == Decimal("886.25")

    house_2150 = await _stat(
        db_session,
        jurisdiction="NSW",
        period="2026-07",
        area_code="2150",
        dwelling_type="house",
        bedrooms=None,
    )
    assert house_2150.sample_size == 5 and house_2150.median == Decimal("660")

    all_2000 = await _stat(
        db_session,
        jurisdiction="NSW",
        period="2026-07",
        area_code="2000",
        dwelling_type="all",
        bedrooms=None,
    )
    assert all_2000.sample_size == 9


async def test_nsw_reload_same_hash_is_noop(db_session):
    await load_nsw_file(db_session, "nsw_july_2026.xlsx", NSW, URL)
    await db_session.commit()
    again = await load_nsw_file(db_session, "nsw_july_2026.xlsx", NSW, URL)
    await db_session.commit()
    assert again.unchanged is True
    assert await _count(db_session, RentBondLodgement) == 20


async def test_nsw_changed_hash_replaces_file_rows(db_session):
    await load_nsw_file(db_session, "nsw_july_2026.xlsx", NSW, URL)
    await db_session.commit()
    trimmed = _drop_last_row(NSW)
    result = await load_nsw_file(db_session, "nsw_july_2026.xlsx", trimmed, URL)
    await db_session.commit()
    assert result.unchanged is False and result.loaded_rows == 19
    assert await _count(db_session, RentBondLodgement) == 19


async def test_vic_load_upserts_published_medians(db_session):
    result = await load_vic_file(db_session, "vic_sep_2025.xlsx", VIC, URL)
    await db_session.commit()
    assert result.unchanged is False and result.loaded_rows > 0
    stat = await _stat(
        db_session,
        jurisdiction="VIC",
        period="2025-Q3",
        area_code="Albert Park-Middle Park-West St Kilda",
        dwelling_type="unit",
        bedrooms=2,
    )
    assert (stat.median, stat.sample_size, stat.p25, stat.p75) == (Decimal("643"), 144, None, None)
    again = await load_vic_file(db_session, "vic_sep_2025.xlsx", VIC, URL)
    assert again.unchanged is True


def _drop_last_row(data: bytes) -> bytes:
    import io

    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb.worksheets[0]
    ws.delete_rows(ws.max_row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

(Fixture arithmetic: 22 data rows, 2 skipped (`U` bedrooms, `U` rent), 20
loaded of which 2 are `other` (stray code `1`, code `O`). 2000/unit rents
290,680,750,770,925,950 -> median 760, p25 697.5, p75 886.25 by
`percentile_cont`; 2150/house clean rents 650,650,660,720,725 -> median
660; 2000/all = 6 units + 2 houses + 1 unknown-coded "other" = 9. The
last fixture row (2010/O) is parseable, so dropping it leaves 19. Check the `db_session` fixture name in
`tests/conftest.py` and adapt if it differs.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rent_stats_loader.py -v`
Expected: FAIL — `ImportError` on the new models.

- [ ] **Step 3: Implement models and migration**

Create `app/models/rent_stats.py`:

```python
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
```

Postgres treats NULLs as distinct in unique constraints, so the loader
must upsert with `ON CONFLICT` on a functional key — implement the upsert
in the loader as delete-then-insert per (jurisdiction, period,
area_code, dwelling_type, bedrooms IS NOT DISTINCT FROM :b) to keep the
`bedrooms = NULL` rollups idempotent (see loader below). Export the three
models from `app/models/__init__.py` in the existing style. Generate the
migration with `uv run alembic revision --autogenerate -m "rent statistics"`
against the dev DB, review it (three tables, both indexes, the unique
constraint), and apply `uv run alembic upgrade head`.

- [ ] **Step 4: Implement the loader**

Create `app/rent_stats/loader.py`:

```python
"""Load parsed workbooks idempotently and aggregate NSW detail into statistics."""

import hashlib
from dataclasses import dataclass

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RentBondLodgement, RentSourceFile, RentStatistic
from app.rent_stats.parser import parse_nsw_lodgements, parse_vic_moving_annual

NSW_SOURCE_NAME = "NSW Fair Trading rental bond lodgements"
VIC_SOURCE_NAME = "Homes Victoria Rental Report (moving annual median rents by suburb)"


@dataclass(frozen=True)
class LoadResult:
    loaded_rows: int
    skipped_rows: int
    unknown_dwelling: int
    periods: list[str]
    unchanged: bool


async def _source_file(session, jurisdiction, source_file, data, source_url):
    """Return (row, unchanged). A new hash replaces the row and its cascaded detail."""
    digest = hashlib.sha256(data).hexdigest()
    existing = (
        await session.execute(
            select(RentSourceFile).where(
                RentSourceFile.jurisdiction == jurisdiction,
                RentSourceFile.source_file == source_file,
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.content_hash == digest:
        return existing, True
    if existing is not None:
        await session.delete(existing)
        await session.flush()
    row = RentSourceFile(
        jurisdiction=jurisdiction,
        source_file=source_file,
        content_hash=digest,
        source_url=source_url,
    )
    session.add(row)
    await session.flush()
    return row, False


async def load_nsw_file(
    session: AsyncSession, source_file: str, data: bytes, source_url: str
) -> LoadResult:
    row, unchanged = await _source_file(session, "NSW", source_file, data, source_url)
    if unchanged:
        return LoadResult(0, 0, 0, [], True)
    parsed = parse_nsw_lodgements(data)
    session.add_all(
        RentBondLodgement(
            source_file_id=row.id,
            jurisdiction="NSW",
            period=r.period,
            postcode=r.postcode,
            dwelling_type=r.dwelling_type,
            bedrooms=r.bedrooms,
            weekly_rent=r.weekly_rent,
        )
        for r in parsed.rows
    )
    await session.flush()
    periods = sorted({r.period for r in parsed.rows})
    await aggregate_nsw(session, periods, source_url)
    return LoadResult(
        len(parsed.rows), parsed.skipped_rows, parsed.unknown_dwelling, periods, False
    )


_NSW_AGGREGATE = text(
    """
    WITH base AS (
        SELECT period, postcode, dwelling_type, bedrooms, weekly_rent
        FROM rent_bond_lodgements
        WHERE jurisdiction = 'NSW' AND period = ANY(:periods)
    ),
    grouped AS (
        SELECT period, postcode, dwelling_type, bedrooms, weekly_rent FROM base
        UNION ALL SELECT period, postcode, dwelling_type, NULL, weekly_rent FROM base
        UNION ALL SELECT period, postcode, 'all', NULL, weekly_rent FROM base
    )
    INSERT INTO rent_statistics
        (id, jurisdiction, period, area_code, dwelling_type, bedrooms, median, p25, p75, sample_size, source_url)
    SELECT gen_random_uuid(), 'NSW', period, postcode, dwelling_type, bedrooms,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY weekly_rent),
           percentile_cont(0.25) WITHIN GROUP (ORDER BY weekly_rent),
           percentile_cont(0.75) WITHIN GROUP (ORDER BY weekly_rent),
           count(*), :source_url
    FROM grouped
    GROUP BY period, postcode, dwelling_type, bedrooms
    """
)


async def aggregate_nsw(session: AsyncSession, periods: list[str], source_url: str) -> int:
    """Recompute NSW statistics for the given periods from the detail table."""
    if not periods:
        return 0
    await session.execute(
        delete(RentStatistic).where(
            RentStatistic.jurisdiction == "NSW", RentStatistic.period.in_(periods)
        )
    )
    result = await session.execute(_NSW_AGGREGATE, {"periods": periods, "source_url": source_url})
    return result.rowcount


async def load_vic_file(
    session: AsyncSession, source_file: str, data: bytes, source_url: str
) -> LoadResult:
    row, unchanged = await _source_file(session, "VIC", source_file, data, source_url)
    if unchanged:
        return LoadResult(0, 0, 0, [], True)
    stats = parse_vic_moving_annual(data)
    periods = sorted({s.period for s in stats})
    await session.execute(
        delete(RentStatistic).where(
            RentStatistic.jurisdiction == "VIC", RentStatistic.period.in_(periods)
        )
    )
    session.add_all(
        RentStatistic(
            jurisdiction="VIC",
            period=s.period,
            area_code=s.area_code,
            dwelling_type=s.dwelling_type,
            bedrooms=s.bedrooms,
            median=s.median,
            p25=None,
            p75=None,
            sample_size=s.sample_size,
            source_url=source_url,
        )
        for s in stats
    )
    await session.flush()
    return LoadResult(len(stats), 0, 0, periods, False)
```

Note the design choice: NSW aggregation is period-scoped delete-then-
insert (a whole month is recomputed from all its detail rows, so annual
backfill files and monthly files agree), and VIC is period-scoped
delete-then-insert as well; both stay idempotent without relying on
NULL-aware unique constraints. `gen_random_uuid()` is built into
PostgreSQL 13+.

- [ ] **Step 5: Run the tests, full suite, ruff, commit**

Run: `uv run pytest tests/test_rent_stats_loader.py -v` — Expected: 4 PASS.
Run: `uv run pytest -m "not llm_eval" -q` — Expected: PASS.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/models/rent_stats.py app/models/__init__.py alembic/versions app/rent_stats/loader.py tests/test_rent_stats_loader.py
git commit -m "Add rent statistics tables, migration, and idempotent loader"
```

---

### Task 3: Fetcher and CLI

**Files:**
- Create: `app/rent_stats/fetcher.py`, `app/rent_stats/__main__.py`
- Test: `tests/test_rent_stats_fetcher.py`

**Interfaces:**
- Consumes: Task 2 loaders.
- Produces: `nsw_annual_url(year) -> str`, `nsw_monthly_url(year, month) -> str`,
  `vic_quarter_url(year, quarter) -> str`, `nsw_monthly_targets(today: date,
  since: date) -> list[tuple[str, str]]` ((source_file, url) pairs, oldest
  first, up to the month before `today`), `async fetch(client, url) -> bytes |
  None` (None on 404 — a month not yet published), `async run_backfill(session,
  client, today) -> dict`, `async run_update(session, client, today) -> dict`;
  CLI `uv run python -m app.rent_stats backfill|update`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rent_stats_fetcher.py`:

```python
from datetime import date

import httpx
import pytest

from app.rent_stats import fetcher


def test_nsw_urls_follow_the_published_patterns():
    assert fetcher.nsw_monthly_url(2026, 7) == (
        "https://www.nsw.gov.au/sites/default/files/noindex/2026-08/rentalbond_lodgements_july_2026.xlsx"
    )
    assert fetcher.nsw_monthly_url(2025, 12) == (
        "https://www.nsw.gov.au/sites/default/files/noindex/2026-01/rentalbond_lodgements_december_2025.xlsx"
    )
    assert fetcher.nsw_annual_url(2025) == (
        "https://www.nsw.gov.au/sites/default/files/noindex/2026-01/rentalbond_lodgements_year_2025.xlsx"
    )


def test_vic_url_follows_the_published_pattern():
    assert fetcher.vic_quarter_url(2025, 3) == (
        "https://www.dffh.vic.gov.au/moving-annual-rent-suburb-september-quarter-2025-excel"
    )


def test_nsw_monthly_targets_stop_before_current_month():
    targets = fetcher.nsw_monthly_targets(today=date(2026, 8, 16), since=date(2026, 1, 1))
    names = [name for name, _ in targets]
    assert names[0] == "rentalbond_lodgements_january_2026.xlsx"
    assert names[-1] == "rentalbond_lodgements_july_2026.xlsx"
    assert len(names) == 7


async def test_fetch_returns_none_on_404():
    def handler(request):
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetcher.fetch(client, "https://example.test/missing.xlsx") is None


async def test_fetch_raises_on_server_error():
    def handler(request):
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetcher.fetch(client, "https://example.test/down.xlsx")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rent_stats_fetcher.py -v`
Expected: FAIL — `ImportError: cannot import name 'fetcher'`.

- [ ] **Step 3: Implement the fetcher**

Create `app/rent_stats/fetcher.py`:

```python
"""Download the official rent workbooks by their published URL patterns."""

import calendar
import logging
from datetime import date

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.rent_stats.loader import load_nsw_file, load_vic_file

logger = logging.getLogger("app.rent_stats")

NSW_BASE = "https://www.nsw.gov.au/sites/default/files/noindex"
VIC_BASE = "https://www.dffh.vic.gov.au"
NSW_ANNUAL_YEARS = range(2021, 2026)
NSW_MONTHLY_SINCE = date(2026, 1, 1)
_VIC_QUARTER_MONTH = {1: "march", 2: "june", 3: "september", 4: "december"}


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def nsw_monthly_url(year: int, month: int) -> str:
    folder_year, folder_month = _next_month(year, month)
    name = calendar.month_name[month].lower()
    return f"{NSW_BASE}/{folder_year}-{folder_month:02d}/rentalbond_lodgements_{name}_{year}.xlsx"


def nsw_annual_url(year: int) -> str:
    return f"{NSW_BASE}/{year + 1}-01/rentalbond_lodgements_year_{year}.xlsx"


def vic_quarter_url(year: int, quarter: int) -> str:
    return (
        f"{VIC_BASE}/moving-annual-rent-suburb-{_VIC_QUARTER_MONTH[quarter]}-quarter-{year}-excel"
    )


def nsw_monthly_targets(today: date, since: date) -> list[tuple[str, str]]:
    """(source_file, url) for every complete month from `since` up to last month."""
    targets = []
    year, month = since.year, since.month
    while (year, month) < (today.year, today.month):
        url = nsw_monthly_url(year, month)
        targets.append((url.rsplit("/", 1)[1], url))
        year, month = _next_month(year, month)
    return targets


def latest_vic_quarter(today: date) -> tuple[int, int]:
    """The most recent quarter-end that is at least ~5 months old (publication lag)."""
    year, month = today.year, today.month - 5
    while month <= 0:
        year, month = year - 1, month + 12
    return year, (month - 1) // 3 + 1


async def fetch(client: httpx.AsyncClient, url: str) -> bytes | None:
    response = await client.get(url, follow_redirects=True, timeout=120)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


async def _load_nsw_targets(session, client, targets, summary):
    for name, url in targets:
        data = await fetch(client, url)
        if data is None:
            summary["nsw_missing"].append(name)
            continue
        result = await load_nsw_file(session, name, data, url)
        await session.commit()
        summary["nsw_files"] += 0 if result.unchanged else 1
        summary["nsw_rows"] += result.loaded_rows


async def _load_vic(session, client, today, summary):
    year, quarter = latest_vic_quarter(today)
    url = vic_quarter_url(year, quarter)
    data = await fetch(client, url)
    if data is None:
        summary["vic_missing"].append(url)
        return
    result = await load_vic_file(session, f"vic_moving_annual_{year}_q{quarter}.xlsx", data, url)
    await session.commit()
    summary["vic_files"] += 0 if result.unchanged else 1
    summary["vic_rows"] += result.loaded_rows


def _summary() -> dict:
    return {
        "nsw_files": 0,
        "nsw_rows": 0,
        "nsw_missing": [],
        "vic_files": 0,
        "vic_rows": 0,
        "vic_missing": [],
    }


async def run_backfill(session: AsyncSession, client: httpx.AsyncClient, today: date) -> dict:
    summary = _summary()
    annual = [(f"rentalbond_lodgements_year_{y}.xlsx", nsw_annual_url(y)) for y in NSW_ANNUAL_YEARS]
    await _load_nsw_targets(session, client, annual, summary)
    await _load_nsw_targets(session, client, nsw_monthly_targets(today, NSW_MONTHLY_SINCE), summary)
    await _load_vic(session, client, today, summary)
    return summary


async def run_update(session: AsyncSession, client: httpx.AsyncClient, today: date) -> dict:
    """Load anything new: recent NSW months (last three) and the current VIC quarter."""
    summary = _summary()
    year, month = today.year, today.month
    for _ in range(3):
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    since = date(year, month, 1)
    await _load_nsw_targets(session, client, nsw_monthly_targets(today, since), summary)
    await _load_vic(session, client, today, summary)
    return summary
```

Create `app/rent_stats/__main__.py` following `app/monitor/__main__.py`'s
argparse + session style:

```python
"""CLI: uv run python -m app.rent_stats backfill|update"""

import argparse
import asyncio
import json
from datetime import date

import httpx

from app.core.db import async_session_factory
from app.core.logs import configure_logging
from app.rent_stats.fetcher import run_backfill, run_update


async def run(command: str) -> None:
    async with async_session_factory() as session, httpx.AsyncClient() as client:
        runner = run_backfill if command == "backfill" else run_update
        summary = await runner(session, client, date.today())
    print(json.dumps(summary))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["backfill", "update"])
    args = parser.parse_args()
    configure_logging()
    asyncio.run(run(args.command))


if __name__ == "__main__":
    main()
```

(Check `app/core/logs.configure_logging` exists as used by `app/main.py`;
if the monitor CLI wires logging differently, mirror the monitor.)

- [ ] **Step 4: Run tests, full suite, ruff, commit**

Run: `uv run pytest tests/test_rent_stats_fetcher.py -v` — Expected: 5 PASS.
Run: `uv run pytest -m "not llm_eval" -q` — Expected: PASS.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/rent_stats/fetcher.py app/rent_stats/__main__.py tests/test_rent_stats_fetcher.py
git commit -m "Add the rent statistics fetcher and CLI"
```

---

### Task 4: Query endpoint

**Files:**
- Create: `app/routers/rent_statistics.py`, `app/schemas/rent_statistics.py`
- Modify: `app/main.py` (include router)
- Test: `tests/test_rent_statistics_api.py`

**Interfaces:**
- Consumes: `RentStatistic` model; `TenantDep`, `enforce_rate_limit` from
  `app/core/auth.py` / the rate-limit module the audits router imports;
  `record_usage` from `app/core/usage.py`; the loader from Task 2 for test
  seeding.
- Produces: `GET /v1/rent-statistics` per the Global Constraints.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rent_statistics_api.py`. Auth follows
`tests/test_clause_api.py`: the `seeded_tenants` fixture (conftest) creates
tenants whose keys are `test-key`/`other-key`; requests pass
`headers={"X-API-Key": "test-key"}`. `KEY` below is that dict.

```python
from pathlib import Path

from app.rent_stats.loader import load_nsw_file, load_vic_file

FIXTURES = Path(__file__).parent / "fixtures" / "rent_stats"
KEY = {"X-API-Key": "test-key"}


async def _seed(session):
    await load_nsw_file(
        session, "nsw_july_2026.xlsx", (FIXTURES / "nsw_lodgements_sample.xlsx").read_bytes(), "u1"
    )
    await load_vic_file(
        session, "vic.xlsx", (FIXTURES / "vic_moving_annual_sample.xlsx").read_bytes(), "u2"
    )
    await session.commit()


async def test_requires_api_key(client):
    response = await client.get(
        "/v1/rent-statistics",
        params={"jurisdiction": "NSW", "area": "2000", "dwelling_type": "unit"},
    )
    assert response.status_code == 401


async def test_nsw_series_newest_first_with_percentiles(client, db_session, seeded_tenants):
    await _seed(db_session)
    response = await client.get(
        "/v1/rent-statistics",
        params={"jurisdiction": "NSW", "area": "2000", "dwelling_type": "unit"},
        headers=KEY,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["jurisdiction"] == "NSW" and body["bedrooms"] is None
    assert body["series"] == [
        {
            "period": "2026-07",
            "median": "760.00",
            "p25": "697.50",
            "p75": "886.25",
            "sample_size": 6,
        }
    ]
    assert body["source"]["name"].startswith("NSW Fair Trading")


async def test_vic_series_respects_periods_cap_and_order(client, db_session, seeded_tenants):
    await _seed(db_session)
    response = await client.get(
        "/v1/rent-statistics",
        params={
            "jurisdiction": "VIC",
            "area": "Albert Park-Middle Park-West St Kilda",
            "dwelling_type": "unit",
            "bedrooms": 2,
            "periods": 3,
        },
        headers=KEY,
    )
    body = response.json()
    assert [s["period"] for s in body["series"]] == ["2025-Q3", "2025-Q2", "2025-Q1"]
    assert body["series"][0]["p25"] is None and body["series"][0]["median"] == "643.00"


async def test_unknown_area_is_empty_series(client, db_session, seeded_tenants):
    await _seed(db_session)
    response = await client.get(
        "/v1/rent-statistics",
        params={"jurisdiction": "NSW", "area": "0000", "dwelling_type": "unit"},
        headers=KEY,
    )
    assert response.status_code == 200 and response.json()["series"] == []


async def test_periods_above_cap_is_422(client, seeded_tenants):
    response = await client.get(
        "/v1/rent-statistics",
        params={"jurisdiction": "NSW", "area": "2000", "dwelling_type": "unit", "periods": 41},
        headers=KEY,
    )
    assert response.status_code == 422
```

Also add a test asserting usage recording: after one successful call, a
`UsageCounter` row exists with `endpoint_class == "rent_statistics"`
(`select(UsageCounter).where(UsageCounter.endpoint_class == "rent_statistics")`
on `db_session`; `UsageCounter` is exported from `app.models`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rent_statistics_api.py -v`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Implement schemas, router, registration**

Create `app/schemas/rent_statistics.py`:

```python
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
    dwelling_type: str
    bedrooms: int | None
    series: list[RentStatPoint]
    source: RentStatSource
```

Create `app/routers/rent_statistics.py`:

```python
"""Point-in-time rent statistics from the official bond datasets."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TenantDep
from app.core.db import get_session
from app.core.ratelimit import enforce_rate_limit
from app.core.usage import record_usage
from app.models import RentStatistic
from app.rent_stats.loader import NSW_SOURCE_NAME, VIC_SOURCE_NAME
from app.schemas.rent_statistics import RentStatisticsResponse, RentStatPoint, RentStatSource

router = APIRouter(prefix="/v1", dependencies=[Depends(enforce_rate_limit)])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

_SOURCES = {
    "NSW": (
        NSW_SOURCE_NAME,
        "https://www.nsw.gov.au/housing-and-construction/rental-forms-surveys-and-data/rental-bond-data",
        "NSW Government open data (terms on the source page)",
    ),
    "VIC": (VIC_SOURCE_NAME, "https://www.dffh.vic.gov.au/publications/rental-report", "CC BY 4.0"),
}


@router.get("/rent-statistics", response_model=RentStatisticsResponse)
async def get_rent_statistics(
    tenant: TenantDep,
    session: SessionDep,
    jurisdiction: Literal["NSW", "VIC"],
    area: str,
    dwelling_type: str,
    bedrooms: int | None = None,
    periods: Annotated[int, Query(ge=1, le=40)] = 8,
) -> RentStatisticsResponse:
    stmt = (
        select(RentStatistic)
        .where(
            RentStatistic.jurisdiction == jurisdiction,
            RentStatistic.area_code == area,
            RentStatistic.dwelling_type == dwelling_type,
            RentStatistic.bedrooms.is_(None)
            if bedrooms is None
            else RentStatistic.bedrooms == bedrooms,
        )
        .order_by(RentStatistic.period.desc())
        .limit(periods)
    )
    rows = (await session.execute(stmt)).scalars().all()
    await record_usage(session, tenant.tenant_id, "rent_statistics")
    await session.commit()
    name, url, licence = _SOURCES[jurisdiction]
    return RentStatisticsResponse(
        jurisdiction=jurisdiction,
        area=area,
        dwelling_type=dwelling_type,
        bedrooms=bedrooms,
        series=[
            RentStatPoint(
                period=r.period, median=r.median, p25=r.p25, p75=r.p75, sample_size=r.sample_size
            )
            for r in rows
        ],
        source=RentStatSource(
            name=name, url=url, licence=licence, fetched_at=rows[0].fetched_at if rows else None
        ),
    )
```

(Import `enforce_rate_limit` from wherever `app/routers/audits.py` imports
it — check the module path.) Register in `app/main.py` exactly like the
other routers. Period ordering: `YYYY-MM` and `YYYY-Qn` both sort
correctly as text within one jurisdiction.

- [ ] **Step 4: Run tests, full suite, ruff, commit**

Run: `uv run pytest tests/test_rent_statistics_api.py -v` — Expected: PASS.
Run: `uv run pytest -m "not llm_eval" -q` — Expected: PASS.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/routers/rent_statistics.py app/schemas/rent_statistics.py app/main.py tests/test_rent_statistics_api.py
git commit -m "Serve rent statistics from a tenant endpoint"
```

---

### Task 5: Production backfill, monitor wiring, docs (controller-run)

- [ ] **Step 1: Deploy** — after Tasks 1-4 are pushed and CI is green,
  `LEASE_DEPLOY_SERVER=deploy@168.144.169.66 LEASE_DEPLOY_DOMAIN=api.leasekoala.com ./deploy/deploy.sh sha-<short>` (migration runs on deploy); verify image and `/health`.
- [ ] **Step 2: Backfill production** — from the Mac over the manual tunnel
  (port 15433, `DATABASE_URL` in tunnel form): `uv run python -m app.rent_stats backfill`; record the printed summary (files/rows per jurisdiction, any `nsw_missing`) in the ledger. Spot-check: `GET /v1/rent-statistics?jurisdiction=NSW&area=2000&dwelling_type=unit&bedrooms=2&periods=3` and one VIC suburb against the published workbook values.
- [ ] **Step 3: Monitor wiring** — add `uv run python -m app.rent_stats update` after the jurisdiction loop in `deploy/launchd/monitor-remote.sh` (same tunnel, port 15434); commit.
- [ ] **Step 4: Docs** — `deploy/README.md`: new "Rent statistics" section (sources, licence note incl. the NSW terms check before external tenants rely on it, backfill/update commands, the `rent_statistics` usage class); `docs/rule-candidates.md` untouched. Ruff sequence, commit "Document rent statistics ingest and wire the daily update", push, CI green.
- [ ] **Step 5: Close** — ledger entry; final whole-branch review per SDD; memory roadmap update.
