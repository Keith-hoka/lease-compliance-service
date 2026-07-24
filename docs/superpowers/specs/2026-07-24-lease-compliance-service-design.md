# Lease Compliance Service — V1 Design

**Date:** 2026-07-24
**Project:** `lease-compliance-service` (standalone repo; the
rental-management SaaS becomes its first API client in a later milestone).

## Goal

An Australian residential lease compliance audit API: submit structured lease
terms, get back red/green findings with statutory citations, evaluated
against the law **as at a chosen date**. V1 is fully deterministic (no LLM)
and ships NSW only, backed by a temporal legislation store seeded with the
complete point-in-time history of the Residential Tenancies Act 2010 (NSW).

Output is general information, not legal advice; API responses and any
future UI must carry that disclaimer.

## Decisions (from brainstorming)

- **Standalone service**, public-style API design, but day 1 has a single
  API key held by the owner (no key self-service, no billing).
- **V1 is deterministic only.** Findings/schemas are shaped so the later
  LLM audit milestone extends them without breaking clients (`yellow`
  verdict reserved for LLM abstention).
- **NSW only in V1; `jurisdiction` is a first-class dimension everywhere**
  (corpus, rules, audits, evals) so VIC and the rest are data additions,
  not architecture changes.
- **Scraper is in V1 and ingests the complete history**: all point-in-time
  versions of the Act listed on legislation.nsw.gov.au (45 versions,
  17/06/2010 through 10/06/2026 as of 2026-07-24).
- **Audits are persisted** in the service DB, enabling the later
  change-monitor milestone to re-run affected audits.
- **Rules live in code** (Python registry with declarative metadata), not
  in DB rows. The DB stores legislation and audits only.
- **Act only in V1.** The Residential Tenancies Regulation 2019 is a later
  ingestion; any candidate rule whose basis lives in the Regulation is
  deferred to that milestone.
- **GitHub repo `Keith-hoka/lease-compliance-service`, public**, created in
  M1 with GitHub Actions CI.

## Stack

FastAPI + async SQLAlchemy 2.0 + Alembic + PostgreSQL (local instance on
port 5433, new database `lease_compliance`), `uv`, pytest, ruff, GitHub
Actions (pytest + ruff jobs with a Postgres service container). No frontend
and no deployment in V1; the service runs locally and in CI.

## Data model

`acts`:

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `jurisdiction` | String(3) | `NSW` (indexed) |
| `slug` | String | site identifier, e.g. `act-2010-042`; unique with jurisdiction |
| `title` | String | e.g. `Residential Tenancies Act 2010` |
| `source_url` | String | act landing page |

`sections` (temporal, SCD-2):

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `act_id` | uuid FK acts.id | indexed |
| `section_no` | String(20) | text, supports `159A` |
| `heading` | String | |
| `body_text` | Text | plain text of the section |
| `part` | String, nullable | hierarchy label |
| `division` | String, nullable | hierarchy label |
| `valid_from` | Date | version date that introduced this content |
| `valid_to` | Date, nullable | null = current; exclusive bound |
| `source_version_date` | Date | point-in-time version this row came from |
| `content_hash` | String(64) | sha256 of normalized heading+body |

Composite index `(act_id, section_no, valid_from)`. Every read is
point-in-time: `valid_from <= as_at AND (valid_to IS NULL OR as_at <
valid_to)`. No section row is ever mutated; changes close the old row and
insert a new one.

`audits`:

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `jurisdiction` | String(3) | |
| `as_at` | Date | law date the audit ran against |
| `input` | JSON | the submitted lease payload, verbatim |
| `findings` | JSON | list of finding objects (schema below) |
| `engine_version` | String | rules/code version recorded for reproducibility |
| `created_at` | DateTime(tz) | server default |

## Ingestion pipeline (M2–M3)

1. **Version discovery** — fetch the act landing page, parse the
   point-in-time version date list.
2. **Fetch** — for each version date, download the whole-act HTML view.
   Browser-like User-Agent, ~1 request per 2 seconds, and every raw HTML
   response is cached on disk (`data/raw/nsw/act-2010-042/<date>.html`);
   re-runs never re-fetch a cached version. Trimmed copies of cached pages
   become committed parser test fixtures.
3. **Parse** — HTML into Part / Division / Section hierarchy; one record
   per section with heading and plain-text body.
4. **Load (SCD-2, chronological)** — the earliest version inserts all
   sections with `valid_from = version_date`. Each subsequent version is
   hash-compared per `section_no`: changed sections close the old row
   (`valid_to = version_date`) and insert a new one; removed sections are
   closed; new sections are inserted. Loading is idempotent: version dates
   already ingested are skipped.
5. **CLI** — `uv run python -m app.ingest nsw` with `--limit-versions N`
   for development runs.

Implementation-time verification (first step of M3, before the parser is
locked): confirm the whole-act HTML URL pattern for point-in-time versions
against the live site, and record the site's licensing terms in the README
(NSW legislation is published by the Parliamentary Counsel's Office; the
scraper stores text with source URLs and version dates for attribution).

The per-section diff logic built here is deliberately the same machinery a
later change-monitor milestone reuses on newly published versions.

## Rule engine (M4)

Rules are Python: one small class per rule registered in a module-level
registry, with declarative metadata —

```python
@dataclass(frozen=True)
class Rule:
    rule_id: str  # "nsw.bond_max_4_weeks"
    jurisdiction: str  # "NSW"
    citations: list[SectionRef]  # act slug + section_no
    applies_from: date | None  # rule active window (law-driven)
    applies_to: date | None
    required_inputs: list[str]  # lease fields the check needs
    check: Callable[[LeaseInput, RuleContext], Verdict]
```

The engine, given `(jurisdiction, as_at, lease)`: selects rules active at
`as_at`, resolves each rule's cited sections from the store at `as_at`
(a rule whose cited section does not exist at that date is inapplicable),
runs `check` for rules whose `required_inputs` are present, and emits one
finding per rule:

```json
{
  "rule_id": "nsw.bond_max_4_weeks",
  "verdict": "red" | "green" | "skipped",
  "summary": "Bond of $3000 exceeds the 4-week maximum of $2400.",
  "evidence": {"fields": {"bond_amount": 3000}, "computed": {"weekly_rent": 600, "max_bond": 2400}},
  "citations": [
    {"act": "Residential Tenancies Act 2010 (NSW)", "section_no": "159",
     "as_at": "2026-07-24", "section_id": "<uuid>"}
  ],
  "skip_reason": null
}
```

`skipped` findings carry `skip_reason` (e.g. `missing input:
rent_increases`). `yellow` is reserved for the LLM milestone's abstention
and never emitted in V1.

**Lease input** (superset of the SaaS lease fields; all monetary values
decimal, dates ISO):

```json
{
  "rent_amount": 600, "rent_frequency": "weekly|fortnightly|monthly",
  "bond_amount": 2400,
  "start_date": "2026-01-01", "end_date": "2026-12-31",
  "rent_in_advance_amount": 1200,
  "rent_increases": [{"effective_on": "2026-06-01", "new_amount": 650}],
  "break_fee_amount": 2400
}
```

Only `rent_amount`, `rent_frequency`, and `start_date` are required;
everything else optional (absent inputs produce `skipped` findings for the
rules that need them).

**V1 rule set — 8 to 10 deterministic NSW rules** drawn from: bond maximum
(4 weeks rent), rent-in-advance maximum, rent increase frequency limits
(periodic agreements), rent increase notice requirements expressible from
dates, break fee scale for fixed terms, and fixed-term/date coherence rules.
The exact list and each rule's section numbers are pinned in M4 by reading
the ingested corpus (the spec deliberately does not assert section numbers
from memory); any candidate whose statutory basis turns out to live in the
Regulation is deferred, keeping V1 Act-only. Each rule records
`applies_from` when the underlying provision commenced, so temporal audits
are honest for historical `as_at` dates.

## API (M5)

Auth: `X-API-Key` header checked against `API_KEYS` (comma-separated env
var); `/health` is open. 401 on missing/unknown key.

- `GET /health` — liveness.
- `POST /v1/audits` — body `{jurisdiction: "NSW", as_at?: "YYYY-MM-DD"
  (default today), lease: {...}}` → 201 `{id, jurisdiction, as_at,
  engine_version, findings: [...], created_at}`. 422 on unknown
  jurisdiction or malformed lease.
- `GET /v1/audits/{id}` → the stored audit. 404 unknown.
- `GET /v1/legislation/sections?act=act-2010-042&section_no=159&as_at=...`
  → the section text and its validity window at that date (transparency /
  demo endpoint). 404 if the section does not exist at that date.

## Testing and evals

- **Parser**: unit tests against committed fixture HTML (trimmed real
  pages) — hierarchy, headings, edge sections (e.g. lettered numbers).
- **SCD-2 loader**: synthetic mini-act with three versions → exact
  assertions on validity windows for changed / removed / added sections;
  idempotency test (re-run is a no-op).
- **Rules**: every rule has at least green, red, and (where inputs are
  optional) skipped cases with exact asserts on verdict, evidence, and
  citations.
- **Golden set**: ~20 synthetic lease payloads with programmatically seeded
  violations; a parametrized test asserts the full findings list for each.
  This harness is the base the LLM milestone later extends with
  precision/recall metrics.
- **Temporal test**: one lease audited at two `as_at` dates straddling a
  real amendment to a provision a V1 rule cites (chosen in M4 from the
  ingested history) must produce different findings.
- **API**: auth 401, audit create/get round trip, unknown jurisdiction 422,
  section point-in-time lookup.
- Live-site fetching is never exercised in tests or CI; ingestion tests run
  entirely on fixtures.

## Out of scope (V1)

LLM audit and PDF ingestion; VIC and other jurisdictions; the Regulation
instrument; the change monitor (its diff core ships inside ingestion, but
no scheduler/alerting); SaaS integration; deployment; any frontend;
pgvector/embeddings (added when the LLM milestone needs retrieval).

## Milestones

- **M1** — repo hygiene: public GitHub repo, uv project scaffold, FastAPI
  app + `/health`, Postgres database + Alembic baseline, CI (pytest + ruff).
- **M2** — legislation store: models + migration, HTML parser
  (fixture-driven), SCD-2 loader with idempotency.
- **M3** — live scraper: version discovery, throttled cached fetcher, full
  NSW history ingest, spot-check verification of known amendment dates,
  licensing note.
- **M4** — rule engine: registry, lease input schema, findings format,
  8–10 NSW rules pinned against the corpus, golden set, temporal test.
- **M5** — API: auth, `POST/GET /v1/audits`, legislation lookup endpoint,
  persistence, engine_version stamping.

Each milestone follows the standard rhythm: task-by-task TDD, full suite,
ruff sequence, commit, push, CI green, report, wait for approval.

## Later milestones (context, not commitments)

VIC corpus + rules; Regulation ingestion; change monitor (scheduled
re-scrape → diff → re-audit affected stored audits → notify); LLM clause
audit over PDFs with citation verification and golden-set P/R; SaaS
integration (client module + audit UI); deployment.
