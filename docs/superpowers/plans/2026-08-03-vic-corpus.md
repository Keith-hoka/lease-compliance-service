# VIC Corpus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest Victoria's Residential Tenancies Act 1997 and Residential Tenancies Regulations 2021 into the existing temporal corpus with full point-in-time history, a daily monitor, and production rollout.

**Architecture:** A new httpx fetcher (no browser - the VIC site answers plain GETs) and a new python-docx parser emit the same `ParsedSection` the NSW pipeline uses, so the loader, temporal diff, and `section_at()` are untouched. The registry grows a VIC instrument list; the ingest and monitor CLIs gain a `vic` choice; the launchd wrapper runs both jurisdictions in one tunnel session.

**Tech Stack:** httpx (existing), selectolax (existing), python-docx (new), respx (tests).

**Spec:** `docs/superpowers/specs/2026-08-03-vic-corpus-design.md`

## Global Constraints

- Python 3.12+, `uv` only. TDD: failing test first, watch it fail for the right reason, then implement.
- Every task ends: full suite (`uv run pytest`) -> ruff sequence (`uv run ruff format .` -> `uv run ruff check --fix .` -> `uv run ruff check .` -> `uv run ruff format --check .`) -> commit -> push origin main -> CI green.
- No emojis anywhere. Docstrings over comments. Fail loud in CLI paths - no retry loops.
- Only new dependency: `python-docx`.
- NSW paths untouched: `app/ingest/fetcher.py` and `app/ingest/parser.py` must not change; `ParsedSection` keeps its exact five fields.
- VIC slugs exactly: `residential-tenancies-act-1997`, `residential-tenancies-regulations-2021`; jurisdiction string `VIC`; cache under `data/raw/vic/<slug>/<version-number>.docx`.
- Landing URLs exactly: `https://www.legislation.vic.gov.au/in-force/acts/residential-tenancies-act-1997` and `https://www.legislation.vic.gov.au/in-force/statutory-rules/residential-tenancies-regulations-2021`.
- The `effective_date` from the site's version history is the loader's `version_date` (`valid_from`); filenames carry the site's version number, not the date.

---

### Task 1: Registry - landing URLs as data, VIC instruments

**Files:**
- Modify: `app/ingest/registry.py`
- Test: `tests/test_registry.py` (create)

**Interfaces:**
- Consumes: existing `ensure_act(session, instrument)`.
- Produces: instrument dicts gain a `landing_url` key; `VIC_INSTRUMENTS` list; `INSTRUMENTS = {"nsw": NSW_INSTRUMENTS, "vic": VIC_INSTRUMENTS}`. Tasks 4-5 look instruments up via `INSTRUMENTS[jurisdiction]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_registry.py`:

```python
from sqlalchemy import select

from app.ingest.registry import INSTRUMENTS, NSW_INSTRUMENTS, VIC_INSTRUMENTS, ensure_act
from app.models import Act


def test_instrument_map_covers_both_jurisdictions():
    assert INSTRUMENTS["nsw"] is NSW_INSTRUMENTS
    assert INSTRUMENTS["vic"] is VIC_INSTRUMENTS


def test_every_instrument_carries_a_landing_url():
    for instruments in INSTRUMENTS.values():
        for instrument in instruments:
            assert instrument["landing_url"].startswith("https://")


def test_vic_instruments_pinned():
    slugs = [i["slug"] for i in VIC_INSTRUMENTS]
    assert slugs == [
        "residential-tenancies-act-1997",
        "residential-tenancies-regulations-2021",
    ]
    assert all(i["jurisdiction"] == "VIC" for i in VIC_INSTRUMENTS)


async def test_ensure_act_uses_landing_url(db_session):
    instrument = VIC_INSTRUMENTS[0]
    act = await ensure_act(db_session, instrument)
    await db_session.commit()
    stored = (
        await db_session.execute(select(Act).where(Act.slug == instrument["slug"]))
    ).scalar_one()
    assert stored.source_url == instrument["landing_url"]
    assert stored.jurisdiction == "VIC"
    again = await ensure_act(db_session, instrument)
    assert again.id == act.id
```

- [ ] **Step 2: Run to watch them fail**

Run: `uv run pytest tests/test_registry.py -v`
Expected: ImportError (`VIC_INSTRUMENTS`/`INSTRUMENTS` do not exist).

- [ ] **Step 3: Implement**

`app/ingest/registry.py` becomes:

```python
from sqlalchemy import select

from app.ingest.fetcher import LANDING_URL_TEMPLATE
from app.models import Act

NSW_INSTRUMENTS = [
    {
        "jurisdiction": "NSW",
        "slug": "act-2010-042",
        "title": "Residential Tenancies Act 2010",
        "landing_url": LANDING_URL_TEMPLATE.format(slug="act-2010-042"),
    },
    {
        "jurisdiction": "NSW",
        "slug": "sl-2019-0629",
        "title": "Residential Tenancies Regulation 2019",
        "landing_url": LANDING_URL_TEMPLATE.format(slug="sl-2019-0629"),
    },
]

VIC_INSTRUMENTS = [
    {
        "jurisdiction": "VIC",
        "slug": "residential-tenancies-act-1997",
        "title": "Residential Tenancies Act 1997",
        "landing_url": (
            "https://www.legislation.vic.gov.au/in-force/acts/residential-tenancies-act-1997"
        ),
    },
    {
        "jurisdiction": "VIC",
        "slug": "residential-tenancies-regulations-2021",
        "title": "Residential Tenancies Regulations 2021",
        "landing_url": (
            "https://www.legislation.vic.gov.au/in-force/statutory-rules/"
            "residential-tenancies-regulations-2021"
        ),
    },
]

INSTRUMENTS = {"nsw": NSW_INSTRUMENTS, "vic": VIC_INSTRUMENTS}


async def ensure_act(session, instrument: dict) -> Act:
    """The registered row for a legislative instrument, created on first use."""
    act = (
        await session.execute(select(Act).where(Act.slug == instrument["slug"]))
    ).scalar_one_or_none()
    if act is None:
        act = Act(
            jurisdiction=instrument["jurisdiction"],
            slug=instrument["slug"],
            title=instrument["title"],
            source_url=instrument["landing_url"],
        )
        session.add(act)
        await session.flush()
    return act
```

(`ensure_act` previously spread `**instrument`; the dict now carries the
extra `landing_url` key, so fields are passed explicitly.)

- [ ] **Step 4: Run the tests, then the full suite**

`uv run pytest tests/test_registry.py -v` then `uv run pytest`.
Expected: registry tests pass; full suite passes (NSW ingest tests keep
passing because `landing_url` for NSW is the same template value the old
code computed inside `ensure_act`).

- [ ] **Step 5: Ruff, commit, push, CI**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/ingest/registry.py tests/test_registry.py
git commit -m "Carry landing URLs on instruments and register VIC"
git push origin main
```

---

### Task 2: VIC fetcher (httpx, cache-first)

**Files:**
- Create: `app/ingest/fetcher_vic.py`
- Test: `tests/test_fetcher_vic.py`

**Interfaces:**
- Produces: `VersionInfo(number: str, effective_date: date, status: str)`; `list_versions(landing_url) -> list[VersionInfo]` (ascending by date); `docx_url(landing_url, number) -> str`; `fetch_docx(url, cache_path: Path) -> bytes`. All synchronous (callers wrap in `asyncio.to_thread`, mirroring the NSW fetchers).

- [ ] **Step 1: Write the failing tests**

`tests/test_fetcher_vic.py`:

```python
from datetime import date
from pathlib import Path

import respx
from httpx import Response

from app.ingest.fetcher_vic import VersionInfo, docx_url, fetch_docx, list_versions

LANDING = "https://www.legislation.vic.gov.au/in-force/acts/residential-tenancies-act-1997"

LANDING_HTML = f"""
<html><body>
<div class="version-history">
  <a href="{LANDING}/113">1 July 2026 113 In force</a>
  <a href="{LANDING}/113#rpl-above-body">1 July 2026 113 In force</a>
  <a href="{LANDING}/112">30 June 2026 112 Superseded</a>
  <a href="{LANDING}/098">29 Mar 2021 098 Superseded</a>
  <a href="{LANDING}">not a version row</a>
</div>
</body></html>
"""

VERSION_HTML = """
<html><body>
<a href="https://content.legislation.vic.gov.au/sites/default/files/2026-07/97-109aa113-authorised.pdf">pdf</a>
<a href="https://content.legislation.vic.gov.au/sites/default/files/2026-07/97-109a113.docx">docx</a>
</body></html>
"""


@respx.mock
def test_list_versions_parses_rows_ascending():
    respx.get(LANDING).mock(return_value=Response(200, text=LANDING_HTML))
    versions = list_versions(LANDING)
    assert versions == [
        VersionInfo("098", date(2021, 3, 29), "Superseded"),
        VersionInfo("112", date(2026, 6, 30), "Superseded"),
        VersionInfo("113", date(2026, 7, 1), "In force"),
    ]


@respx.mock
def test_docx_url_skips_authorised_pdf():
    respx.get(f"{LANDING}/113").mock(return_value=Response(200, text=VERSION_HTML))
    url = docx_url(LANDING, "113")
    assert url.endswith("/97-109a113.docx")


@respx.mock
def test_fetch_docx_caches(tmp_path: Path):
    url = "https://content.legislation.vic.gov.au/files/97-109a113.docx"
    route = respx.get(url).mock(return_value=Response(200, content=b"DOCXBYTES"))
    cache = tmp_path / "vic" / "residential-tenancies-act-1997" / "113.docx"

    first = fetch_docx(url, cache)
    assert first == b"DOCXBYTES"
    assert cache.read_bytes() == b"DOCXBYTES"
    assert route.call_count == 1

    second = fetch_docx(url, cache)
    assert second == b"DOCXBYTES"
    assert route.call_count == 1
```

- [ ] **Step 2: Watch them fail** - `uv run pytest tests/test_fetcher_vic.py -v` -> ModuleNotFoundError.

- [ ] **Step 3: Implement**

`app/ingest/fetcher_vic.py`:

```python
"""VIC legislation fetchers: plain httpx, no browser needed."""

import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import httpx
from selectolax.parser import HTMLParser

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
FETCH_PAUSE_SECONDS = 1.0

_DATE_RE = re.compile(r"(\d{1,2} \w+ \d{4})")


@dataclass(frozen=True)
class VersionInfo:
    number: str
    effective_date: date
    status: str


def _get(url: str) -> httpx.Response:
    response = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return response


def _parse_date(text: str) -> date:
    raw = _DATE_RE.search(text).group(1)
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable version date: {raw!r}")


def list_versions(landing_url: str) -> list[VersionInfo]:
    """Version-history rows from the landing page, ascending by date."""
    html = _get(landing_url).text
    row_href = re.compile(rf"^{re.escape(landing_url)}/(\d+)$")
    seen: dict[str, VersionInfo] = {}
    for node in HTMLParser(html).css("a"):
        href = node.attributes.get("href", "") or ""
        match = row_href.match(href)
        if match is None:
            continue
        number = match.group(1)
        if number in seen:
            continue
        text = node.text()
        status = "In force" if "In force" in text else "Superseded"
        seen[number] = VersionInfo(number, _parse_date(text), status)
    return sorted(seen.values(), key=lambda v: v.effective_date)


def docx_url(landing_url: str, number: str) -> str:
    """The whole-instrument DOCX link on a version page."""
    html = _get(f"{landing_url}/{number}").text
    candidates = [
        node.attributes.get("href", "") or ""
        for node in HTMLParser(html).css("a")
    ]
    docx = [
        href
        for href in candidates
        if "content.legislation.vic.gov.au" in href
        and href.endswith(".docx")
        and "authorised" not in href
    ]
    return docx[0]


def fetch_docx(url: str, cache_path: Path) -> bytes:
    """Cache-first download; full re-ingests never re-hit the site."""
    if cache_path.exists():
        return cache_path.read_bytes()
    data = _get(url).content
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    time.sleep(FETCH_PAUSE_SECONDS)
    return data
```

- [ ] **Step 4: Run the tests** - 3 passed. Note the anchor with the
`#rpl-above-body` fragment must not produce a duplicate (the strict
`^...$` href regex rejects it).

- [ ] **Step 5: Full suite, ruff, commit** - "Add the VIC httpx fetcher"; push; CI green.

---

### Task 3: DOCX parser

**Files:**
- Create: `app/ingest/parser_vic.py`
- Modify: `pyproject.toml` via `uv add python-docx`
- Test: `tests/test_parser_vic.py`

**Interfaces:**
- Consumes: `ParsedSection` from `app.ingest.parser` (unchanged).
- Produces: `parse_docx(data: bytes) -> list[ParsedSection]`; module constant `SECTION_HEADING_STYLES: tuple[str, ...]` - starts empty (regex path active); the rollout spike (Task 6) pins real style names into it.

- [ ] **Step 1: `uv add python-docx`**

- [ ] **Step 2: Write the failing tests**

`tests/test_parser_vic.py`:

```python
import io

from docx import Document

from app.ingest.parser_vic import parse_docx


def build_docx(paragraphs: list[tuple[str | None, str]]) -> bytes:
    """A minimal DOCX from (style_name, text) rows; None = default style."""
    doc = Document()
    for style_name, text in paragraphs:
        paragraph = doc.add_paragraph(text)
        if style_name is not None:
            from docx.enum.style import WD_STYLE_TYPE

            styles = doc.styles
            if style_name not in [s.name for s in styles]:
                styles.add_style(style_name, WD_STYLE_TYPE.PARAGRAPH)
            paragraph.style = styles[style_name]
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def test_sections_split_with_part_and_division_tracking():
    data = build_docx(
        [
            (None, "Part 2—Tenancy agreements"),
            (None, "Division 1—General"),
            (None, "26 Application of Part"),
            (None, "This Part applies to all agreements."),
            (None, "27B Prohibited terms"),
            (None, "A term listed below must not be included."),
            (None, "Penalty: 60 penalty units."),
        ]
    )
    sections = parse_docx(data)
    assert [s.section_no for s in sections] == ["26", "27B"]
    assert sections[1].heading == "Prohibited terms"
    assert "must not be included" in sections[1].body_text
    assert "Penalty" in sections[1].body_text
    assert sections[0].part == "Part 2—Tenancy agreements"
    assert sections[0].division == "Division 1—General"


def test_toc_is_skipped_and_collection_starts_at_first_part():
    data = build_docx(
        [
            ("TOC 1", "27B Prohibited terms 55"),
            (None, "27B Prohibited terms"),
            (None, "Part 1—Preliminary"),
            (None, "1 Purposes"),
            (None, "The purposes of this Act are set out."),
        ]
    )
    sections = parse_docx(data)
    assert [s.section_no for s in sections] == ["1"]


def test_parsing_stops_at_endnotes():
    data = build_docx(
        [
            (None, "Part 1—Preliminary"),
            (None, "1 Purposes"),
            (None, "Body text."),
            (None, "Endnotes"),
            (None, "2 This looks like a section but is history."),
        ]
    )
    sections = parse_docx(data)
    assert [s.section_no for s in sections] == ["1"]


def test_schedule_becomes_part_label():
    data = build_docx(
        [
            (None, "Part 1—Preliminary"),
            (None, "1 Purposes"),
            (None, "Body."),
            (None, "Schedule 1—Transitional provisions"),
            (None, "1 Saved instruments"),
            (None, "Schedule body."),
        ]
    )
    sections = parse_docx(data)
    assert sections[-1].part == "Schedule 1—Transitional provisions"
    assert sections[-1].section_no == "1"
    assert sections[-1].division is None


def test_repealed_placeholder_loads_as_shown():
    data = build_docx(
        [
            (None, "Part 1—Preliminary"),
            (None, "27A Repealed"),
            (None, "* * * * *"),
        ]
    )
    sections = parse_docx(data)
    assert sections[0].section_no == "27A"
    assert sections[0].heading == "Repealed"


def test_pinned_style_wins_over_regex():
    from app.ingest import parser_vic

    original = parser_vic.SECTION_HEADING_STYLES
    parser_vic.SECTION_HEADING_STYLES = ("SectionHead",)
    try:
        data = build_docx(
            [
                (None, "Part 1—Preliminary"),
                ("SectionHead", "5 Definitions"),
                (None, "14 days means a fortnight in this test body."),
            ]
        )
        sections = parse_docx(data)
        assert [s.section_no for s in sections] == ["5"]
        assert "14 days" in sections[0].body_text
    finally:
        parser_vic.SECTION_HEADING_STYLES = original
```

The last test encodes the safety property: once real style names are
pinned, body paragraphs that merely start with a number ("14 days...")
can no longer be misread as section headings.

- [ ] **Step 3: Watch them fail** - ModuleNotFoundError for `app.ingest.parser_vic`.

- [ ] **Step 4: Implement**

`app/ingest/parser_vic.py`:

```python
"""Parse a whole VIC instrument DOCX into sections.

Classification is style-first: when SECTION_HEADING_STYLES is pinned
(by the rollout spike, from real authorised-version style names), only
those styles start a section. While it is empty, a regex fallback
matches headings like "27B Prohibited terms".
"""

import io
import re

from docx import Document

from app.ingest.parser import ParsedSection

SECTION_HEADING_STYLES: tuple[str, ...] = ()

_PART_RE = re.compile(r"^Part \d+[A-Z]*—")
_DIVISION_RE = re.compile(r"^Division \d+[A-Z]*—")
_SCHEDULE_RE = re.compile(r"^Schedule \d+[A-Z]*—")
_SECTION_RE = re.compile(r"^(\d+[A-Z]*)\s+(\S.*)$")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_section_heading(style_name: str, text: str) -> bool:
    if SECTION_HEADING_STYLES:
        return style_name in SECTION_HEADING_STYLES and bool(_SECTION_RE.match(text))
    return bool(_SECTION_RE.match(text))


def parse_docx(data: bytes) -> list[ParsedSection]:
    document = Document(io.BytesIO(data))
    sections: list[ParsedSection] = []
    part: str | None = None
    division: str | None = None
    current: dict | None = None
    started = False

    def flush() -> None:
        nonlocal current
        if current is not None:
            sections.append(
                ParsedSection(
                    section_no=current["section_no"],
                    heading=current["heading"],
                    body_text=_clean(" ".join(current["body"])),
                    part=current["part"],
                    division=current["division"],
                )
            )
            current = None

    for paragraph in document.paragraphs:
        style_name = paragraph.style.name if paragraph.style is not None else ""
        if style_name.lower().startswith("toc"):
            continue
        text = _clean(paragraph.text)
        if not text:
            continue
        if text == "Endnotes":
            break
        if _PART_RE.match(text):
            flush()
            part, division, started = text, None, True
            continue
        if _SCHEDULE_RE.match(text):
            flush()
            part, division, started = text, None, True
            continue
        if _DIVISION_RE.match(text):
            flush()
            division = text
            continue
        if not started:
            continue
        match = _SECTION_RE.match(text)
        if match and _is_section_heading(style_name, text):
            flush()
            current = {
                "section_no": match.group(1),
                "heading": match.group(2),
                "part": part,
                "division": division,
                "body": [],
            }
            continue
        if current is not None:
            current["body"].append(text)
    flush()
    return sections
```

- [ ] **Step 5: Run the tests** - 6 passed. Then full suite, ruff, commit
"Add the VIC DOCX parser"; push; CI green.

---

### Task 4: Ingest CLI - vic dispatch

**Files:**
- Modify: `app/ingest/__main__.py`
- Test: `tests/test_ingest_vic.py`

**Interfaces:**
- Consumes: `INSTRUMENTS` (Task 1), `fetcher_vic` (Task 2), `parse_docx` (Task 3), existing `load_version`/`ensure_act`.
- Produces: `uv run python -m app.ingest vic [--limit-versions N]`; internal `run_vic(limit_versions)` and per-instrument `load_all_vic(instrument, versions, cache_dir)` used by the monitor task.

- [ ] **Step 1: Write the failing test**

`tests/test_ingest_vic.py`:

```python
from datetime import date

from sqlalchemy import select

from app.ingest.fetcher_vic import VersionInfo
from app.ingest.__main__ import load_all_vic
from app.models import Act, Section
from tests.test_parser_vic import build_docx

V1 = build_docx(
    [
        (None, "Part 1—Preliminary"),
        (None, "1 Purposes"),
        (None, "Original body."),
    ]
)
V2 = build_docx(
    [
        (None, "Part 1—Preliminary"),
        (None, "1 Purposes"),
        (None, "Amended body."),
    ]
)


async def test_load_all_vic_builds_the_timeline(db_session, tmp_path, monkeypatch):
    from app.ingest import __main__ as ingest_main

    bytes_by_number = {"001": V1, "002": V2}
    monkeypatch.setattr(
        ingest_main, "docx_url", lambda landing, number: f"https://x/{number}.docx"
    )
    monkeypatch.setattr(
        ingest_main,
        "fetch_docx",
        lambda url, cache_path: bytes_by_number[url.split("/")[-1].removesuffix(".docx")],
    )

    instrument = {
        "jurisdiction": "VIC",
        "slug": "residential-tenancies-act-1997",
        "title": "Residential Tenancies Act 1997",
        "landing_url": "https://x/landing",
    }
    versions = [
        VersionInfo("001", date(2021, 1, 1), "Superseded"),
        VersionInfo("002", date(2022, 1, 1), "In force"),
    ]
    await load_all_vic(db_session, instrument, versions, tmp_path)

    act = (
        await db_session.execute(
            select(Act).where(Act.slug == "residential-tenancies-act-1997")
        )
    ).scalar_one()
    rows = (
        (await db_session.execute(select(Section).where(Section.act_id == act.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    closed = next(r for r in rows if r.valid_to is not None)
    open_row = next(r for r in rows if r.valid_to is None)
    assert closed.valid_from == date(2021, 1, 1)
    assert closed.valid_to == date(2022, 1, 1)
    assert "Original" in closed.body_text
    assert open_row.valid_from == date(2022, 1, 1)
    assert "Amended" in open_row.body_text
```

- [ ] **Step 2: Watch it fail** - ImportError (`load_all_vic`).

- [ ] **Step 3: Implement**

In `app/ingest/__main__.py`: add imports and the vic path; the docstring
usage line becomes `uv run python -m app.ingest {nsw|vic} [--limit-versions N]`.

```python
from app.ingest.fetcher_vic import docx_url, fetch_docx, list_versions
from app.ingest.parser_vic import parse_docx
from app.ingest.registry import INSTRUMENTS, ensure_act
```

(`NSW_INSTRUMENTS` import switches to `INSTRUMENTS`; the NSW loop reads
`INSTRUMENTS["nsw"]`.)

```python
async def load_all_vic(session, instrument: dict, versions, cache_dir: Path) -> None:
    act = await ensure_act(session, instrument)
    for version in versions:
        url = docx_url(instrument["landing_url"], version.number)
        data = fetch_docx(url, cache_dir / f"{version.number}.docx")
        sections = parse_docx(data)
        stats = await load_version(session, act.id, version.effective_date, sections)
        print(f"{instrument['slug']} {version.effective_date}: sections={len(sections)} {stats}")
    await session.commit()


async def run_vic(limit_versions: int | None) -> None:
    for instrument in INSTRUMENTS["vic"]:
        versions = await asyncio.to_thread(list_versions, instrument["landing_url"])
        if limit_versions:
            versions = versions[-limit_versions:]
        cache_dir = Path("data/raw/vic") / instrument["slug"]
        async with async_session_factory() as session:
            await load_all_vic(session, instrument, versions, cache_dir)
```

(`--limit-versions` for vic takes the LATEST N - the spike wants the
newest version; the NSW path keeps its existing head-of-list behaviour.)
`main()` gains `choices=["nsw", "vic"]` and dispatches:

```python
    if args.jurisdiction == "vic":
        asyncio.run(run_vic(args.limit_versions))
    else:
        asyncio.run(run(args.limit_versions))
```

Note: `load_all_vic` calls the sync fetchers inline (they run inside
`asyncio.run` via the CLI); wrap the two fetcher calls in
`await asyncio.to_thread(...)` to keep the event loop responsive:

```python
        url = await asyncio.to_thread(docx_url, instrument["landing_url"], version.number)
        data = await asyncio.to_thread(fetch_docx, url, cache_dir / f"{version.number}.docx")
```

(The test monkeypatches `docx_url`/`fetch_docx` with plain lambdas -
`asyncio.to_thread` runs them fine.)

- [ ] **Step 4: Run the test, then the full suite** - both green.

- [ ] **Step 5: Ruff, commit** - "Ingest VIC through the docx pipeline"; push; CI green.

---

### Task 5: Monitor - vic corpus check, launchd wrapper

**Files:**
- Modify: `app/monitor/__main__.py`
- Modify: `deploy/launchd/monitor-remote.sh`
- Test: `tests/test_monitor_vic.py`

**Interfaces:**
- Consumes: `INSTRUMENTS`, `fetcher_vic.list_versions`, `load_all_vic` semantics (re-implemented inline against missing dates), existing `new_version_dates`, `run_monitor`.
- Produces: `uv run python -m app.monitor vic [--skip-fetch]`; `refresh_corpus_vic()`.

- [ ] **Step 1: Write the failing test**

`tests/test_monitor_vic.py`:

```python
from datetime import date

from sqlalchemy import select

from app.ingest.fetcher_vic import VersionInfo
from app.models import Act, IngestedVersion, Section
from tests.test_parser_vic import build_docx

DOCX = build_docx(
    [
        (None, "Part 1—Preliminary"),
        (None, "1 Purposes"),
        (None, "Fresh body."),
    ]
)


async def test_refresh_corpus_vic_ingests_only_missing(db_session, tmp_path, monkeypatch, capsys):
    from app.monitor import __main__ as monitor_main

    monkeypatch.setattr(
        monitor_main,
        "list_versions",
        lambda landing_url: [VersionInfo("001", date(2021, 1, 1), "In force")],
    )
    monkeypatch.setattr(monitor_main, "docx_url", lambda landing, number: "https://x/1.docx")
    monkeypatch.setattr(monitor_main, "fetch_docx", lambda url, cache_path: DOCX)
    monkeypatch.setattr(monitor_main, "VIC_CACHE_ROOT", tmp_path)

    await monitor_main.refresh_corpus_vic(session_factory=lambda: db_session_context(db_session))

    acts = (await db_session.execute(select(Act))).scalars().all()
    assert {a.slug for a in acts} == {
        "residential-tenancies-act-1997",
        "residential-tenancies-regulations-2021",
    }
    sections = (await db_session.execute(select(Section))).scalars().all()
    assert len(sections) == 2

    await monitor_main.refresh_corpus_vic(session_factory=lambda: db_session_context(db_session))
    out = capsys.readouterr().out
    assert out.count("no new versions") == 2
```

with this helper at the top of the file:

```python
from contextlib import asynccontextmanager


@asynccontextmanager
async def db_session_context(session):
    yield session
```

(`refresh_corpus_vic` takes a `session_factory` defaulting to
`async_session_factory` so the test injects the fixture session; the NSW
`refresh_corpus` keeps its current shape untouched.)

- [ ] **Step 2: Watch it fail** - AttributeError (`refresh_corpus_vic`).

- [ ] **Step 3: Implement**

In `app/monitor/__main__.py` add:

```python
from app.ingest.fetcher_vic import docx_url, fetch_docx, list_versions
from app.ingest.parser_vic import parse_docx
from app.ingest.registry import INSTRUMENTS

VIC_CACHE_ROOT = Path("data/raw/vic")


async def refresh_corpus_vic(session_factory=async_session_factory) -> None:
    """Ingest any VIC versions published since the last run. No browser."""
    for instrument in INSTRUMENTS["vic"]:
        versions = await asyncio.to_thread(list_versions, instrument["landing_url"])
        async with session_factory() as session:
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
            missing = [v for v in versions if v.effective_date not in ingested]
            if not missing:
                print(f"corpus: {instrument['slug']} no new versions")
                await session.commit()
                continue
            cache_dir = VIC_CACHE_ROOT / instrument["slug"]
            for version in missing:
                url = await asyncio.to_thread(
                    docx_url, instrument["landing_url"], version.number
                )
                data = await asyncio.to_thread(
                    fetch_docx, url, cache_dir / f"{version.number}.docx"
                )
                stats = await load_version(
                    session, act.id, version.effective_date, parse_docx(data)
                )
                print(f"corpus: {instrument['slug']} {version.effective_date} {stats}")
            await session.commit()
```

`main()` gains `choices=["nsw", "vic"]`; `run(skip_fetch)` becomes
`run(jurisdiction, skip_fetch)`: the corpus half calls `refresh_corpus()`
for nsw and `refresh_corpus_vic()` for vic; the monitor half calls
`run_monitor(session, jurisdiction.upper(), sydney_today())` exactly as
it does today for NSW. (The NSW loop keeps reading `NSW_INSTRUMENTS`;
only the import line may switch to `INSTRUMENTS["nsw"]` if ruff flags
the unused name.)

`deploy/launchd/monitor-remote.sh`: the run line becomes

```bash
for jurisdiction in nsw vic; do
    uv run python -m app.monitor "$jurisdiction"
done
```

- [ ] **Step 4: Run the test, then the full suite** - green. `bash -n deploy/launchd/monitor-remote.sh`.

- [ ] **Step 5: Ruff, commit** - "Monitor VIC legislation daily"; push; CI green.

---

### Task 6: Rollout (interactive) - spike, ingest, production, acceptance

No repo changes except the pinned style constant and the ledger. All
operator steps.

- [ ] **Step 1: Spike - pin the real section-heading styles**

```bash
uv run python -m app.ingest vic --limit-versions 1
uv run python - <<'EOF'
import collections, io
from pathlib import Path
from docx import Document

path = sorted(Path("data/raw/vic/residential-tenancies-act-1997").glob("*.docx"))[-1]
doc = Document(io.BytesIO(path.read_bytes()))
styles = collections.Counter(p.style.name for p in doc.paragraphs if p.text.strip())
for name, count in styles.most_common(30):
    print(f"{count:6} {name}")
EOF
```

Read the output; identify the style used by section-heading paragraphs
(cross-check by printing a few paragraphs of that style). Set
`SECTION_HEADING_STYLES` in `app/ingest/parser_vic.py` to the exact
names found (both instruments may differ - include both). Re-run the
limit-1 ingest (delete the two ingested rows first: simplest is
`uv run alembic ...`? No - just drop and re-create the local dev
database content for these acts:
`psql`-level `delete from sections where act_id in (select id from acts where jurisdiction='VIC'); delete from ingested_versions where act_id in (...); delete from acts where jurisdiction='VIC';`
via `docker exec rental_management_app-db-1 psql -U rental lease_compliance -c "..."`)
then verify the section count with the pinned styles: version 113 must
yield more than 400 sections and include 27B with heading
"Prohibited terms":

```bash
uv run python - <<'EOF'
import asyncio
from datetime import date
from app.core.db import async_session_factory
from app.services.legislation import section_at

async def check():
    async with async_session_factory() as session:
        s = await section_at(session, "residential-tenancies-act-1997", "27B", date(2026, 8, 3))
        assert s is not None and s.heading == "Prohibited terms", s
        print("27B ok:", s.heading)

asyncio.run(check())
EOF
```

Commit the pinned constant: "Pin VIC section heading styles from the spike"; push; CI green.

- [ ] **Step 2: Local full ingest**

```bash
uv run python -m app.ingest vic
```

Expected: every version of both instruments loads; spot-check the
LoadStats lines look like NSW's (inserts on amendment versions, zeros on
no-change versions). Re-run the 27B check above plus the pre-commencement
negative: `section_at(..., "27B", date(2020, 6, 1))` returns None.

- [ ] **Step 3: Production ingest**

```bash
SOCK=/tmp/lease-ingest-tunnel.sock
PGPASS=$(ssh deploy@168.144.169.66 "grep '^POSTGRES_PASSWORD=' /opt/lease-compliance/.env | cut -d= -f2-")
ssh -M -S "$SOCK" -f -N -o ExitOnForwardFailure=yes -L 15433:127.0.0.1:5432 deploy@168.144.169.66
DATABASE_URL="postgresql+asyncpg://postgres:${PGPASS}@localhost:15433/lease_compliance" \
  uv run python -m app.ingest vic
ssh -S "$SOCK" -O exit deploy@168.144.169.66
```

Expected: cache-warm run, minutes, no site traffic beyond version pages.

- [ ] **Step 4: Production acceptance pair**

```bash
APIKEY=$(ssh deploy@168.144.169.66 "grep '^API_KEYS=' /opt/lease-compliance/.env | cut -d= -f2- | cut -d: -f1")
curl -s "https://api.leasekoala.com/v1/legislation/sections?act=residential-tenancies-act-1997&section_no=27B&as_at=2026-08-03" -H "X-API-Key: ${APIKEY}" | head -c 200
curl -s -o /dev/null -w "%{http_code}\n" "https://api.leasekoala.com/v1/legislation/sections?act=residential-tenancies-act-1997&section_no=27B&as_at=2020-06-01" -H "X-API-Key: ${APIKEY}"
```

(The stored production key belongs to rentalapp; any active key works.)
Expected: the first returns the 27B JSON with heading "Prohibited
terms"; the second prints 404.

- [ ] **Step 5: Monitor kickstart**

```bash
launchctl kickstart gui/$(id -u)/com.lease-monitor
tail -5 ~/Library/Logs/lease-monitor.log
```

Expected: four `corpus: ... no new versions` lines (two NSW, two VIC)
and two `monitor: checked=... changed=...` lines.

- [ ] **Step 6: Ledger**

Append to `.superpowers/sdd/progress.md`: VIC corpus complete
(commits, pinned styles, prod ingest date, acceptance pair result).

---

## Self-review

- Spec coverage: instruments/registry (Task 1), fetcher three functions
  + politeness + cache (Task 2), parser incl. the three pitfalls and the
  style-pinning mechanism (Task 3 + Task 6 spike), CLI (Task 4), monitor
  + launchd (Task 5), rollout incl. the s 27B acceptance pair and
  kickstart (Task 6), testing matrix mapped to the four test files. Out
  of scope respected: no NSW fetcher/parser edits, no schema Literal
  change.
- Placeholders: none. `SECTION_HEADING_STYLES = ()` is a working state
  (regex path), not a placeholder; Task 6 pins it from real data.
- Type consistency: `VersionInfo(number, effective_date, status)` used
  identically in Tasks 2/4/5; `load_all_vic(session, instrument,
  versions, cache_dir)` matches its Task 4 test; `refresh_corpus_vic`'s
  `session_factory` kwarg matches the Task 5 test; `parse_docx(bytes)`
  everywhere.
