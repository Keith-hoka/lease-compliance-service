# VIC corpus design

Sub-project 1 of the VIC second-jurisdiction milestone (1 corpus ->
2 deterministic rules + API opening -> 3 clause audit -> 4 SaaS wiring;
each gets its own spec and plan). This stage ingests Victoria's
Residential Tenancies Act 1997 and Residential Tenancies Regulations
2021 into the existing temporal corpus, with full point-in-time
version history, a daily monitor, and production rollout.

Recon findings this design rests on (verified live 2026-08-03):
legislation.vic.gov.au lists full version history per instrument
(`/in-force/acts/residential-tenancies-act-1997/{091..113}`, each with
an effective date); each version publishes the whole instrument as one
DOCX on content.legislation.vic.gov.au; both the pages and the files
answer plain HTTP GETs (no bot wall), so VIC ingestion needs no headed
Chrome - unlike NSW.

## Instruments and registry

| slug (the site's own path segment) | title | jurisdiction |
|---|---|---|
| `residential-tenancies-act-1997` | Residential Tenancies Act 1997 | VIC |
| `residential-tenancies-regulations-2021` | Residential Tenancies Regulations 2021 | VIC |

- Data model unchanged: `Act.jurisdiction` already exists; sections keep
  the `valid_from`/`valid_to` half-open interval model; the loader's
  insert/close diff (`load_version` -> `LoadStats`) is reused untouched.
- `registry.py`: add `VIC_INSTRUMENTS` beside `NSW_INSTRUMENTS`; each
  instrument dict gains an explicit `landing_url` (replacing the
  NSW-template call inside `ensure_act` - NSW behaviour unchanged, the
  URL just becomes data). Add `INSTRUMENTS = {"nsw": ..., "vic": ...}`
  for CLI lookup.
- Version depth: the Act from the earliest version the site lists (091,
  April 2020) - this covers every commencement the VIC rules will pin
  (the 2021 reform package, incl. s 27B prohibited terms, landed at
  version 098, 29 March 2021). The Regulations 2021 from their first
  version. Shallower than NSW's 2011 depth, and sufficient.

## Fetcher (httpx, no browser)

New `app/ingest/fetcher_vic.py`, three functions, one `httpx.Client`
with a browser User-Agent:

1. `list_versions(landing_url) -> list[VersionInfo]` - GET the landing
   page, parse the Version history rows into
   `VersionInfo(number, effective_date, status)`, ascending by date.
   HTML parsing uses the same library the NSW parser already uses; no
   new parsing dependency.
2. `docx_url(landing_url, number) -> str` - GET
   `{landing_url}/{number}`, return the page's
   `content.legislation.vic.gov.au/...*.docx` link (when several
   candidates appear, the non-authorised file whose name carries the
   version number).
3. `fetch_docx(url, cache_path) -> bytes` - cache-first at
   `data/raw/vic/<slug>/<version>.docx`; download and write on miss.
   Full-history re-ingests never re-hit the site.

Politeness and failure: one second sleep between versions;
`raise_for_status()` and let errors propagate (CLI context, fail loud,
no retry loops).

Date semantics: the effective date shown in the version history is that
version's `valid_from`; the loader closes the previous version's
sections at the next `valid_from`. The "In force" version's sections
carry `valid_to = NULL`.

## DOCX parser

New dependency `python-docx`. New `app/ingest/parser_vic.py` with one
entry point:

```
parse_docx(data: bytes) -> list[ParsedSection]
```

It returns the same `ParsedSection` dataclass the NSW parser emits
(section_no, heading, body_text, part, division), so the loader,
temporal diff, and `section_at()` need no changes.

Classification walks paragraphs in order, style-first with regex
fallback:

- Part: text matching `^Part \d+[A-Z]*—` (em-dash) updates the current
  part label.
- Division: likewise for `^Division \d+[A-Z]*—`.
- Section start: the pinned section-heading style, or text matching
  `^(\d+[A-Z]*)\s+\S` (e.g. `27B Prohibited terms`) - closes the
  previous section, opens a new one.
- Everything else (including penalty, note, and example paragraphs)
  joins the current section's `body_text`, as NSW does.
- Schedules: from `^Schedule \d+—` onward the part label becomes
  `Schedule N`; numbered clauses inside split as sections.

Three pitfalls are design decisions, not implementation details:

1. Table of contents: paragraphs whose style name starts with `TOC`
   (case-insensitive) are skipped, and collection only begins at the
   first Part heading - ToC lines are indistinguishable from section
   headings by text alone.
2. Endnotes: parsing stops at the `Endnotes` heading; the amendment
   tables after it are full of false section numbers.
3. Repealed placeholders (`27A Repealed` plus asterisks) load as the
   document shows them - each version reflects its own state, so
   point-in-time semantics stay correct automatically.

Style-name pinning: the real style names come from a spike - the first
plan task downloads one real version, dumps the distinct style names,
and pins them as constants. The design fixes the mechanism
(style-first, regex fallback, pinned constants); the spike fills the
constant values.

## CLI, monitor, launchd

- `app.ingest`: `jurisdiction` choices become `nsw|vic`; `nsw` keeps
  the existing Chrome path untouched; `vic` dispatches to
  `fetcher_vic` + `parser_vic`; cache under `data/raw/vic/`;
  `--limit-versions` works for both.
- `app.monitor`: gains `vic` - the corpus check compares the site's
  version list against `ingested_versions` over httpx and ingests
  anything new, printing the existing `corpus: <slug> ...` lines. The
  audit re-check half is idempotent, so running the monitor once per
  jurisdiction cannot double-notify.
- `deploy/launchd/monitor-remote.sh`: run `app.monitor nsw` then
  `app.monitor vic` inside the same tunnel session. The plist points at
  the script, so no re-bootstrap is needed; a `kickstart` validates the
  change.

## Rollout

All operator steps, no user-performed items:

1. Local spike (`--limit-versions 1`) pins the style constants; local
   full ingest; the real-file integrity assertions pass (version 113
   contains s 27B "Prohibited terms"; total sections > 400).
2. Production: ssh tunnel + `app.ingest vic` full history (hits the
   local cache; minutes).
3. Acceptance pair against production:
   `GET /v1/legislation/sections?act=residential-tenancies-act-1997&section_no=27B&as_at=2026-08-03`
   returns the section; the same query `as_at=2020-06-01` returns 404
   "Section not in force at that date" (s 27B commenced 2021-03-29).
4. `launchctl kickstart` the monitor once: both jurisdictions report
   `no new versions`.

## Testing

No LLM stage; eval-first is satisfied by exact-assert pytest.

- fetcher_vic: respx-mocked landing page, version page, and DOCX
  download; cache hit and miss.
- parser_vic: synthetic DOCX fixtures generated with python-docx in the
  tests, covering part/division tracking, ToC skipping, endnotes stop,
  schedule labelling, and repealed placeholders.
- registry: per-jurisdiction instrument lookup; `ensure_act` with
  explicit landing_url.
- Real-file integrity: not in CI (CI has no cache); asserted as a
  rollout step.

## Out of scope

- VIC deterministic rules and opening the API's jurisdiction literal
  (sub-project 2)
- VIC clause audit rules, prompts, and golden sets (sub-project 3)
- SaaS state-to-jurisdiction wiring (sub-project 4)
- Any change to the NSW fetcher or parser
