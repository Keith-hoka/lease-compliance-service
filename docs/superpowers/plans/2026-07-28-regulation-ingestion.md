# Regulation Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load the Residential Tenancies Regulation 2019 (slug `sl-2019-0629`, 20 point-in-time versions) into the temporal store, cover it with the daily monitor and the lookup API, and produce an evidence-driven rule-candidate survey.

**Architecture:** `registry.NSW_INSTRUMENTS` becomes a two-entry list and `ensure_act` takes the instrument; the ingest and monitor CLIs loop instruments through the unchanged fetch/parse/load pipeline. The ingest CLI restructures to a single `asyncio.run` with fetches in `asyncio.to_thread` — two sequential `asyncio.run` calls would reuse the global engine's pooled connections across dead event loops (the known cross-loop failure), and sync Playwright cannot run inside the loop.

**Tech Stack:** Existing service stack; no new dependencies; no schema change.

## Global Constraints

- Repo: `/Users/keithho/LLMProjects/lease-compliance-service`. Every task ends: full `uv run pytest -q` -> ruff sequence (`uv run ruff format .` -> `uv run ruff check --fix .` -> `uv run ruff check .` -> `uv run ruff format --check .`) -> commit -> `git push origin main` -> CI green -> report -> WAIT for approval.
- Regulation identity verbatim: jurisdiction `NSW`, slug `sl-2019-0629`, title `Residential Tenancies Regulation 2019`.
- Tests and CI never fetch the live site; live fetching happens only in the CLI verification steps.
- Grounded facts to re-verify during Task 2's first live run: 20 versions from 2019-12-16 to 2026-07-01; body clauses use `sec.<n>` ids. If the site differs, adjust and report.
- Schedules and the 2010 Regulation stay out; the SaaS repo is untouched.

## File Structure

| Path | Responsibility |
|---|---|
| `app/ingest/registry.py` | `NSW_INSTRUMENTS` list; `ensure_act(session, instrument)` |
| `app/ingest/__main__.py` | instrument loop, single-asyncio.run restructure |
| `app/monitor/__main__.py` | `refresh_corpus` instrument loop |
| `tests/test_loader.py` | updated registry test |
| `tests/test_regulation_corpus.py` | corpus-gated Regulation spot-checks |
| `docs/rule-candidates.md` | Task 3 survey output |

---

### Task 1: Multi-instrument registry and CLI loops

**Files:**
- Modify: `app/ingest/registry.py`, `app/ingest/__main__.py`, `app/monitor/__main__.py`, `tests/test_loader.py`

**Interfaces:**
- Consumes: existing fetcher/parser/loader and `new_version_dates`.
- Produces: `NSW_INSTRUMENTS: list[dict]` (Act entry first, Regulation second; keys `jurisdiction`/`slug`/`title`) and `async ensure_act(session, instrument: dict) -> Act`. Task 2 runs the CLIs this task rewires.

- [ ] **Step 1: Failing test** — in `tests/test_loader.py` replace `test_ensure_act_creates_then_reuses` with:

```python
async def test_ensure_act_creates_then_reuses(db_session):
    from app.ingest.registry import NSW_INSTRUMENTS, ensure_act

    for instrument in NSW_INSTRUMENTS:
        created = await ensure_act(db_session, instrument)
        assert created.slug == instrument["slug"]
        again = await ensure_act(db_session, instrument)
        assert again.id == created.id

    acts = (await db_session.execute(select(Act))).scalars().all()
    assert {act.slug for act in acts} == {"act-2010-042", "sl-2019-0629"}
```

- [ ] **Step 2: Run -> fail.** `uv run pytest tests/test_loader.py -q` — ImportError (`NSW_INSTRUMENTS`).

- [ ] **Step 3: Registry** — `app/ingest/registry.py` becomes:

```python
from sqlalchemy import select

from app.ingest.fetcher import LANDING_URL_TEMPLATE
from app.models import Act

NSW_INSTRUMENTS = [
    {
        "jurisdiction": "NSW",
        "slug": "act-2010-042",
        "title": "Residential Tenancies Act 2010",
    },
    {
        "jurisdiction": "NSW",
        "slug": "sl-2019-0629",
        "title": "Residential Tenancies Regulation 2019",
    },
]


async def ensure_act(session, instrument: dict) -> Act:
    """The registered row for a legislative instrument, created on first use."""
    act = (
        await session.execute(select(Act).where(Act.slug == instrument["slug"]))
    ).scalar_one_or_none()
    if act is None:
        act = Act(**instrument, source_url=LANDING_URL_TEMPLATE.format(slug=instrument["slug"]))
        session.add(act)
        await session.flush()
    return act
```

- [ ] **Step 4: Ingest CLI** — `app/ingest/__main__.py` becomes (single `asyncio.run`; fetches via `to_thread` because sync Playwright refuses to run inside the loop and a second `asyncio.run` would reuse dead-loop pooled connections):

```python
"""Ingest each instrument's full point-in-time history: fetch, parse, load.

Usage: uv run python -m app.ingest nsw [--limit-versions N]
"""

import argparse
import asyncio
from datetime import date
from pathlib import Path

from app.core.db import async_session_factory
from app.ingest.fetcher import fetch_landing, fetch_versions, parse_version_dates
from app.ingest.loader import load_version
from app.ingest.parser import parse_whole_act
from app.ingest.registry import NSW_INSTRUMENTS, ensure_act


async def load_all(instrument: dict, paths) -> None:
    async with async_session_factory() as session:
        act = await ensure_act(session, instrument)
        for path in paths:
            version_date = date.fromisoformat(path.stem)
            sections = parse_whole_act(path.read_text())
            stats = await load_version(session, act.id, version_date, sections)
            print(f"{instrument['slug']} {version_date}: sections={len(sections)} {stats}")
        await session.commit()


async def run(limit_versions: int | None) -> None:
    for instrument in NSW_INSTRUMENTS:
        landing = await asyncio.to_thread(fetch_landing, instrument["slug"])
        dates = parse_version_dates(landing)
        if limit_versions:
            dates = dates[:limit_versions]
        cache = Path("data/raw/nsw") / instrument["slug"]
        paths = await asyncio.to_thread(fetch_versions, instrument["slug"], dates, cache)
        await load_all(instrument, paths)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jurisdiction", choices=["nsw"])
    parser.add_argument("--limit-versions", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(run(args.limit_versions))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Monitor loop** — in `app/monitor/__main__.py`: change the registry import to `from app.ingest.registry import NSW_INSTRUMENTS, ensure_act`, and replace `refresh_corpus` and `run`'s first lines:

```python
async def refresh_corpus() -> None:
    """Fetch and load any legislation versions published since the last run."""
    for instrument in NSW_INSTRUMENTS:
        landing = await asyncio.to_thread(fetch_landing, instrument["slug"])
        timeline = parse_version_dates(landing)
        async with async_session_factory() as session:
            act = await ensure_act(session, instrument)
            ingested = set(
                (
                    await session.execute(
                        select(IngestedVersion.version_date).where(
                            IngestedVersion.act_id == act.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            missing = new_version_dates(timeline, ingested)
            if not missing:
                print(f"corpus: {instrument['slug']} no new versions")
                await session.commit()
                continue
            cache = Path("data/raw/nsw") / instrument["slug"]
            paths = await asyncio.to_thread(
                fetch_versions, instrument["slug"], missing, cache
            )
            for path in paths:
                version_date = date.fromisoformat(path.stem)
                if version_date not in missing:
                    continue
                stats = await load_version(
                    session, act.id, version_date, parse_whole_act(path.read_text())
                )
                print(f"corpus: {instrument['slug']} {version_date} {stats}")
            await session.commit()
```

and in `run(...)` replace `NSW_ACT["jurisdiction"]` with `NSW_INSTRUMENTS[0]["jurisdiction"]`.

- [ ] **Step 6: Run -> pass.** `uv run pytest tests/test_loader.py -q`, then quick CLI sanity without new fetches: `uv run python -m app.monitor nsw --skip-fetch` — expected `monitor: checked=<n> changed=0`.

- [ ] **Step 7: Full suite; ruff; commit** (`Register the Regulation as a second instrument`); push; CI green. Report and WAIT.

---

### Task 2: Backfill, idempotency and corpus spot-checks

**Files:**
- Create: `tests/test_regulation_corpus.py`

**Interfaces:**
- Consumes: Task 1's CLIs; `section_at(session, act_slug, section_no, as_at)`.
- Produces: the loaded Regulation corpus and its skip-guarded tests.

- [ ] **Step 1: Trial run** — `uv run python -m app.ingest nsw --limit-versions 1` (opens Chrome; Act line skips, Regulation fetches its first version). Expected final line shape: `sl-2019-0629 2019-12-16: sections=<n> LoadStats(inserted=<n>, closed=0, skipped=False)`.

- [ ] **Step 2: Full backfill** — `uv run python -m app.ingest nsw` (~19 more Regulation fetches at 2 s spacing). Then re-run the same command: every line for both instruments must print `skipped=True` (idempotency proof). Record both outputs for the report.

- [ ] **Step 3: Failing tests** — `tests/test_regulation_corpus.py`:

```python
from datetime import date

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models import Act
from app.services.legislation import section_at

REG = "sl-2019-0629"
FIRST_VERSION = date(2019, 12, 16)


@pytest.fixture
async def regulation_session():
    """A session against the dev store; skip when the Regulation is not loaded.

    Mirrors corpus_session in tests/test_rules_nsw.py with its own slug guard.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            act = (
                await session.execute(select(Act).where(Act.slug == REG))
            ).scalar_one_or_none()
        except (OSError, SQLAlchemyError, asyncpg.PostgresError):
            pytest.skip("corpus store not reachable")
        if act is None:
            pytest.skip("Regulation corpus not ingested")
        yield session
    await engine.dispose()


async def test_regulation_clause_resolves_across_time(regulation_session):
    early = await section_at(regulation_session, REG, "1", date(2020, 1, 1))
    now = await section_at(regulation_session, REG, "1", date(2026, 7, 28))
    assert early is not None
    assert now is not None
    assert early.valid_from == FIRST_VERSION


async def test_regulation_before_first_version_is_none(regulation_session):
    assert (await section_at(regulation_session, REG, "1", date(2019, 1, 1))) is None
```

If clause 1 turns out to have been amended (its earliest window not starting at `FIRST_VERSION`), pin the assertion to the actual corpus window and note it in the report.

- [ ] **Step 4: Run -> pass.** `uv run pytest tests/test_regulation_corpus.py -q` (skips before the backfill, passes after — Step 2 already ran, so expect 2 passed).

- [ ] **Step 5: Spot-check script** (output goes in the report):

```bash
uv run python -c "
import asyncio
from datetime import date
from sqlalchemy import func, select
from app.core.db import async_session_factory
from app.models import Act, Section

async def main():
    async with async_session_factory() as s:
        act = (await s.execute(select(Act).where(Act.slug == 'sl-2019-0629'))).scalar_one()
        open_now = (await s.execute(select(func.count()).where(Section.act_id == act.id, Section.valid_to.is_(None)))).scalar()
        total = (await s.execute(select(func.count()).where(Section.act_id == act.id))).scalar()
        print('regulation open clauses:', open_now, '| total rows:', total)
        changed = (await s.execute(select(Section.section_no, Section.valid_to).where(Section.act_id == act.id, Section.valid_to.is_not(None)).order_by(Section.valid_to.desc()).limit(10))).all()
        print('recently changed clauses:', changed)
asyncio.run(main())
"
```

Also confirm the lookup path end to end once: start the API (`API_KEYS=dev-key:rentalapp uv run uvicorn app.main:app --port 8100`), `curl -s "http://localhost:8100/v1/legislation/sections?act=sl-2019-0629&section_no=1&as_at=2026-01-01" -H "X-API-Key: dev-key"` returns the clause, and a pre-2019 `as_at` returns 404; stop the server. Record both responses.

- [ ] **Step 6: Full suite; ruff; commit** (`Load the Regulation corpus and its spot-checks`); push; CI green. Report (include run + spot-check output) and WAIT.

---

### Task 3: Rule candidate survey

**Files:**
- Create: `docs/rule-candidates.md`

**Interfaces:**
- Consumes: the loaded Regulation corpus.
- Produces: the classified survey; possibly proposed rules for approval.

- [ ] **Step 1: Find candidate clauses** — search the current Regulation text for lease-domain terms and list matches:

```bash
uv run python -c "
import asyncio
from sqlalchemy import select
from app.core.db import async_session_factory
from app.models import Act, Section

TERMS = ['water', 'receipt', 'condition report', 'holding fee', 'break fee', 'bond', 'rent increase', 'smoke alarm']

async def main():
    async with async_session_factory() as s:
        act = (await s.execute(select(Act).where(Act.slug == 'sl-2019-0629'))).scalar_one()
        rows = (await s.execute(select(Section).where(Section.act_id == act.id, Section.valid_to.is_(None)))).scalars().all()
        for term in TERMS:
            hits = [(r.section_no, r.heading) for r in rows if term in r.body_text.lower() or term in r.heading.lower()]
            print(term, '->', hits)
asyncio.run(main())
"
```

- [ ] **Step 2: Read and classify** — for each hit, read the clause body from the corpus (`section_at` at today) and classify against the current `LeaseInput` fields (`rent_amount`, `rent_frequency`, `start_date`, `end_date`, `bond_amount`, `rent_in_advance_amount`, `holding_deposit_amount`, `other_security_amount`, `break_fee_amount`, `rent_increases`, `fixed_term_increase_in_agreement`):
  - **Computable now**: the check is a pure function of those fields.
  - **Needs new inputs**: name the missing input and the supplying milestone (SaaS form fields or LLM clause audit).
  - **Not rule-shaped**: procedural/definitional clauses with nothing to check.

- [ ] **Step 3: Write `docs/rule-candidates.md`** — one table row per candidate: clause number, heading, one-line obligation summary, classification, missing input (if any), pinned operative quote (short). Honest outcome allowed: zero computable rules.

- [ ] **Step 4: Full suite; ruff; commit** (`Add the Regulation rule-candidate survey`); push; CI green. Report the classification summary and WAIT. If any candidate is **computable now**, the report proposes it (pinned text, threshold, `applies_from` from corpus windows) and implementation proceeds only after approval as an amendment task using the V1 rule pattern (failing red/green/skipped tests on the corpus first, then the rule in `app/rules/nsw.py`, golden extension, full rhythm).
