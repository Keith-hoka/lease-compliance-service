# Lease Compliance Service V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deterministic NSW residential lease compliance audit API backed by a temporal legislation store holding the complete point-in-time history of the Residential Tenancies Act 2010 (NSW).

**Architecture:** An ingestion pipeline (Playwright fetcher -> HTML parser -> SCD-2 loader) maintains `sections` rows with `valid_from`/`valid_to` windows. A rules-in-code registry runs deterministic checks against a lease payload at an `as_at` date, resolving citations from the store. A FastAPI app persists audits and serves findings.

**Tech Stack:** Python 3.12+, FastAPI, async SQLAlchemy 2.0, Alembic, PostgreSQL (localhost:5433), selectolax (HTML parsing), Playwright (fetch only), uv, pytest (+pytest-asyncio), ruff, GitHub Actions.

## Global Constraints

- `uv` only: `uv run ...`, `uv add ...` — never `python3`/`pip`.
- Ruff sequence before every push, in this exact order from the repo root: `uv run ruff format .` -> `uv run ruff check --fix .` -> `uv run ruff check .` -> `uv run ruff format --check .`.
- No emojis in code, logs, or prints. Docstrings over inline comments. Do not overengineer.
- Every task ends: full `uv run pytest -q` -> ruff sequence -> commit -> `git push origin main` -> CI green -> report -> WAIT for user approval.
- `jurisdiction` is a first-class dimension everywhere even though only NSW ships.
- Findings are general information, not legal advice; README and API docs carry the disclaimer.
- Tests and CI never fetch the live site; ingestion tests run on committed fixtures.
- Section reads are always point-in-time: `valid_from <= as_at AND (valid_to IS NULL OR as_at < valid_to)`.
- Repo: public `Keith-hoka/lease-compliance-service`. Working directory is always `/Users/keithho/LLMProjects/lease-compliance-service` (cd explicitly; shell cwd resets between commands).

## Grounded site facts (verified in a real browser on 2026-07-24)

These were confirmed against the live site during planning. Re-verify in T5/T6 before locking the fetcher; if any differ, adjust code and note it in the task report.

- Landing page (version list): `https://legislation.nsw.gov.au/view/html/inforce/current/act-2010-042` — a "Point-in-time versions" timeline listing 45 `dd/mm/yyyy` dates from `17/06/2010` to `10/06/2026`.
- Whole-act HTML: `https://legislation.nsw.gov.au/view/whole/html/inforce/{YYYY-MM-DD}/act-2010-042` (also `/current/`). Confirmed for `2024-10-31` -> banner "Historical version for 31 October 2024 to 12 December 2024", 301 sections; current has 335.
- Markup: each section is `div.frag-clause` with `id="sec.<no>"` (e.g. `sec.159`, lettered `sec.159A`). Inside: `div.heading` with text `"159   Payment of bonds"`, then `blockquote.children` with the body. History notes to strip: `.frag-historynote` / `.view-history-note`. Ancestors: `div.frag-division` -> `div.frag-part` (each with its own `div.heading`).
- Anti-bot: plain `curl`/httpx get **403 even with a browser User-Agent** (TLS/bot fingerprinting). A real browser loads fine -> the fetcher uses Playwright headless Chromium.
- Grounded section headings (current text): s159 "Payment of bonds" (bond must not exceed 4 weeks rent — body text confirmed), s33 "Payment of rent by tenant", s23 "Limit on amounts payable by tenant before agreement", s41 "Rent increases", s42 "Rent increases under fixed term agreements", s160 "Other security may not be required". s107 is "Landlord's remedies on abandonment" — NOT break fees; the break-fee basis is pinned from the corpus in T8 and deferred if it lives in the Regulation.

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml`, `.gitignore`, `README.md` | uv project, tool config |
| `.github/workflows/ci.yml` | test + lint jobs, Postgres service |
| `app/main.py` | FastAPI app, routers, `/health` |
| `app/core/config.py` | pydantic-settings (`database_url`, `api_keys`) |
| `app/core/db.py` | engine, `Base`, `get_session` |
| `app/core/auth.py` | `require_api_key` dependency (T10) |
| `app/models/legislation.py` | `Act`, `Section`, `IngestedVersion` |
| `app/models/audit.py` | `Audit` |
| `app/models/__init__.py` | re-exports |
| `alembic/…` | async env + baseline migration |
| `app/ingest/parser.py` | `parse_whole_act(html) -> list[ParsedSection]` |
| `app/ingest/loader.py` | SCD-2 `load_version(...)` |
| `app/ingest/fetcher.py` | version discovery + Playwright fetch + cache |
| `app/ingest/__main__.py` | `uv run python -m app.ingest nsw` CLI |
| `app/services/legislation.py` | `section_at(...)` point-in-time query |
| `app/schemas/lease.py` | `LeaseInput`, `RentIncrease` |
| `app/schemas/audit.py` | `AuditCreate`, `FindingInfo`, `AuditInfo` |
| `app/rules/base.py` | `SectionRef`, `Finding`, `Rule`, `to_weekly_rent` |
| `app/rules/nsw.py` | NSW rule implementations |
| `app/rules/__init__.py` | `ALL_RULES`, `ENGINE_VERSION` |
| `app/rules/engine.py` | `run_audit(...)` |
| `app/routers/audits.py`, `app/routers/legislation.py` | API (T10) |
| `tests/…` | conftest, fixtures, per-module tests |
| `data/raw/` | gitignored fetch cache (~45 x ~1 MB) |

---

### Task 1: Scaffold, health endpoint, CI, GitHub repo

**Files:** Create `pyproject.toml`, `.gitignore`, `README.md`, `app/__init__.py`, `app/main.py`, `tests/__init__.py`, `tests/test_health.py`, `.github/workflows/ci.yml`.

**Interfaces:** Produces `app.main:app` (FastAPI instance) that every later task mounts onto; CI that runs `pytest` + ruff on push.

- [x] **Step 1: Init project + deps**

```bash
cd /Users/keithho/LLMProjects/lease-compliance-service
uv init --name lease-compliance-service --python 3.12
rm main.py 2>/dev/null || true
uv add fastapi "uvicorn[standard]" "sqlalchemy[asyncio]" asyncpg alembic pydantic-settings selectolax
uv add --dev pytest pytest-asyncio httpx ruff
```

- [x] **Step 2: Tool config** — append to `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

`.gitignore`:

```
.venv/
__pycache__/
*.pyc
.env
data/raw/
```

`README.md`:

```markdown
# lease-compliance-service

Deterministic NSW residential lease compliance audits with a temporal
legislation store. Output is general information, not legal advice.
```

- [x] **Step 3: Failing test** — `tests/test_health.py`:

```python
from httpx import ASGITransport, AsyncClient

from app.main import app


async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [x] **Step 4: Run -> fail** — `uv run pytest -q` -> ImportError (no `app.main`).

- [x] **Step 5: Implement** — `app/__init__.py` empty; `app/main.py`:

```python
from fastapi import FastAPI

app = FastAPI(title="Lease Compliance Service")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
```

- [x] **Step 6: Run -> pass.**

- [x] **Step 7: CI** — `.github/workflows/ci.yml`:

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: lease_compliance_test
        ports: ["5433:5432"]
        options: >-
          --health-cmd "pg_isready -U postgres" --health-interval 5s
          --health-timeout 5s --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --dev
      - run: uv run pytest -q
        env:
          TEST_DATABASE_URL: postgresql+asyncpg://postgres:postgres@localhost:5433/lease_compliance_test

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
```

- [x] **Step 8: Ruff sequence, commit, create repo, push**

```bash
cd /Users/keithho/LLMProjects/lease-compliance-service
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Scaffold the service with a health endpoint and CI"
gh repo create Keith-hoka/lease-compliance-service --public --source . --push
```

- [x] **Step 9: CI green** (`gh run watch <id> --exit-status`). Report and WAIT.

---

### Task 2: Settings, DB, models, baseline migration, conftest

**Files:** Create `app/core/__init__.py`, `app/core/config.py`, `app/core/db.py`, `app/models/__init__.py`, `app/models/legislation.py`, `app/models/audit.py`, `alembic.ini` + `alembic/env.py` + baseline migration, `tests/conftest.py`, `tests/test_models.py`.

**Interfaces:** Produces `Base`, `get_session`, `settings`; models `Act(id, jurisdiction, slug, title, source_url)`, `Section(id, act_id, section_no, heading, body_text, part, division, valid_from, valid_to, source_version_date, content_hash)`, `IngestedVersion(act_id, version_date)`, `Audit(id, jurisdiction, as_at, input, findings, engine_version, created_at)`; test fixtures `db_session`, `client`.

- [x] **Step 1: Local databases** (match CI creds; adjust user/password to the local Postgres on 5433 if different and note it in the report):

```bash
psql -h localhost -p 5433 -U postgres -c "CREATE DATABASE lease_compliance;"
psql -h localhost -p 5433 -U postgres -c "CREATE DATABASE lease_compliance_test;"
```

- [x] **Step 2: Failing test** — `tests/test_models.py`:

```python
import uuid
from datetime import date

from sqlalchemy import select

from app.models import Act, Audit, IngestedVersion, Section


async def test_legislation_round_trip(db_session):
    act = Act(
        jurisdiction="NSW",
        slug="act-2010-042",
        title="Residential Tenancies Act 2010",
        source_url="https://legislation.nsw.gov.au/view/html/inforce/current/act-2010-042",
    )
    db_session.add(act)
    await db_session.flush()
    db_session.add(
        Section(
            act_id=act.id,
            section_no="159",
            heading="Payment of bonds",
            body_text="A landlord must not require a bond exceeding 4 weeks rent.",
            part="Part 8 Rental bonds",
            division="Division 1 Payment of bonds",
            valid_from=date(2011, 1, 31),
            valid_to=None,
            source_version_date=date(2011, 1, 31),
            content_hash="abc123",
        )
    )
    db_session.add(IngestedVersion(act_id=act.id, version_date=date(2011, 1, 31)))
    await db_session.commit()

    stored = (
        await db_session.execute(select(Section).where(Section.section_no == "159"))
    ).scalar_one()
    assert stored.valid_to is None
    assert stored.part == "Part 8 Rental bonds"


async def test_audit_round_trip(db_session):
    audit = Audit(
        jurisdiction="NSW",
        as_at=date(2026, 7, 24),
        input={"rent_amount": "600"},
        findings=[{"rule_id": "nsw.bond_max_4_weeks", "verdict": "green"}],
        engine_version="1.0.0",
    )
    db_session.add(audit)
    await db_session.commit()
    stored = (await db_session.execute(select(Audit))).scalar_one()
    assert stored.findings[0]["verdict"] == "green"
    assert isinstance(stored.id, uuid.UUID)
```

- [x] **Step 3: Run -> fail** (ImportError).

- [x] **Step 4: Implement config/db** — `app/core/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service configuration, overridable via environment or .env."""

    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/lease_compliance"
    api_keys: str = ""


settings = Settings()
```

`app/core/db.py`:

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
```

- [x] **Step 5: Models** — `app/models/legislation.py`:

```python
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
```

`app/models/audit.py`:

```python
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, String, func
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

`app/models/__init__.py`:

```python
from app.models.audit import Audit
from app.models.legislation import Act, IngestedVersion, Section

__all__ = ["Act", "Audit", "IngestedVersion", "Section"]
```

- [x] **Step 6: Alembic** — `uv run alembic init alembic`; make `alembic/env.py` async and import `Base`:

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.core.db import Base
from app.models import *  # noqa: F401,F403  (register tables)

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=settings.database_url, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(config.get_section(config.config_ini_section, {}))
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

Then `uv run alembic revision -m "baseline"` and hand-write `upgrade` (create `acts`, `sections`, `ingested_versions`, `audits` with the exact columns above, plus indexes `ix_acts_jurisdiction`, `ix_sections_act_id`, and a composite `ix_sections_act_no_from` on `(act_id, section_no, valid_from)`) and `downgrade` (drop the four tables in reverse order). No PG enums in this schema. Verify `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`.

- [x] **Step 7: conftest** — `tests/conftest.py`:

```python
import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base, get_session
from app.main import app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5433/lease_compliance_test",
)


@pytest.fixture
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def client(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
```

- [x] **Step 8: Run -> pass; full suite; ruff; commit** (`Add settings, models and the baseline migration`); push; CI green. Report and WAIT.

---

### Task 3: Whole-act HTML parser

**Files:** Create `app/ingest/__init__.py`, `app/ingest/parser.py`, `tests/fixtures/mini_act.html`, `tests/test_parser.py`.

**Interfaces:** Produces `ParsedSection(section_no, heading, body_text, part, division)` (frozen dataclass, all `str`, `part`/`division` `str | None`) and `parse_whole_act(html: str) -> list[ParsedSection]`. The loader (T4) consumes this list.

- [x] **Step 1: Fixture** — `tests/fixtures/mini_act.html` (mirrors the verified live markup: `frag-clause` ids, `heading` + `blockquote.children`, history notes, part/division ancestors, a lettered section):

```html
<div class="content">
  <div class="frag-part">
    <div class="heading">Part 1 Preliminary</div>
    <div class="frag-clause" id="sec.1">
      <div class="heading">1   Name of Act</div>
      <blockquote class="children"><p>This Act is the Residential Tenancies Act 2010.</p></blockquote>
    </div>
  </div>
  <div class="frag-part">
    <div class="heading">Part 8 Rental bonds</div>
    <div class="frag-division">
      <div class="heading">Division 1 Payment of bonds</div>
      <div class="frag-clause" id="sec.159">
        <div class="heading">159   Payment of bonds</div>
        <blockquote class="children">
          <p>(1) A landlord must not require a rental bond exceeding 4 weeks rent.</p>
          <div class="frag-historynote view-history-note">Am 2018 No 58, Sch 1.</div>
        </blockquote>
      </div>
      <div class="frag-clause" id="sec.159A">
        <div class="heading">159A   Lettered example</div>
        <blockquote class="children"><p>Lettered body text.</p></blockquote>
      </div>
    </div>
  </div>
</div>
```

- [x] **Step 2: Failing tests** — `tests/test_parser.py`:

```python
from pathlib import Path

from app.ingest.parser import parse_whole_act

HTML = (Path(__file__).parent / "fixtures" / "mini_act.html").read_text()


def test_parses_all_sections_in_order():
    sections = parse_whole_act(HTML)
    assert [s.section_no for s in sections] == ["1", "159", "159A"]


def test_heading_and_body():
    s159 = parse_whole_act(HTML)[1]
    assert s159.heading == "Payment of bonds"
    assert "exceeding 4 weeks rent" in s159.body_text


def test_history_notes_stripped():
    s159 = parse_whole_act(HTML)[1]
    assert "Am 2018" not in s159.body_text


def test_part_and_division_labels():
    sections = parse_whole_act(HTML)
    assert sections[0].part == "Part 1 Preliminary"
    assert sections[0].division is None
    assert sections[1].part == "Part 8 Rental bonds"
    assert sections[1].division == "Division 1 Payment of bonds"
```

- [x] **Step 3: Run -> fail** (ImportError).

- [x] **Step 4: Implement** — `app/ingest/parser.py`:

```python
import re
from dataclasses import dataclass

from selectolax.parser import HTMLParser


@dataclass(frozen=True)
class ParsedSection:
    section_no: str
    heading: str
    body_text: str
    part: str | None
    division: str | None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _ancestor_heading(node, ancestor_class: str) -> str | None:
    current = node.parent
    while current is not None:
        classes = current.attributes.get("class", "") or ""
        if ancestor_class in classes.split():
            heading = current.css_first(":scope > .heading") or current.css_first(".heading")
            return _clean(heading.text()) if heading else None
        current = current.parent
    return None


def parse_whole_act(html: str) -> list[ParsedSection]:
    """Extract the act's numbered sections from a whole-act HTML page."""
    tree = HTMLParser(html)
    sections: list[ParsedSection] = []
    for clause in tree.css("div.frag-clause"):
        node_id = clause.attributes.get("id", "") or ""
        if not node_id.startswith("sec."):
            continue
        section_no = node_id.removeprefix("sec.")
        for note in clause.css(".frag-historynote, .view-history-note"):
            note.decompose()
        heading_node = clause.css_first(".heading")
        raw_heading = _clean(heading_node.text()) if heading_node else ""
        heading = _clean(re.sub(rf"^{re.escape(section_no)}\s+", "", raw_heading))
        body_node = clause.css_first("blockquote.children")
        body_text = _clean(body_node.text()) if body_node else ""
        sections.append(
            ParsedSection(
                section_no=section_no,
                heading=heading,
                body_text=body_text,
                part=_ancestor_heading(clause, "frag-part"),
                division=_ancestor_heading(clause, "frag-division"),
            )
        )
    return sections
```

Note: if selectolax's `:scope >` selector is unsupported in the installed version, drop to iterating `current.iter()` children for a `heading` class — keep the first matching child element.

- [x] **Step 5: Run -> pass; full suite; ruff; commit** (`Add the whole-act HTML parser`); push; CI green. Report and WAIT.

---

### Task 4: SCD-2 loader + point-in-time query

**Files:** Create `app/ingest/loader.py`, `app/services/__init__.py`, `app/services/legislation.py`, `tests/test_loader.py`.

**Interfaces:** Consumes `ParsedSection` (T3), models (T2). Produces:
- `content_hash(section: ParsedSection) -> str` (sha256 hex of `heading + "\n" + body_text`).
- `async load_version(session, act_id, version_date: date, sections: list[ParsedSection]) -> LoadStats` where `LoadStats(inserted: int, closed: int, skipped: bool)`.
- `async section_at(session, act_slug: str, section_no: str, as_at: date) -> Section | None`.

- [x] **Step 1: Failing tests** — `tests/test_loader.py`:

```python
from datetime import date

from app.ingest.loader import LoadStats, load_version
from app.ingest.parser import ParsedSection
from app.models import Act
from app.services.legislation import section_at


def _ps(no, heading, body):
    return ParsedSection(no, heading, body, part=None, division=None)


async def _act(db_session) -> Act:
    act = Act(jurisdiction="NSW", slug="act-test", title="Test Act", source_url="http://x")
    db_session.add(act)
    await db_session.flush()
    return act


V1 = date(2010, 6, 17)
V2 = date(2020, 3, 23)
V3 = date(2025, 5, 19)


async def test_scd2_windows(db_session):
    act = await _act(db_session)
    s1 = await load_version(
        db_session, act.id, V1, [_ps("1", "Name", "Old body"), _ps("2", "Two", "B")]
    )
    assert s1 == LoadStats(inserted=2, closed=0, skipped=False)
    s2 = await load_version(
        db_session, act.id, V2, [_ps("1", "Name", "New body"), _ps("2", "Two", "B")]
    )
    assert s2 == LoadStats(inserted=1, closed=1, skipped=False)
    await db_session.commit()

    old = await section_at(db_session, "act-test", "1", date(2015, 1, 1))
    new = await section_at(db_session, "act-test", "1", date(2024, 1, 1))
    assert old.body_text == "Old body" and old.valid_to == V2
    assert new.body_text == "New body" and new.valid_to is None
    unchanged = await section_at(db_session, "act-test", "2", date(2024, 1, 1))
    assert unchanged.valid_from == V1 and unchanged.valid_to is None


async def test_removed_and_added_sections(db_session):
    act = await _act(db_session)
    await load_version(db_session, act.id, V1, [_ps("1", "Name", "A"), _ps("9", "Gone", "X")])
    await load_version(db_session, act.id, V2, [_ps("1", "Name", "A"), _ps("10", "New", "Y")])
    await db_session.commit()
    assert (await section_at(db_session, "act-test", "9", date(2024, 1, 1))) is None
    assert (await section_at(db_session, "act-test", "9", date(2015, 1, 1))).valid_to == V2
    assert (await section_at(db_session, "act-test", "10", date(2024, 1, 1))).valid_from == V2


async def test_idempotent_rerun(db_session):
    act = await _act(db_session)
    await load_version(db_session, act.id, V1, [_ps("1", "Name", "A")])
    again = await load_version(db_session, act.id, V1, [_ps("1", "Name", "A")])
    assert again.skipped is True


async def test_before_first_version_is_none(db_session):
    act = await _act(db_session)
    await load_version(db_session, act.id, V1, [_ps("1", "Name", "A")])
    assert (await section_at(db_session, "act-test", "1", date(2009, 1, 1))) is None
```

- [x] **Step 2: Run -> fail.**

- [x] **Step 3: Implement** — `app/ingest/loader.py`:

```python
import hashlib
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.parser import ParsedSection
from app.models import IngestedVersion, Section


@dataclass(frozen=True)
class LoadStats:
    inserted: int
    closed: int
    skipped: bool


def content_hash(section: ParsedSection) -> str:
    return hashlib.sha256(f"{section.heading}\n{section.body_text}".encode()).hexdigest()


async def load_version(
    session: AsyncSession, act_id, version_date: date, sections: list[ParsedSection]
) -> LoadStats:
    """Apply one point-in-time version to the store (SCD-2, idempotent)."""
    already = await session.get(IngestedVersion, (act_id, version_date))
    if already is not None:
        return LoadStats(inserted=0, closed=0, skipped=True)

    open_rows = (
        (
            await session.execute(
                select(Section).where(Section.act_id == act_id, Section.valid_to.is_(None))
            )
        )
        .scalars()
        .all()
    )
    current = {row.section_no: row for row in open_rows}
    incoming = {s.section_no: s for s in sections}

    inserted = closed = 0
    for no, row in current.items():
        replacement = incoming.get(no)
        if replacement is None or content_hash(replacement) != row.content_hash:
            row.valid_to = version_date
            closed += 1
    for no, parsed in incoming.items():
        existing = current.get(no)
        if existing is not None and content_hash(parsed) == existing.content_hash:
            continue
        session.add(
            Section(
                act_id=act_id,
                section_no=no,
                heading=parsed.heading,
                body_text=parsed.body_text,
                part=parsed.part,
                division=parsed.division,
                valid_from=version_date,
                valid_to=None,
                source_version_date=version_date,
                content_hash=content_hash(parsed),
            )
        )
        inserted += 1
    session.add(IngestedVersion(act_id=act_id, version_date=version_date))
    await session.flush()
    return LoadStats(inserted=inserted, closed=closed, skipped=False)
```

`app/services/legislation.py`:

```python
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Act, Section


async def section_at(
    session: AsyncSession, act_slug: str, section_no: str, as_at: date
) -> Section | None:
    """The section text in force at as_at, or None."""
    query = (
        select(Section)
        .join(Act, Act.id == Section.act_id)
        .where(
            Act.slug == act_slug,
            Section.section_no == section_no,
            Section.valid_from <= as_at,
            (Section.valid_to.is_(None)) | (Section.valid_to > as_at),
        )
    )
    return (await session.execute(query)).scalar_one_or_none()
```

- [x] **Step 4: Run -> pass; full suite; ruff; commit** (`Add the SCD-2 loader and point-in-time query`); push; CI green. Report and WAIT.

---

### Task 5: Version discovery + Playwright fetcher with cache

**Files:** Create `app/ingest/fetcher.py`, `tests/fixtures/landing_timeline.html`, `tests/test_fetcher.py`. Dev dep: Playwright.

**Interfaces:** Produces `parse_version_dates(html: str) -> list[date]` (ascending, unique) and `fetch_versions(slug: str, dates: list[date], cache_dir: Path, delay_seconds: float = 2.0) -> list[Path]` (Playwright; skips dates already cached; returns cached file paths in date order). Constants `LANDING_URL_TEMPLATE`, `WHOLE_ACT_URL_TEMPLATE`.

- [ ] **Step 1: Install Playwright**

```bash
cd /Users/keithho/LLMProjects/lease-compliance-service
uv add --dev playwright
uv run playwright install chromium
```

- [ ] **Step 2: Fixture** — `tests/fixtures/landing_timeline.html` (shape of the landing page's timeline; exact container class re-verified live in Step 6):

```html
<div class="timeline">
  <h2 class="timeline-heading">Point-in-time versions</h2>
  <ul>
    <li><a>17/06/2010</a></li>
    <li><a>31/10/2024</a></li>
    <li><a>10/06/2026</a></li>
  </ul>
</div>
<p>accessed 24 July 2026 - this date must not be parsed as a version</p>
```

- [ ] **Step 3: Failing tests** — `tests/test_fetcher.py`:

```python
from datetime import date
from pathlib import Path

from app.ingest.fetcher import parse_version_dates

HTML = (Path(__file__).parent / "fixtures" / "landing_timeline.html").read_text()


def test_parses_timeline_dates_ascending():
    assert parse_version_dates(HTML) == [date(2010, 6, 17), date(2024, 10, 31), date(2026, 6, 10)]


def test_ignores_dates_outside_timeline():
    assert date(2026, 7, 24) not in parse_version_dates(HTML)
```

- [ ] **Step 4: Run -> fail.**

- [ ] **Step 5: Implement** — `app/ingest/fetcher.py`:

```python
import re
import time
from datetime import date, datetime
from pathlib import Path

from selectolax.parser import HTMLParser

LANDING_URL_TEMPLATE = "https://legislation.nsw.gov.au/view/html/inforce/current/{slug}"
WHOLE_ACT_URL_TEMPLATE = "https://legislation.nsw.gov.au/view/whole/html/inforce/{version}/{slug}"


def parse_version_dates(html: str) -> list[date]:
    """The point-in-time version dates listed on an act landing page."""
    tree = HTMLParser(html)
    timeline = tree.css_first(".timeline")
    text = timeline.text() if timeline else ""
    dates = {
        datetime.strptime(m, "%d/%m/%Y").date() for m in re.findall(r"\b\d{2}/\d{2}/\d{4}\b", text)
    }
    return sorted(dates)


def fetch_landing(slug: str) -> str:
    """Fetch the act landing page HTML with a real browser."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(LANDING_URL_TEMPLATE.format(slug=slug), wait_until="domcontentloaded")
        page.wait_for_selector(".timeline", timeout=30000)
        html = page.content()
        browser.close()
    return html


def fetch_versions(
    slug: str, dates: list[date], cache_dir: Path, delay_seconds: float = 2.0
) -> list[Path]:
    """Fetch each version's whole-act HTML into cache_dir; skip cached dates."""
    from playwright.sync_api import sync_playwright

    cache_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    missing = [d for d in dates if not (cache_dir / f"{d.isoformat()}.html").exists()]
    for d in dates:
        paths.append(cache_dir / f"{d.isoformat()}.html")
    if not missing:
        return paths
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for d in missing:
            url = WHOLE_ACT_URL_TEMPLATE.format(version=d.isoformat(), slug=slug)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector("div.frag-clause", timeout=60000)
            (cache_dir / f"{d.isoformat()}.html").write_text(page.content())
            time.sleep(delay_seconds)
        browser.close()
    return paths
```

- [ ] **Step 6: Live verification (one page, manual)** — run a one-off snippet to confirm the real landing page's timeline container and that a dated whole-act URL renders `div.frag-clause` (pattern verified in planning; this re-checks at execution time):

```bash
uv run python -c "
from pathlib import Path
from app.ingest.fetcher import fetch_landing, parse_version_dates
html = fetch_landing('act-2010-042')
dates = parse_version_dates(html)
print(len(dates), dates[:2], dates[-1])
Path('data/raw/landing-check.html').parent.mkdir(parents=True, exist_ok=True)
Path('data/raw/landing-check.html').write_text(html)
"
```

Expected: `45 [datetime.date(2010, 6, 17), ...] datetime.date(2026, 6, 10)` (count may exceed 45 if new versions have published). If the timeline selector differs, adjust `parse_version_dates` and the fixture to the real container class and re-run the tests.

- [ ] **Step 7: Run tests -> pass; ruff; commit** (`Add version discovery and the Playwright fetcher`); push; CI green. Report and WAIT.

---

### Task 6: Ingest CLI + full NSW history run

**Files:** Create `app/ingest/__main__.py`; modify `README.md` (licensing/attribution note).

**Interfaces:** Consumes fetcher (T5), parser (T3), loader (T4). Produces the populated local store: all point-in-time versions of `act-2010-042` loaded chronologically.

- [ ] **Step 1: CLI** — `app/ingest/__main__.py`:

```python
"""Ingest an act's full point-in-time history: fetch, parse, load.

Usage: uv run python -m app.ingest nsw [--limit-versions N]
"""

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select

from app.core.db import async_session_factory
from app.ingest.fetcher import (
    LANDING_URL_TEMPLATE,
    fetch_landing,
    fetch_versions,
    parse_version_dates,
)
from app.ingest.loader import load_version
from app.ingest.parser import parse_whole_act
from app.models import Act

NSW_ACT = {
    "jurisdiction": "NSW",
    "slug": "act-2010-042",
    "title": "Residential Tenancies Act 2010",
}


async def ensure_act(session) -> Act:
    act = (
        await session.execute(select(Act).where(Act.slug == NSW_ACT["slug"]))
    ).scalar_one_or_none()
    if act is None:
        act = Act(**NSW_ACT, source_url=LANDING_URL_TEMPLATE.format(slug=NSW_ACT["slug"]))
        session.add(act)
        await session.flush()
    return act


async def load_all(paths) -> None:
    async with async_session_factory() as session:
        act = await ensure_act(session)
        for path in paths:
            version_date = __import__("datetime").date.fromisoformat(path.stem)
            sections = parse_whole_act(path.read_text())
            stats = await load_version(session, act.id, version_date, sections)
            print(f"{version_date}: sections={len(sections)} {stats}")
        await session.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jurisdiction", choices=["nsw"])
    parser.add_argument("--limit-versions", type=int, default=None)
    args = parser.parse_args()

    landing = fetch_landing(NSW_ACT["slug"])
    dates = parse_version_dates(landing)
    if args.limit_versions:
        dates = dates[: args.limit_versions]
    cache = Path("data/raw/nsw") / NSW_ACT["slug"]
    paths = fetch_versions(NSW_ACT["slug"], dates, cache)
    asyncio.run(load_all(paths))


main()
```

(Replace the inline `__import__("datetime")` with a top-level `from datetime import date` and `date.fromisoformat` when writing the real file — shown inline here only to keep the listing compact. The real file imports at top per E402 conventions.)

- [ ] **Step 2: Trial run** — `uv run python -m app.ingest nsw --limit-versions 3`. Expected: three version lines, first with a few hundred inserts, later ones with small insert/close counts.

- [ ] **Step 3: Full run** — `uv run python -m app.ingest nsw` (~45 fetches x 2 s delay + parse/load; minutes). Re-run afterwards -> every line `skipped=True` (idempotency proof).

- [ ] **Step 4: Spot-check verification** (documented in the task report with real output):

```bash
uv run python -c "
import asyncio
from datetime import date
from app.core.db import async_session_factory
from app.services.legislation import section_at

async def main():
    async with async_session_factory() as s:
        for as_at in (date(2012,1,1), date(2024,10,31), date(2026,7,1)):
            row = await section_at(s, 'act-2010-042', '159', as_at)
            print(as_at, row.heading if row else None, row.valid_from if row else '-')
asyncio.run(main())
"
```

Expected: s159 "Payment of bonds" resolves at all three dates (windows may differ). Also count open sections now vs at 2024-10-31 (expect ~335 vs ~301, matching the browser evidence) and list sections whose `valid_to` equals a 2024-10-31 or 2025-05-19 reform date — these are the temporal-test candidates for T9.

- [ ] **Step 5: README licensing note** — append: source is the NSW legislation website (Parliamentary Counsel's Office); stored text carries source URLs and version dates for attribution; check and record the site's current licence statement (NSW legislation is generally published under Creative Commons — cite the exact licence text found).

- [ ] **Step 6: Full suite; ruff; commit** (`Add the ingest CLI and load the full NSW history`); push; CI green. Report (include run output summary) and WAIT.

---

### Task 7: Lease schema, rule base, engine, first two rules

**Files:** Create `app/schemas/__init__.py`, `app/schemas/lease.py`, `app/rules/__init__.py`, `app/rules/base.py`, `app/rules/engine.py`, `app/rules/nsw.py`, `tests/test_rules_nsw.py`, `tests/test_engine.py`.

**Interfaces:** Produces:
- `LeaseInput` (pydantic): required `rent_amount: Decimal`, `rent_frequency: Literal["weekly","fortnightly","monthly"]`, `start_date: date`; optional `end_date`, `bond_amount`, `rent_in_advance_amount`, `holding_deposit_amount`, `other_security_amount`, `break_fee_amount` (all `Decimal | None`), `rent_increases: list[RentIncrease] | None` (`RentIncrease(effective_on: date, new_amount: Decimal, notice_given_on: date | None)`), `fixed_term_increase_in_agreement: bool | None`. Validator: `end_date` must be after `start_date` (422).
- `app/rules/base.py`: `SectionRef(act_slug: str, section_no: str)`; `Finding` (pydantic: `rule_id, verdict: Literal["red","green","skipped"], summary: str, evidence: dict, citations: list[Citation], skip_reason: str | None`); `Citation(act: str, section_no: str, as_at: date, section_id: uuid.UUID)`; `Rule` dataclass (`rule_id, jurisdiction, citations: list[SectionRef], applies_from: date | None, applies_to: date | None, required_inputs: list[str], check: Callable[[LeaseInput], CheckResult]`) where `CheckResult = tuple[Literal["red","green"], str, dict]` (verdict, summary, evidence); `to_weekly_rent(amount: Decimal, frequency: str) -> Decimal` (weekly: amount; fortnightly: amount/2; monthly: amount*12/52; quantized to cents).
- `app/rules/engine.py`: `async run_audit(session, jurisdiction: str, as_at: date, lease: LeaseInput) -> list[Finding]`.
- `app/rules/__init__.py`: `ALL_RULES: list[Rule]`, `ENGINE_VERSION = "1.0.0"`.
- First two rules in `app/rules/nsw.py`: `nsw.bond_max_4_weeks` (s159) and `nsw.rent_in_advance_max` (s33).

- [ ] **Step 1: Pin statutory text** — before writing the rules, read the two sections from the ingested corpus and paste the operative sentences into each rule's docstring:

```bash
uv run python -c "
import asyncio
from datetime import date
from app.core.db import async_session_factory
from app.services.legislation import section_at

async def main():
    async with async_session_factory() as s:
        for no in ('159', '33'):
            row = await section_at(s, 'act-2010-042', no, date(2026, 7, 24))
            print(no, '|', row.heading, '|', row.body_text[:400])
asyncio.run(main())
"
```

Confirm s159 contains the 4-weeks bond cap and locate the exact advance-rent limit wording in s33 (expected: rent in advance capped at 2 weeks for weekly-rent agreements; if the cap turns out to live in a different section, update the `SectionRef` accordingly and note it in the report).

- [ ] **Step 2: Failing tests** — `tests/test_rules_nsw.py` (rule-level, via the engine against the ingested store — these run against the real corpus loaded in T6; guard with a fixture that skips if the store is empty so CI without the corpus still passes):

```python
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Act
from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput

AS_AT = date(2026, 7, 24)


@pytest.fixture
async def corpus_session():
    """A session against the dev store; skip when the corpus is not loaded."""
    from app.core.db import async_session_factory

    async with async_session_factory() as session:
        act = (
            await session.execute(select(Act).where(Act.slug == "act-2010-042"))
        ).scalar_one_or_none()
        if act is None:
            pytest.skip("NSW corpus not ingested")
        yield session


def lease(**kw) -> LeaseInput:
    base = dict(rent_amount=Decimal("600"), rent_frequency="weekly", start_date=date(2026, 1, 1))
    base.update(kw)
    return LeaseInput(**base)


async def test_bond_over_four_weeks_is_red(corpus_session):
    findings = await run_audit(corpus_session, "NSW", AS_AT, lease(bond_amount=Decimal("3000")))
    finding = next(f for f in findings if f.rule_id == "nsw.bond_max_4_weeks")
    assert finding.verdict == "red"
    assert finding.citations[0].section_no == "159"
    assert finding.evidence["computed"]["max_bond"] == "2400.00"


async def test_bond_at_cap_is_green(corpus_session):
    findings = await run_audit(corpus_session, "NSW", AS_AT, lease(bond_amount=Decimal("2400")))
    assert next(f for f in findings if f.rule_id == "nsw.bond_max_4_weeks").verdict == "green"


async def test_missing_bond_is_skipped(corpus_session):
    findings = await run_audit(corpus_session, "NSW", AS_AT, lease())
    finding = next(f for f in findings if f.rule_id == "nsw.bond_max_4_weeks")
    assert finding.verdict == "skipped"
    assert "bond_amount" in finding.skip_reason
```

`tests/test_engine.py` (engine mechanics on a synthetic store — no corpus dependency):

```python
from datetime import date
from decimal import Decimal

from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act
from app.rules.base import Rule, SectionRef
from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput


def lease() -> LeaseInput:
    return LeaseInput(
        rent_amount=Decimal("500"), rent_frequency="weekly", start_date=date(2026, 1, 1)
    )


async def test_rule_with_section_not_in_force_is_skipped(db_session, monkeypatch):
    act = Act(jurisdiction="NSW", slug="act-2010-042", title="T", source_url="x")
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session, act.id, date(2020, 1, 1), [ParsedSection("1", "One", "Body", None, None)]
    )

    fake = Rule(
        rule_id="nsw.fake",
        jurisdiction="NSW",
        citations=[SectionRef("act-2010-042", "999")],
        applies_from=None,
        applies_to=None,
        required_inputs=[],
        check=lambda lease: ("green", "ok", {}),
    )
    monkeypatch.setattr("app.rules.engine.ALL_RULES", [fake])
    findings = await run_audit(db_session, "NSW", date(2026, 1, 1), lease())
    assert findings[0].verdict == "skipped"
    assert "not in force" in findings[0].skip_reason


async def test_rule_outside_applies_window_is_skipped(db_session, monkeypatch):
    act = Act(jurisdiction="NSW", slug="act-2010-042", title="T", source_url="x")
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session, act.id, date(2020, 1, 1), [ParsedSection("1", "One", "Body", None, None)]
    )
    fake = Rule(
        rule_id="nsw.fake",
        jurisdiction="NSW",
        citations=[SectionRef("act-2010-042", "1")],
        applies_from=date(2024, 1, 1),
        applies_to=None,
        required_inputs=[],
        check=lambda lease: ("green", "ok", {}),
    )
    monkeypatch.setattr("app.rules.engine.ALL_RULES", [fake])
    findings = await run_audit(db_session, "NSW", date(2022, 1, 1), lease())
    assert findings[0].verdict == "skipped"
    assert "not active" in findings[0].skip_reason
```

- [ ] **Step 3: Run -> fail.**

- [ ] **Step 4: Implement** — `app/schemas/lease.py`:

```python
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, model_validator


class RentIncrease(BaseModel):
    effective_on: date
    new_amount: Decimal
    notice_given_on: date | None = None


class LeaseInput(BaseModel):
    rent_amount: Decimal
    rent_frequency: Literal["weekly", "fortnightly", "monthly"]
    start_date: date
    end_date: date | None = None
    bond_amount: Decimal | None = None
    rent_in_advance_amount: Decimal | None = None
    holding_deposit_amount: Decimal | None = None
    other_security_amount: Decimal | None = None
    break_fee_amount: Decimal | None = None
    rent_increases: list[RentIncrease] | None = None
    fixed_term_increase_in_agreement: bool | None = None

    @model_validator(mode="after")
    def end_after_start(self) -> "LeaseInput":
        if self.end_date is not None and self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self
```

`app/rules/base.py`:

```python
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, Literal

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
    verdict: Literal["red", "green", "skipped"]
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
```

`app/rules/nsw.py` (docstrings get the pinned statutory sentences from Step 1):

```python
from datetime import date
from decimal import Decimal

from app.rules.base import CheckResult, Rule, SectionRef, to_weekly_rent
from app.schemas.lease import LeaseInput

ACT = "act-2010-042"


def _bond_check(lease: LeaseInput) -> CheckResult:
    """s159: a rental bond must not exceed 4 weeks rent."""
    weekly = to_weekly_rent(lease.rent_amount, lease.rent_frequency)
    max_bond = (weekly * 4).quantize(Decimal("0.01"))
    evidence = {
        "fields": {"bond_amount": str(lease.bond_amount)},
        "computed": {"weekly_rent": str(weekly), "max_bond": str(max_bond)},
    }
    if lease.bond_amount > max_bond:
        return (
            "red",
            f"Bond of {lease.bond_amount} exceeds the 4-week maximum of {max_bond}.",
            evidence,
        )
    return (
        "green",
        f"Bond of {lease.bond_amount} is within the 4-week maximum of {max_bond}.",
        evidence,
    )


def _advance_check(lease: LeaseInput) -> CheckResult:
    """s33: rent in advance capped (pinned wording pasted here in Step 1)."""
    weekly = to_weekly_rent(lease.rent_amount, lease.rent_frequency)
    cap = (weekly * 2).quantize(Decimal("0.01"))
    evidence = {
        "fields": {"rent_in_advance_amount": str(lease.rent_in_advance_amount)},
        "computed": {"weekly_rent": str(weekly), "max_advance": str(cap)},
    }
    if lease.rent_in_advance_amount > cap:
        return (
            "red",
            f"Rent in advance of {lease.rent_in_advance_amount} exceeds the cap of {cap}.",
            evidence,
        )
    return (
        "green",
        f"Rent in advance of {lease.rent_in_advance_amount} is within the cap of {cap}.",
        evidence,
    )


NSW_RULES = [
    Rule(
        rule_id="nsw.bond_max_4_weeks",
        jurisdiction="NSW",
        citations=[SectionRef(ACT, "159")],
        applies_from=date(2011, 1, 31),
        applies_to=None,
        required_inputs=["bond_amount"],
        check=_bond_check,
    ),
    Rule(
        rule_id="nsw.rent_in_advance_max",
        jurisdiction="NSW",
        citations=[SectionRef(ACT, "33")],
        applies_from=date(2011, 1, 31),
        applies_to=None,
        required_inputs=["rent_in_advance_amount"],
        check=_advance_check,
    ),
]
```

(The two-week advance cap and both `applies_from` commencement dates are pinned in Step 1 from the corpus — the Act commenced 31 January 2011; correct these constants if the corpus shows otherwise, and note corrections in the report.)

`app/rules/__init__.py`:

```python
from app.rules.nsw import NSW_RULES

ENGINE_VERSION = "1.0.0"
ALL_RULES = [*NSW_RULES]
```

`app/rules/engine.py`:

```python
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Act
from app.rules import ALL_RULES
from app.rules.base import Citation, Finding
from app.schemas.lease import LeaseInput
from app.services.legislation import section_at


async def run_audit(
    session: AsyncSession, jurisdiction: str, as_at: date, lease: LeaseInput
) -> list[Finding]:
    """Run every registered rule for the jurisdiction against the lease at as_at."""
    findings: list[Finding] = []
    for rule in ALL_RULES:
        if rule.jurisdiction != jurisdiction:
            continue
        if (rule.applies_from and as_at < rule.applies_from) or (
            rule.applies_to and as_at >= rule.applies_to
        ):
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    verdict="skipped",
                    summary="Rule not active at the audit date.",
                    skip_reason=f"rule not active at {as_at}",
                )
            )
            continue

        citations: list[Citation] = []
        missing_section = None
        for ref in rule.citations:
            section = await section_at(session, ref.act_slug, ref.section_no, as_at)
            if section is None:
                missing_section = ref
                break
            act = await session.get(Act, section.act_id)
            citations.append(
                Citation(
                    act=act.title, section_no=ref.section_no, as_at=as_at, section_id=section.id
                )
            )
        if missing_section is not None:
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    verdict="skipped",
                    summary="Statutory basis not in force at the audit date.",
                    skip_reason=f"section {missing_section.section_no} not in force at {as_at}",
                )
            )
            continue

        absent = [f for f in rule.required_inputs if getattr(lease, f) is None]
        if absent:
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    verdict="skipped",
                    summary="Insufficient input to run this check.",
                    citations=citations,
                    skip_reason="missing input: " + ", ".join(absent),
                )
            )
            continue

        verdict, summary, evidence = rule.check(lease)
        findings.append(
            Finding(
                rule_id=rule.rule_id,
                verdict=verdict,
                summary=summary,
                evidence=evidence,
                citations=citations,
            )
        )
    return findings
```

- [ ] **Step 5: Run -> pass** (corpus tests pass locally against the loaded store; they skip on CI where no corpus exists — engine tests cover CI).
- [ ] **Step 6: Full suite; ruff; commit** (`Add the lease schema, rule engine and first NSW rules`); push; CI green. Report and WAIT.

---

### Task 8: Remaining NSW rules

**Files:** Modify `app/rules/nsw.py`; extend `tests/test_rules_nsw.py`.

**Interfaces:** Consumes the T7 rule machinery unchanged. Produces rules: `nsw.pre_agreement_amounts` (s23, `holding_deposit_amount`), `nsw.rent_increase_frequency` (s41, gaps between `rent_increases` >= 12 months; also >= 12 months after `start_date` for the first increase), `nsw.rent_increase_notice` (s41, each increase with `notice_given_on` must give >= 60 days), `nsw.fixed_term_increase_disclosure` (s42, increase during a fixed term requires `fixed_term_increase_in_agreement is True`), `nsw.no_other_security` (s160, red if `other_security_amount` > 0), and `nsw.break_fee_cap` if — and only if — Step 1 locates its basis in the Act (defer to the Regulation milestone otherwise).

- [ ] **Step 1: Pin statutory text** — same query pattern as T7 Step 1 for sections 23, 41, 42, 160, and search the corpus for the break-fee basis:

```bash
uv run python -c "
import asyncio
from datetime import date
from sqlalchemy import select
from app.core.db import async_session_factory
from app.models import Act, Section

async def main():
    async with async_session_factory() as s:
        act = (await s.execute(select(Act).where(Act.slug == 'act-2010-042'))).scalar_one()
        rows = (await s.execute(select(Section).where(
            Section.act_id == act.id, Section.valid_to.is_(None),
            Section.body_text.ilike('%break fee%')))).scalars().all()
        for r in rows:
            print(r.section_no, r.heading)
asyncio.run(main())
"
```

Record each rule's operative wording, exact thresholds (frequency window, notice days, s23 cap expressed in weeks of rent, commencement dates for `applies_from` — s41's 12-month frequency limit commenced with a specific amendment; find it by querying that section's historical windows) in the rule docstrings. Correct any threshold the corpus contradicts and say so in the report.

- [ ] **Step 2: Failing tests** — extend `tests/test_rules_nsw.py`; every rule gets red/green/skipped cases in the T7 style. Representative cases (write all of them):

```python
async def test_two_increases_eight_months_apart_is_red(corpus_session):
    findings = await run_audit(
        corpus_session,
        "NSW",
        AS_AT,
        lease(
            rent_increases=[
                {"effective_on": "2027-02-01", "new_amount": "620"},
                {"effective_on": "2027-10-01", "new_amount": "640"},
            ]
        ),
    )
    assert next(f for f in findings if f.rule_id == "nsw.rent_increase_frequency").verdict == "red"


async def test_increase_with_59_days_notice_is_red(corpus_session):
    findings = await run_audit(
        corpus_session,
        "NSW",
        AS_AT,
        lease(
            rent_increases=[
                {"effective_on": "2027-06-01", "new_amount": "620", "notice_given_on": "2027-04-04"}
            ]
        ),
    )
    assert next(f for f in findings if f.rule_id == "nsw.rent_increase_notice").verdict == "red"


async def test_other_security_present_is_red(corpus_session):
    findings = await run_audit(
        corpus_session, "NSW", AS_AT, lease(other_security_amount=Decimal("500"))
    )
    assert next(f for f in findings if f.rule_id == "nsw.no_other_security").verdict == "red"
```

(`lease()` accepts the dict forms because pydantic coerces nested models.)

- [ ] **Step 3: Run -> fail.**
- [ ] **Step 4: Implement the rules** in `app/rules/nsw.py`, one check function per rule in the `_bond_check` style: compute, build `evidence` with `fields` + `computed`, return red/green with a one-sentence summary. Define `FREQ_COMMENCED: date` at module level (the pinned commencement of the s41 12-month frequency limit, from Step 1) and use it as that rule's `applies_from` — T9's temporal test imports it. Frequency check sorts increases by `effective_on` and flags any adjacent gap < 365 days (also `start_date` -> first increase); notice check flags `(effective_on - notice_given_on).days < 60` for increases that carry `notice_given_on`; disclosure check is red when the lease has an `end_date`, an increase effective before it, and `fixed_term_increase_in_agreement` is not True; s23 check compares `holding_deposit_amount` against the pinned cap; s160 is red when `other_security_amount > 0`. Register all in `NSW_RULES`.
- [ ] **Step 5: Run -> pass; full suite; ruff; commit** (`Add the remaining NSW rules`); push; CI green. Report the final rule count vs the spec's 8-10 target (with the break-fee disposition) and WAIT.

---

### Task 9: Golden set + temporal test

**Files:** Create `tests/test_golden.py`, `tests/golden/__init__.py` (empty), `tests/golden/leases.py`.

**Interfaces:** Consumes `run_audit`. Produces the golden harness later LLM milestones extend.

- [ ] **Step 1: Golden leases** — `tests/golden/leases.py`: a `GOLDEN: list[tuple[str, dict, dict[str, str]]]` of (case_id, lease_kwargs, expected `{rule_id: verdict}` for every non-skipped rule). ~20 cases built by seeding violations programmatically: a compliant baseline, then one case per rule flipping exactly that rule to red (bond 5 weeks, advance 3 weeks, holding deposit over cap, increases 8 months apart, 45-day notice, fixed-term increase without disclosure, other security 500), plus combined-violation cases and boundary cases (bond exactly at cap, notice exactly 60 days, increase exactly 12 months).

- [ ] **Step 2: Golden test** — `tests/test_golden.py`:

```python
from datetime import date

import pytest

from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput
from tests.golden.leases import GOLDEN
from tests.test_rules_nsw import corpus_session  # noqa: F401  (reuse the skip-guard fixture)

AS_AT = date(2026, 7, 24)


@pytest.mark.parametrize("case_id,lease_kwargs,expected", GOLDEN, ids=[g[0] for g in GOLDEN])
async def test_golden_case(corpus_session, case_id, lease_kwargs, expected):
    findings = await run_audit(corpus_session, "NSW", AS_AT, LeaseInput(**lease_kwargs))
    actual = {f.rule_id: f.verdict for f in findings if f.verdict != "skipped"}
    assert actual == expected
```

- [ ] **Step 3: Temporal test** — append to `tests/test_golden.py`. Choose the anchor from the T6 Step 4 candidate list (a section a V1 rule cites whose window closed at a reform date, or a rule whose `applies_from` falls inside the corpus range — the s41 12-month frequency commencement found in T8 Step 1 is the expected anchor). With `FREQ_COMMENCED` set to that pinned date:

```python
async def test_same_lease_differs_across_reform(corpus_session):
    """The frequency rule is inactive before its commencement and red after."""
    from app.rules.nsw import FREQ_COMMENCED

    lease_kwargs = dict(
        rent_amount="600",
        rent_frequency="weekly",
        start_date="2000-01-01",
        rent_increases=[
            {"effective_on": "2001-01-01", "new_amount": "620"},
            {"effective_on": "2001-06-01", "new_amount": "640"},
        ],
    )
    before = await run_audit(
        corpus_session,
        "NSW",
        FREQ_COMMENCED.replace(year=FREQ_COMMENCED.year - 1),
        LeaseInput(**lease_kwargs),
    )
    after = await run_audit(corpus_session, "NSW", AS_AT, LeaseInput(**lease_kwargs))
    freq_before = next(f for f in before if f.rule_id == "nsw.rent_increase_frequency")
    freq_after = next(f for f in after if f.rule_id == "nsw.rent_increase_frequency")
    assert freq_before.verdict == "skipped"
    assert freq_after.verdict == "red"
```

(T8 must therefore export `FREQ_COMMENCED: date` from `app/rules/nsw.py` — the pinned commencement used as that rule's `applies_from`.)

- [ ] **Step 4: Run -> all pass; full suite; ruff; commit** (`Add the golden set and temporal test`); push; CI green. Report and WAIT.

---

### Task 10: API — auth, audits, legislation lookup

**Files:** Create `app/core/auth.py`, `app/schemas/audit.py`, `app/routers/__init__.py`, `app/routers/audits.py`, `app/routers/legislation.py`, `tests/test_api.py`; modify `app/main.py`, `README.md`.

**Interfaces:** Consumes `run_audit`, `section_at`, `Audit` model, `ENGINE_VERSION`. Produces the public API per the spec.

- [ ] **Step 1: Failing tests** — `tests/test_api.py` (API tests run on the synthetic store via the `client` fixture; seed a minimal act+section so the bond rule resolves):

```python
from datetime import date

import pytest

from app.core.config import settings
from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act

KEY = {"X-API-Key": "test-key"}


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "test-key")


@pytest.fixture
async def seeded(db_session):
    act = Act(
        jurisdiction="NSW",
        slug="act-2010-042",
        title="Residential Tenancies Act 2010",
        source_url="x",
    )
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session,
        act.id,
        date(2011, 1, 31),
        [
            ParsedSection("159", "Payment of bonds", "4 weeks limit body", "Part 8", None),
            ParsedSection("33", "Payment of rent by tenant", "advance body", "Part 3", None),
        ],
    )
    await db_session.commit()


AUDIT_BODY = {
    "jurisdiction": "NSW",
    "lease": {
        "rent_amount": "600",
        "rent_frequency": "weekly",
        "start_date": "2026-01-01",
        "bond_amount": "3000",
    },
}


async def test_missing_key_is_401(client):
    assert (await client.post("/v1/audits", json=AUDIT_BODY)).status_code == 401


async def test_create_and_get_audit(client, seeded):
    created = await client.post("/v1/audits", json=AUDIT_BODY, headers=KEY)
    assert created.status_code == 201
    body = created.json()
    assert body["engine_version"]
    bond = next(f for f in body["findings"] if f["rule_id"] == "nsw.bond_max_4_weeks")
    assert bond["verdict"] == "red"

    fetched = await client.get(f"/v1/audits/{body['id']}", headers=KEY)
    assert fetched.status_code == 200
    assert fetched.json()["findings"] == body["findings"]


async def test_unknown_jurisdiction_is_422(client, seeded):
    bad = dict(AUDIT_BODY, jurisdiction="VIC")
    assert (await client.post("/v1/audits", json=bad, headers=KEY)).status_code == 422


async def test_section_lookup(client, seeded):
    ok = await client.get(
        "/v1/legislation/sections",
        params={"act": "act-2010-042", "section_no": "159", "as_at": "2026-01-01"},
        headers=KEY,
    )
    assert ok.status_code == 200
    assert ok.json()["heading"] == "Payment of bonds"
    missing = await client.get(
        "/v1/legislation/sections",
        params={"act": "act-2010-042", "section_no": "159", "as_at": "2005-01-01"},
        headers=KEY,
    )
    assert missing.status_code == 404
```

- [ ] **Step 2: Run -> fail** (404 route).

- [ ] **Step 3: Implement** — `app/core/auth.py`:

```python
from fastapi import Header, HTTPException

from app.core.config import settings


def require_api_key(x_api_key: str = Header(default="")) -> None:
    keys = {k.strip() for k in settings.api_keys.split(",") if k.strip()}
    if x_api_key not in keys:
        raise HTTPException(status_code=401, detail="Invalid API key")
```

`app/schemas/audit.py`:

```python
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
```

`app/routers/audits.py`:

```python
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.db import get_session
from app.models import Audit
from app.rules import ENGINE_VERSION
from app.rules.engine import run_audit
from app.schemas.audit import AuditCreate, AuditInfo

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


@router.post("/audits", status_code=201, response_model=AuditInfo)
async def create_audit(
    body: AuditCreate, session: AsyncSession = Depends(get_session)
) -> AuditInfo:
    as_at = body.as_at or date.today()
    findings = await run_audit(session, body.jurisdiction, as_at, body.lease)
    audit = Audit(
        jurisdiction=body.jurisdiction,
        as_at=as_at,
        input=body.lease.model_dump(mode="json"),
        findings=[f.model_dump(mode="json") for f in findings],
        engine_version=ENGINE_VERSION,
    )
    session.add(audit)
    await session.commit()
    await session.refresh(audit)
    return AuditInfo(
        id=audit.id,
        jurisdiction=audit.jurisdiction,
        as_at=audit.as_at,
        engine_version=audit.engine_version,
        findings=findings,
        created_at=audit.created_at,
    )


@router.get("/audits/{audit_id}", response_model=AuditInfo)
async def get_audit(audit_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> AuditInfo:
    audit = await session.get(Audit, audit_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return AuditInfo(
        id=audit.id,
        jurisdiction=audit.jurisdiction,
        as_at=audit.as_at,
        engine_version=audit.engine_version,
        findings=audit.findings,
        created_at=audit.created_at,
    )
```

`app/routers/legislation.py`:

```python
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.db import get_session
from app.services.legislation import section_at

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


class SectionInfo(BaseModel):
    section_no: str
    heading: str
    body_text: str
    part: str | None
    division: str | None
    valid_from: date
    valid_to: date | None


@router.get("/legislation/sections", response_model=SectionInfo)
async def get_section(
    act: str, section_no: str, as_at: date, session: AsyncSession = Depends(get_session)
) -> SectionInfo:
    section = await section_at(session, act, section_no, as_at)
    if section is None:
        raise HTTPException(status_code=404, detail="Section not in force at that date")
    return SectionInfo(
        section_no=section.section_no,
        heading=section.heading,
        body_text=section.body_text,
        part=section.part,
        division=section.division,
        valid_from=section.valid_from,
        valid_to=section.valid_to,
    )
```

Mount in `app/main.py`:

```python
from fastapi import FastAPI

from app.routers.audits import router as audits_router
from app.routers.legislation import router as legislation_router

app = FastAPI(
    title="Lease Compliance Service",
    description="General information, not legal advice.",
)
app.include_router(audits_router)
app.include_router(legislation_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
```

- [ ] **Step 4: Run -> pass.**
- [ ] **Step 5: README usage** — add curl examples for `POST /v1/audits` and the sections lookup (with `X-API-Key`), the `API_KEYS` env var, the ingest command, and the not-legal-advice disclaimer.
- [ ] **Step 6: Full suite; ruff; commit** (`Add the audits API and legislation lookup`); push; CI green. Report and WAIT — V1 complete.
