# VIC Form Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** All prescribed forms' numbered terms in VIC Regs 2021 Schedule 1 (25 form openers in versions 001-004 and 27 in 005-009 - Forms 1-25 plus 3A and 16A; 375 terms in 001-004 and 403 in 005-009 once Form 3A's unkeyable restart is excluded - including Form 1's 32 and Form 2's 40 rental-agreement terms) land in the corpus point-in-time under `S1-F{k}-T{m}` keys.

**Architecture:** `parse_docx` gains a form sweep active inside schedule regions: "New Form Heading" paragraphs open form scopes and titles, raw-text `^N.\t` or `^N. \t` paragraphs start terms (two source conventions; the tab must be matched before `_clean` collapses it), Side Notes are skipped within forms only; the VIC Regulations instrument is wiped and rebuilt from the DOCX cache; the CI dump is refreshed; production follows over the tunnel.

**Tech Stack:** existing python-docx parser, existing loader - no new dependencies, no schema changes.

**Spec:** `docs/superpowers/specs/2026-08-07-vic-form-ingestion-design.md`

## Global Constraints

- Keys exactly `S{sch}-F{form}-T{term}` (Schedule 1 today: `S1-F1-T7` shapes). `part = "Schedule 1—Forms"` (the schedule heading text, as VIC's existing schedule rows use part); `division = "Form {k} <form title>"`.
- Term detection runs on the RAW paragraph text (`^(\d+[A-Z]?)\.\t`) - `_clean` collapses the tab. "Side Note"-style paragraphs are skipped inside form scopes only; body sections keep absorbing side notes exactly as today (byte-identical body-section parsing).
- `PART ...` New Form Headings subdivide a form without closing it; term numbering is continuous across PARTs. Table content stays out of term bodies (accepted limitation).
- Existing S3-S5 schedule clauses, body sections, and the NSW parser are untouched.
- Wipe scope: `residential-tenancies-regulations-2021` only (sections -> ingested_versions -> act row). Rebuild via `uv run python -m app.ingest vic` (the already-ingested RTA Act's versions skip; only the wiped Regulations reload). CI dump refreshed via `scripts/refresh-corpus-dump.sh`.
- Production: tunnel per deploy/README.md, and ALWAYS close it afterwards (`pkill -f "15433:127.0.0.1:5432"`) - the port is shared with the daily monitor.
- uv only, no emojis, TDD, ruff sequence, commit + push + CI green per task. Trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Form sweep in the VIC parser

**Files:**
- Modify: `app/ingest/parser_vic.py`
- Test: `tests/test_parser_vic.py` (append)

**Interfaces:**
- Consumes: existing `ParsedSection`, `_clean`, the parse loop's `part`/`schedule_no` state, tests' `build_docx(paragraphs: list[tuple[str | None, str]])` helper.
- Produces: `parse_docx` additionally emits form terms per the Global Constraints; Task 2's rebuild relies on it.

- [ ] **Step 1: Append the failing tests**

To `tests/test_parser_vic.py`:

```python
def test_form_terms_parse_with_form_scoped_keys():
    data = build_docx(
        [
            ("Heading - PART", "Schedule 1—Forms"),
            ("Side Note", "Sch. 1 Form 1 amended by S.R. No. 123/2025."),
            ("New Form Heading", "Form 1"),
            (None, "Residential Tenancies Act 1997"),
            ("New Form Heading", "Residential rental agreement of no more than 5 years"),
            ("New Form Heading", "PART A—GENERAL"),
            (None, "1.\tDate of agreement"),
            (None, "This is the date the agreement is signed."),
            ("Side Note", "amendment note inside the form"),
            (None, "2.\tPremises"),
            (None, "Address of premises."),
            ("New Form Heading", "PART B—Standard Terms"),
            (None, "3.\tRent"),
            (None, "Rent must be paid on time."),
            ("New Form Heading", "Form 2"),
            ("New Form Heading", "Agreement of more than 5 years"),
            (None, "1.\tDate of agreement"),
            (None, "Second form first term."),
        ]
    )
    sections = parse_docx(data)
    by_no = {s.section_no: s for s in sections}
    assert set(by_no) == {"S1-F1-T1", "S1-F1-T2", "S1-F1-T3", "S1-F2-T1"}

    first = by_no["S1-F1-T1"]
    assert first.heading == "Date of agreement"
    assert first.body_text == "This is the date the agreement is signed."
    assert first.part == "Schedule 1—Forms"
    assert first.division == "Form 1 Residential rental agreement of no more than 5 years"

    rent = by_no["S1-F1-T3"]
    assert rent.heading == "Rent"
    assert rent.body_text == "Rent must be paid on time."

    second_form = by_no["S1-F2-T1"]
    assert second_form.division == "Form 2 Agreement of more than 5 years"
    assert second_form.body_text == "Second form first term."


def test_form_terms_coexist_with_schedule_clauses():
    data = build_docx(
        [
            ("Draft Heading 1", "12 Body clause"),
            (None, "Body clause text."),
            ("Heading - PART", "Schedule 1—Forms"),
            ("Draft Heading 1", "5 Schedule clause"),
            (None, "Schedule clause text."),
            ("New Form Heading", "Form 1"),
            ("New Form Heading", "A form title"),
            (None, "1.\tOnly term"),
            (None, "Term body."),
        ]
    )
    sections = parse_docx(data)
    by_no = {s.section_no: s for s in sections}
    assert set(by_no) == {"12", "S1-5", "S1-F1-T1"}
    assert by_no["12"].body_text == "Body clause text."
    assert by_no["S1-5"].body_text == "Schedule clause text."


def test_numbered_body_lines_do_not_become_terms_outside_forms():
    data = build_docx(
        [
            ("Draft Heading 1", "12 Body clause"),
            (None, "1.\tThis is body prose with a tab, not a form term."),
        ]
    )
    sections = parse_docx(data)
    assert [s.section_no for s in sections] == ["12"]
    assert "body prose" in sections[0].body_text


REGS_CACHE = Path("data/raw/vic/residential-tenancies-regulations-2021")


def test_real_regs_cache_yields_form_terms():
    cached = sorted(REGS_CACHE.glob("*.docx"))
    if not cached:
        pytest.skip("VIC regulations cache not present")
    sections = parse_docx(cached[-1].read_bytes())
    form_terms = [s for s in sections if "-F" in s.section_no]
    f1 = [s for s in form_terms if s.section_no.startswith("S1-F1-")]
    f2 = [s for s in form_terms if s.section_no.startswith("S1-F2-")]
    # Exact counts drift with amendments; floors match the probed current
    # version (F1=32, F2=40, 403 total).
    assert len(f1) >= 30
    assert len(f2) >= 38
    assert len(form_terms) >= 200
    assert any(s.heading == "Rent" for s in f1)
    assert all(s.part == "Schedule 1—Forms" for s in form_terms)
    schedule_clauses = [
        s for s in sections if s.section_no.startswith("S") and "-F" not in s.section_no
    ]
    assert len(schedule_clauses) >= 35
```

Add `from pathlib import Path` and `import pytest` to the file's imports
if absent.

- [ ] **Step 2: Watch them fail**

Run: `uv run pytest tests/test_parser_vic.py -v`
Expected: the three synthetic tests fail with no `S1-F` keys emitted
(the coexist test sees only `{"12", "S1-5"}`); the real-cache test
fails on zero form terms. Every pre-existing test still passes.

- [ ] **Step 3: Implement the form sweep**

In `app/ingest/parser_vic.py`, add at module level:

```python
FORM_HEADING_STYLE = "New Form Heading"
SIDE_NOTE_STYLE = "Side Note"

_FORM_RE = re.compile(r"^Form (\d+[A-Z]?)\b")
_FORM_TERM_RE = re.compile(r"^(\d+[A-Z]?)\.\t(.+)$", re.DOTALL)
```

Inside `parse_docx`, add form state next to the existing state:

```python
form_no: str | None = None
form_title: str | None = None
term: dict | None = None


def flush_term() -> None:
    nonlocal term
    if term is not None:
        sections.append(
            ParsedSection(
                section_no=f"S{schedule_no}-F{form_no}-T{term['no']}",
                heading=term["heading"],
                body_text=_clean(" ".join(term["body"])),
                part=part,
                division=_clean(f"Form {form_no} {form_title or ''}"),
            )
        )
        term = None
```

and wire the loop (placement matters - each insertion point named):

1. In the `text == "Endnotes"` branch: call `flush_term()` before `break`.
2. In the `_SCHEDULE_RE` branch: call `flush_term()` and reset
   `form_no = None; form_title = None` alongside the existing resets.
3. After the `_DIVISION_RE` branch and before the started-gate, add the
   form branches:

```python
        if schedule_no is not None and style_name == FORM_HEADING_STYLE:
            flush_term()
            form_match = _FORM_RE.match(text)
            if form_match:
                form_no, form_title = form_match.group(1), None
            elif form_no is not None and form_title is None and not text.startswith("PART"):
                form_title = text
            continue
        if form_no is not None and style_name == SIDE_NOTE_STYLE:
            continue
        if form_no is not None:
            term_match = _FORM_TERM_RE.match(paragraph.text)
            if term_match:
                flush_term()
                term = {
                    "no": term_match.group(1),
                    "heading": _clean(term_match.group(2)),
                    "body": [],
                }
                continue
            if term is not None:
                term["body"].append(text)
                continue
```

(`paragraph.text` is the raw text - the tab survives there; `text` is
already `_clean`ed by the loop.) 4. At the end of the function, call
`flush_term()` next to the existing `flush()`.

The existing branches are otherwise untouched: body sections, S3-S5
schedule clauses, part/division tracking, the toc skip and the
fallback gate all behave byte-identically (the form branches are dead
code while `schedule_no`/`form_no` are None).

Extend the module docstring with one sentence: "Prescribed forms inside
a schedule (New Form Heading paragraphs) yield their numbered terms as
S{sch}-F{form}-T{term}, with the form identity in division and the
schedule heading in part."

- [ ] **Step 4: All tests green**

Run: `uv run pytest tests/test_parser_vic.py -v`
Expected: every test passes, including the four new ones (real cache:
F1 >= 30, F2 >= 38, total >= 200).

- [ ] **Step 5: Full suite, ruff, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/ingest/parser_vic.py tests/test_parser_vic.py
git commit -m "Parse VIC prescribed-form terms into form-scoped keys"
git push origin main
```

---

### Task 2: Wipe and rebuild the VIC Regulations from cache

**Files:**
- Modify: `scripts/rebuild-nsw-corpus.py` -> rename to `scripts/rebuild-corpus.py` (slug arguments)
- Modify: `tests/fixtures/corpus.dump` (refreshed)

**Interfaces:**
- Consumes: Task 1's parser; `uv run python -m app.ingest vic`; `scripts/refresh-corpus-dump.sh`.
- Produces: dev corpus with VIC form terms point-in-time; refreshed CI dump; the generalised rebuild script Task 3 reuses against production.

- [ ] **Step 1: Generalise the rebuild script**

Rename `scripts/rebuild-nsw-corpus.py` to `scripts/rebuild-corpus.py`
(`git mv`) and replace the hardcoded slug tuple with arguments:

```python
"""Wipe the given instruments and re-ingest from cache.

Usage: PYTHONPATH=. uv run python scripts/rebuild-corpus.py <slug> [<slug> ...]
Point DATABASE_URL elsewhere (e.g. the production tunnel) to rebuild
that store. Follow with the matching ingest command
(python -m app.ingest nsw / vic) - it is cache-first.
"""

import asyncio
import sys

from sqlalchemy import delete, select

from app.core.db import async_session_factory
from app.models import Act, IngestedVersion, Section


async def wipe(slugs: list[str]) -> None:
    async with async_session_factory() as session:
        for slug in slugs:
            act = (await session.execute(select(Act).where(Act.slug == slug))).scalar_one_or_none()
            if act is None:
                print(f"absent {slug}")
                continue
            await session.execute(delete(Section).where(Section.act_id == act.id))
            await session.execute(delete(IngestedVersion).where(IngestedVersion.act_id == act.id))
            await session.execute(delete(Act).where(Act.id == act.id))
            print(f"wiped {slug}")
        await session.commit()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: rebuild-corpus.py <slug> [<slug> ...]")
    asyncio.run(wipe(sys.argv[1:]))
```

(Keep the model-name adjustments the original script already made, if
its imports differ from the above - the original is the source of
truth for those names.)

- [ ] **Step 2: Before-counts, wipe, rebuild**

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
        act = (await s.execute(select(Act).where(Act.slug == "residential-tenancies-regulations-2021"))).scalar_one()
        non = (await s.execute(select(func.count()).select_from(Section).where(Section.act_id == act.id, ~Section.section_no.like("S%")))).scalar_one()
        sched = (await s.execute(select(func.count()).select_from(Section).where(Section.act_id == act.id, Section.section_no.like("S%")))).scalar_one()
        print("regs before: non-schedule", non, "schedule", sched)
    await engine.dispose()

asyncio.run(main())
EOF
PYTHONPATH=. uv run python scripts/rebuild-corpus.py residential-tenancies-regulations-2021
uv run python -m app.ingest vic
```

Expected: the Act's versions all report skipped (already ingested); the
Regulations reload every cached version with the loader guards holding.
A duplicate-key guard firing is a parser keyspace bug - stop, report
BLOCKED.

- [ ] **Step 3: After-probes**

```bash
uv run python - <<'EOF'
import asyncio
from datetime import date, timedelta
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.config import settings
from app.models import Act, Section
from app.services.legislation import section_at

REG = "residential-tenancies-regulations-2021"

async def main():
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        act = (await s.execute(select(Act).where(Act.slug == REG))).scalar_one()
        non = (await s.execute(select(func.count()).select_from(Section).where(Section.act_id == act.id, ~Section.section_no.like("S%")))).scalar_one()
        plain_sched = (await s.execute(select(func.count()).select_from(Section).where(Section.act_id == act.id, Section.section_no.like("S%"), ~Section.section_no.like("%-F%")))).scalar_one()
        current_f1 = (await s.execute(select(func.count()).select_from(Section).where(Section.act_id == act.id, Section.section_no.like("S1-F1-T%"), Section.valid_to.is_(None)))).scalar_one()
        current_f2 = (await s.execute(select(func.count()).select_from(Section).where(Section.act_id == act.id, Section.section_no.like("S1-F2-T%"), Section.valid_to.is_(None)))).scalar_one()
        current_forms = (await s.execute(select(func.count()).select_from(Section).where(Section.act_id == act.id, Section.section_no.like("%-F%"), Section.valid_to.is_(None)))).scalar_one()
        print("regs after: non-schedule", non, "plain-schedule", plain_sched, "| current F1", current_f1, "F2", current_f2, "all-form", current_forms)
        rent = await section_at(s, REG, "S1-F1-T6", date(2026, 8, 7))
        print("S1-F1-T6 today:", rent.heading if rent else "MISSING")
        rows = (
            await s.execute(
                select(Section.section_no, func.min(Section.valid_from))
                .where(Section.act_id == act.id, Section.section_no.like("S1-F1-T%"))
                .group_by(Section.section_no)
                .order_by(func.min(Section.valid_from).desc())
            )
        ).all()
        newest_no, boundary = rows[0]
        pre = await section_at(s, REG, newest_no, boundary - timedelta(days=1))
        post = await section_at(s, REG, newest_no, boundary)
        print("latest-born F1 term:", newest_no, "boundary", boundary, "|", "absent" if pre is None else "PRESENT-EARLY", "->", "hit" if post else "MISS")
    await engine.dispose()

asyncio.run(main())
EOF
```

Expected: non-schedule and plain-schedule counts match the before-probe
(body and S3-S5 rows reproduced); current F1 = 32, F2 = 40, all-form
403; `S1-F1-T6` resolves today (its heading is the Rent term);
the latest-born F1 term's boundary flips absent-to-hit (the 123/2025
amendment guarantees at least one 2025 boundary). If the newest term
was born in the first cached version, pick the next row down - the
point is one genuine absent-to-hit flip. Record every number.

- [ ] **Step 4: Full suite, dump refresh, commit**

```bash
uv run pytest
CI=true uv run pytest tests/test_corpus_ci_guard.py -v
./scripts/refresh-corpus-dump.sh
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add scripts/ tests/fixtures/corpus.dump
git commit -m "Rebuild the VIC Regulations with form terms and refresh the CI dump"
git push origin main
```

Expected: suite fully green (existing VIC rule/clause tests query
body sections by slug + section_no and survive the UUID churn); the
dump grows modestly; CI green against the refreshed dump.

---

### Task 3: Production rebuild and acceptance (interactive)

No repo changes except the ledger and memory. Run by the controller.

- [ ] **Step 1: Rebuild production over the tunnel**

```bash
ssh -f -N -o ExitOnForwardFailure=yes -L 15433:127.0.0.1:5432 deploy@168.144.169.66
DATABASE_URL=<tunnel url> PYTHONPATH=. uv run python scripts/rebuild-corpus.py residential-tenancies-regulations-2021
DATABASE_URL=<tunnel url> uv run python -m app.ingest vic
```

- [ ] **Step 2: Production acceptance, then CLOSE THE TUNNEL**

`GET https://api.leasekoala.com/v1/legislation/sections?act=residential-tenancies-regulations-2021&section_no=S1-F1-T6&as_at=<today>`
hits with the Rent heading; the Task 2 boundary pair reproduces
(pre-boundary 404, on-boundary hit). Then:

```bash
pkill -f "15433:127.0.0.1:5432"
```

and kickstart the monitor
(`launchctl kickstart -k gui/$(id -u)/com.lease-monitor`); its log
reports no-new-versions for all four instruments.

- [ ] **Step 3: Ledger and memory**

Append completion to `.superpowers/sdd/progress.md`; update the
milestone memory: Regulation-schedules (b) done, (c) comparison family
next with its recorded carry-ins.

---

## Self-review

- Spec coverage: form scopes/titles/PART handling and the raw-tab term
  regex (Task 1); S{sch}-F{k}-T{m} keys, part/division per the
  VIC-consistent convention, Side Note skip scoped to forms (Task 1);
  S3-S5 and body parsing untouched (Task 1 tests); regs-only wipe +
  cache rebuild + guards (Task 2); count/point-in-time/empirical
  boundary probes incl. F1=32/F2=40 (Task 2); CI dump refresh (Task 2);
  production rebuild + endpoint acceptance + tunnel close + monitor
  (Task 3); table-content limitation needs no task (accepted).
- Placeholders: none - the one source-of-truth note (rebuild script's
  import names) points at the existing file being renamed.
- Type consistency: `flush_term` reads the loop's `part`/`schedule_no`
  state; key shape identical across parser code, all four tests, both
  probe scripts, and the acceptance URL; `rebuild-corpus.py <slug>...`
  usage identical in Tasks 2 and 3.
