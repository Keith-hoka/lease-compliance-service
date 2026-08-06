# NSW Schedule Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every parseable NSW schedule clause - including the Standard Form Agreement's 59 numbered terms - lands in the corpus point-in-time, rebuilt across all cached historical versions.

**Architecture:** `parse_whole_act` gains a schedule sweep with two extraction shapes (schedule-level `sec` clauses keyed `S{n}-{m}`; form terms keyed `S{n}-T{m}` with blockgroup headings); the two NSW instruments are wiped and re-ingested from the local HTML cache so schedules exist in every historical version window; the CI corpus dump is refreshed.

**Tech Stack:** existing selectolax parser, existing loader/registry - no new dependencies, no schema changes.

**Spec:** `docs/superpowers/specs/2026-08-06-nsw-schedule-ingestion-design.md`

## Global Constraints

- Keyspaces exactly: schedule-level clauses `S{n}-{m}`; form terms `S{n}-T{m}`. Clause ids appear with and without a trailing dot (`sch.1-sec.2.`, `sch.4-sec.1`) - both must parse.
- Form-term headings come from the nearest enclosing `frag-blockgroup` heading; both shapes set part=None and division="Schedule {n} <schedule heading>".
- Body-section parsing byte-identical to today; VIC untouched; no rule or clause-audit changes.
- Wipe order (no FK cascades): sections -> ingested_versions -> acts, for slugs `act-2010-042` and `sl-2019-0629` only.
- Rebuild is cache-first from `data/raw/nsw/`; the CI corpus dump is refreshed afterwards via `scripts/refresh-corpus-dump.sh`.
- uv only, no emojis, TDD, ruff sequence, commit + push + CI green per task. Trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Parser schedule sweep

**Files:**
- Modify: `app/ingest/parser.py`
- Test: `tests/test_parser_schedules.py` (new)

**Interfaces:**
- Consumes: existing `ParsedSection`, `_clean`, `_ancestor_heading`.
- Produces: `parse_whole_act` additionally returns schedule sections shaped per the Global Constraints; Task 2's rebuild relies on it.

- [ ] **Step 1: Write the failing tests**

`tests/test_parser_schedules.py`:

```python
from pathlib import Path

import pytest

from app.ingest.parser import parse_whole_act

FIXTURE = """
<div id="sec.5" class="frag-clause">
  <div role="heading" class="heading"><span class="frag-no">5</span> <span class="frag-heading">Ordinary clause</span></div>
  <blockquote class="children">Body of the ordinary clause.</blockquote>
</div>
<div id="sch.1" class="frag-schedule">
  <div role="heading" class="heading"><span class="frag-no">Schedule 1</span> <span id="sch.1-he" class="frag-heading">Standard Form Agreement</span></div>
  <div id="sch.1-sec.2." class="frag-clause">
    <div role="heading" class="heading"><span class="frag-no">2.</span> <span class="frag-heading">Ending this agreement</span></div>
    <blockquote class="children">Schedule clause body.</blockquote>
    <div class="frag-historynote">history noise</div>
  </div>
  <div id="sch.1-form" class="frag-form">
    <div id="sch.1-form-bg1" class="frag-blockgroup">
      <div class="frag-head"><div class="heading joined"><span class="frag-heading">This agreement is made</span></div></div>
      <div id="sch.1-form-bg1-bg2" class="frag-blockgroup">
        <div class="frag-head"><div class="heading joined"><span class="frag-heading">RENT</span></div></div>
        <div class="frag-block">
          <div id="sch.1-form-bg1-bg2-para1.7." class="frag-li"><blockquote class="children"><span class="frag-no"><b>7.</b></span>&#160;&#160;<b>The tenant agrees</b> to pay rent on time.
            <div class="frag-li"><blockquote class="children"><span class="frag-no">(a)</span> by the method chosen.</blockquote></div>
          </blockquote></div>
        </div>
      </div>
    </div>
  </div>
</div>
<div id="sch.2" class="frag-schedule">
  <div role="heading" class="heading"><span class="frag-no">Schedule 2</span> <span class="frag-heading">Condition report</span></div>
  <div class="frag-form"><div class="frag-table">table only, no numbered terms</div></div>
</div>
<div id="sch.4" class="frag-schedule">
  <div role="heading" class="heading"><span class="frag-no">Schedule 4</span> <span class="frag-heading">Penalty notice offences</span></div>
  <div id="sch.4-sec.1" class="frag-clause">
    <div role="heading" class="heading"><span class="frag-no">1</span> <span class="frag-heading">Application of Schedule</span></div>
    <blockquote class="children">Applies to offences.</blockquote>
  </div>
</div>
"""


def _by_no(sections):
    return {s.section_no: s for s in sections}


def test_schedule_clauses_and_form_terms_parse():
    sections = _by_no(parse_whole_act(FIXTURE))
    assert set(sections) == {"5", "S1-2", "S1-T7", "S4-1"}

    clause = sections["S1-2"]
    assert clause.heading == "Ending this agreement"
    assert clause.body_text == "Schedule clause body."
    assert clause.division == "Schedule 1 Standard Form Agreement"
    assert clause.part is None

    term = sections["S1-T7"]
    assert term.heading == "RENT"
    assert term.body_text.startswith("The tenant agrees")
    assert "(a) by the method chosen." in term.body_text
    assert term.division == "Schedule 1 Standard Form Agreement"

    dotless = sections["S4-1"]
    assert dotless.heading == "Application of Schedule"
    assert dotless.division == "Schedule 4 Penalty notice offences"


def test_ordinary_sections_unchanged():
    sections = _by_no(parse_whole_act(FIXTURE))
    assert sections["5"].heading == "Ordinary clause"
    assert sections["5"].body_text == "Body of the ordinary clause."


def test_history_notes_stripped_from_schedule_clauses():
    sections = _by_no(parse_whole_act(FIXTURE))
    assert "history noise" not in sections["S1-2"].body_text


CACHE = Path("data/raw/nsw/sl-2019-0629")


def test_real_regulation_cache_yields_the_standard_form():
    cached = sorted(CACHE.glob("*.html"))
    if not cached:
        pytest.skip("NSW regulation cache not present")
    sections = parse_whole_act(cached[-1].read_text())
    terms = [s for s in sections if s.section_no.startswith("S1-T")]
    clauses = [s for s in sections if s.section_no.startswith("S1-") and "-T" not in s.section_no]
    assert len(terms) >= 55
    assert len(clauses) == 6
    assert any(s.heading == "RENT" for s in terms)
```

- [ ] **Step 2: Watch them fail**

Run: `uv run pytest tests/test_parser_schedules.py -v`
Expected: the first three tests fail - `parse_whole_act` returns only
`{"5"}` (no schedule keys). The cache test fails on the missing terms.

- [ ] **Step 3: Implement the schedule sweep**

In `app/ingest/parser.py`, add `import re` is already present; add at
module level:

```python
_SCH_SEC_RE = re.compile(r"^sch\.([0-9]+[A-Z]?)-sec\.([0-9A-Za-z]+?)\.?$")
_TERM_NO_RE = re.compile(r"^[0-9]+[A-Z]?\.$")
```

and the sweep:

```python
def _parse_schedules(tree: HTMLParser) -> list[ParsedSection]:
    """Schedule clauses and standard-form terms, in two keyspaces.

    Schedule-level clauses (sch.N-sec.M fragments, trailing dot
    optional) become S{N}-{M}; numbered form terms inside frag-form
    become S{N}-T{M} with the enclosing blockgroup heading.
    """
    sections: list[ParsedSection] = []
    for schedule in tree.css("div.frag-schedule"):
        sch_id = schedule.attributes.get("id", "") or ""
        if not sch_id.startswith("sch."):
            continue
        sch_no = sch_id.removeprefix("sch.")
        heading_node = schedule.css_first(".frag-heading")
        sch_heading = _clean(heading_node.text()) if heading_node else ""
        division = _clean(f"Schedule {sch_no} {sch_heading}")
        for note in schedule.css(".frag-historynote, .view-history-note"):
            note.decompose()
        for clause in schedule.css("div.frag-clause"):
            match = _SCH_SEC_RE.match(clause.attributes.get("id", "") or "")
            if match is None or match.group(1) != sch_no:
                continue
            clause_heading = clause.css_first(".frag-heading")
            body_node = clause.css_first("blockquote.children")
            sections.append(
                ParsedSection(
                    section_no=f"S{sch_no}-{match.group(2)}",
                    heading=_clean(clause_heading.text()) if clause_heading else "",
                    body_text=_clean(body_node.text()) if body_node else "",
                    part=None,
                    division=division,
                )
            )
        form = schedule.css_first("div.frag-form")
        if form is None:
            continue
        for item in form.css("div.frag-li"):
            no_node = item.css_first(".frag-no")
            if no_node is None:
                continue
            no_text = no_node.text().strip()
            if not _TERM_NO_RE.fullmatch(no_text):
                continue
            no_node.decompose()
            sections.append(
                ParsedSection(
                    section_no=f"S{sch_no}-T{no_text.rstrip('.')}",
                    heading=_ancestor_heading(item, "frag-blockgroup") or "",
                    body_text=_clean(item.text()),
                    part=None,
                    division=division,
                )
            )
    return sections
```

and change `parse_whole_act`'s return to include the sweep:

```python
    return sections + _parse_schedules(tree)
```

(The body-section loop is untouched: its `id.startswith("sec.")` filter
already excludes `sch.`-prefixed clause ids.)

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/test_parser_schedules.py -v`
Expected: 4 passed (the cache test runs on this machine).

- [ ] **Step 5: Full suite, ruff, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/ingest/parser.py tests/test_parser_schedules.py
git commit -m "Parse NSW schedule clauses and standard-form terms"
git push origin main
```

Note: CI skips the cache test (no data/ in CI) - the synthetic tests
carry the logic there. Nothing consumes the new keys yet, so counts
elsewhere are unchanged.

---

### Task 2: Wipe and rebuild the NSW corpus from cache

**Files:**
- Create: `scripts/rebuild-nsw-corpus.py` (one-shot, kept for the production run)
- Modify: `tests/fixtures/corpus.dump` (refreshed)

**Interfaces:**
- Consumes: Task 1's parser; existing `app.ingest.__main__` `run` flow; `scripts/refresh-corpus-dump.sh`.
- Produces: dev corpus with NSW schedules point-in-time; refreshed CI dump. Task 3 repeats the wipe+rebuild against production.

- [ ] **Step 1: Record the before-counts**

```bash
uv run python - <<'EOF'
import asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.config import settings
from app.models import Act, Section

async def main():
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for slug in ("act-2010-042", "sl-2019-0629"):
            act = (await s.execute(select(Act).where(Act.slug == slug))).scalar_one()
            total = (await s.execute(select(func.count()).select_from(Section).where(Section.act_id == act.id))).scalar_one()
            sched = (await s.execute(select(func.count()).select_from(Section).where(Section.act_id == act.id, Section.section_no.like("S%")))).scalar_one()
            print(slug, "total", total, "schedule", sched)
    await engine.dispose()

asyncio.run(main())
EOF
```

Expected: schedule counts are 0 for both. Save the totals - the
after-state must show identical non-schedule totals.

- [ ] **Step 2: Write the wipe-and-rebuild script**

`scripts/rebuild-nsw-corpus.py`:

```python
"""Wipe the two NSW instruments and re-ingest every cached version.

Run against the dev store by default; point DATABASE_URL elsewhere
(e.g. the production tunnel) to rebuild that store instead. The ingest
step is cache-first: all cached versions in data/raw/nsw are loaded;
only the landing-page version check touches the network.
"""

import asyncio

from sqlalchemy import delete, select

from app.core.db import async_session_factory
from app.models import Act, IngestedVersion, Section

NSW_SLUGS = ("act-2010-042", "sl-2019-0629")


async def wipe() -> None:
    async with async_session_factory() as session:
        for slug in NSW_SLUGS:
            act = (await session.execute(select(Act).where(Act.slug == slug))).scalar_one_or_none()
            if act is None:
                continue
            await session.execute(delete(Section).where(Section.act_id == act.id))
            await session.execute(delete(IngestedVersion).where(IngestedVersion.act_id == act.id))
            await session.execute(delete(Act).where(Act.id == act.id))
            print(f"wiped {slug}")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(wipe())
```

Verify the model/class names against `app/models/legislation.py` and
`app/core/db.py` before running (the ingested-versions class name and
the session factory export must match the real ones; adjust the import
if they differ - the tables are `acts`, `sections`,
`ingested_versions`).

- [ ] **Step 3: Wipe, then re-ingest**

```bash
uv run python scripts/rebuild-nsw-corpus.py
uv run python -m app.ingest run
```

Expected: both instruments re-ingest every cached version (the landing
check may make one polite live fetch per instrument); the loader's
integrity guards (zero-sections, duplicate section_no, out-of-order
version dates) hold throughout. If a duplicate-key guard fires, that is
a parser keyspace bug - stop and fix Task 1, do not weaken the guard.

- [ ] **Step 4: After-probes**

```bash
uv run python - <<'EOF'
import asyncio
from datetime import date
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.config import settings
from app.models import Act, Section
from app.services.legislation import section_at

async def main():
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for slug in ("act-2010-042", "sl-2019-0629"):
            act = (await s.execute(select(Act).where(Act.slug == slug))).scalar_one()
            total = (await s.execute(select(func.count()).select_from(Section).where(Section.act_id == act.id, ~Section.section_no.like("S%")))).scalar_one()
            sched = (await s.execute(select(func.count()).select_from(Section).where(Section.act_id == act.id, Section.section_no.like("S%")))).scalar_one()
            print(slug, "non-schedule", total, "schedule", sched)
        term = await section_at(s, "sl-2019-0629", "S1-T1", date(2026, 8, 6))
        print("S1-T1 today:", term.heading if term else "MISSING", "|", (term.body_text[:60] if term else ""))
        rows = (
            await s.execute(
                select(Section.section_no, func.min(Section.valid_from))
                .join(Act, Act.id == Section.act_id)
                .where(Act.slug == "sl-2019-0629", Section.section_no.like("S1-T%"))
                .group_by(Section.section_no)
                .order_by(func.min(Section.valid_from).desc())
            )
        ).all()
        newest_no, boundary = rows[0]
        print("latest-born form term:", newest_no, "first valid_from", boundary)
        before = await section_at(s, "sl-2019-0629", newest_no, boundary.replace(day=max(1, boundary.day - 1)) if boundary.day > 1 else boundary)
        after = await section_at(s, "sl-2019-0629", newest_no, boundary)
        print("boundary probe:", "absent" if before is None or before.valid_from == boundary else "PRESENT-EARLY", "->", "hit" if after else "MISS")
    await engine.dispose()

asyncio.run(main())
EOF
```

Expected: non-schedule counts equal Step 1's totals; schedule counts
now positive for both instruments (the Regulation's includes >= 55
S1-T terms + 6 S1 clauses); S1-T1 resolves today; the latest-born form
term's version boundary flips absent-to-hit. Record all numbers in the
report. (If the boundary arithmetic on `replace(day=...)` is awkward
for a month-start boundary, query `section_at` one day earlier via
`timedelta(days=1)` instead - the point is a pre-boundary miss and an
on-boundary hit.)

- [ ] **Step 5: Full suite, dump refresh, commit**

```bash
uv run pytest
CI=true uv run pytest tests/test_corpus_ci_guard.py -v
./scripts/refresh-corpus-dump.sh
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add scripts/rebuild-nsw-corpus.py tests/fixtures/corpus.dump
git commit -m "Rebuild the NSW corpus with schedules and refresh the CI dump"
git push origin main
```

Expected: full suite green (existing corpus tests query by slug +
section_no and survive the UUID churn); the guard passes; the dump
grows (roughly 1.1-1.3 MB); CI green with the corpus tests running
against the refreshed dump.

---

### Task 3: Production rebuild and acceptance (interactive)

No repo changes except the ledger and memory. Run by the controller.

- [ ] **Step 1: Rebuild production over the tunnel**

Per `deploy/README.md:78-79`: open the ssh tunnel, then run the wipe
and ingest with `DATABASE_URL` pointed at
`postgresql+asyncpg://postgres:<db password>@localhost:15433/lease_compliance`:

```bash
ssh -f -N -o ExitOnForwardFailure=yes -L 15433:127.0.0.1:5432 deploy@168.144.169.66
DATABASE_URL=<tunnel url> uv run python scripts/rebuild-nsw-corpus.py
DATABASE_URL=<tunnel url> uv run python -m app.ingest run
```

- [ ] **Step 2: Production acceptance**

`GET https://api.leasekoala.com/v1/legislation/sections` (with the
route's query parameters - slug `sl-2019-0629`, section_no `S1-T1`,
and the as_at pair from Task 2's boundary probe): today's query hits
with the RENT-family heading; the pre-boundary date for the
latest-born term misses. The daily monitor's next kickstart reports
no-new-versions for all four instruments.

- [ ] **Step 3: Ledger and memory**

Append completion to `.superpowers/sdd/progress.md`; update the
milestone memory: Regulation-schedules sub-project (a) done, (b) VIC
Form 1 and (c) comparison family next.

---

## Self-review

- Spec coverage: two extraction shapes with the exact keyspaces and the
  dot variance (Task 1); blockgroup headings, division carrying
  schedule identity, Schedule 2 contributing nothing (Task 1 fixture);
  wipe order and cache-first rebuild (Task 2); before/after count
  probes, point-in-time probe, empirical version boundary (Task 2);
  integrity guards standing watch (Task 2 Step 3); CI dump refresh
  (Task 2 Step 5); production rebuild over the tunnel + endpoint
  acceptance + monitor kickstart (Task 3); dangling section_id UUIDs
  accepted (spec) - no task needs to act on them.
- Placeholders: the two verify-the-name notes (ingested-versions class,
  session factory export; endpoint query parameters) point at exact
  files and are discoverable facts, not deferred design.
- Type consistency: `S{n}-{m}`/`S{n}-T{m}` identical across parser
  code, fixture assertions, probes, and acceptance; `parse_whole_act`
  return shape unchanged (list[ParsedSection]).
