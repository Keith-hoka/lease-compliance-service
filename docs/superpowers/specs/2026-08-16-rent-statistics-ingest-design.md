# Rent Statistics Ingest Design

Date: 2026-08-16. Status: approved for planning.
Sub-project (b0) of the rent-AI milestone: official rent-market data behind
a query endpoint. (b) renewal rent-increase suggestions and (c) market rent
estimation both consume it and follow in their own spec cycles.

## Context and goal

Renewal suggestions and market estimates need a market reference that is
not the model's memory. Both jurisdictions publish bond-lodgement-derived
rent data — actual agreed rents, not advertised asking rents:

- **NSW** (Fair Trading, nsw.gov.au rental-bond-data): per-lodgement rows
  (postcode, dwelling type, bedrooms, weekly rent), monthly XLSX files
  since 2024 under a fixed URL pattern
  (`/sites/default/files/noindex/YYYY-MM/rentalbond_lodgements_<month>_YYYY.xlsx`)
  plus annual files 2021-2025 (`rentalbond_lodgements_year_YYYY.xlsx`).
  No licence text on the page (open government data; confirm terms
  before external tenants rely on it — a rollout checklist item, not a
  build blocker).
- **VIC** (Homes Victoria Rental Report, CC BY 4.0): quarterly XLSX of
  moving-annual median weekly rents by suburb, by property type, with
  bond counts; latest quarter Sep 2025 at brainstorm time. Inspected: one
  workbook carries the whole series (columns Mar 2000 .. latest quarter,
  Count/Median pairs; seven sheets = 1/2/3-bedroom flat, 2/3/4-bedroom
  house, All properties; rows = region + suburb, `-` for suppressed
  cells, a `Group Total` row per region). So VIC needs no multi-file
  backfill: loading the current workbook loads all history.

This sub-project ingests both into the compliance service and exposes a
tenant-facing query endpoint. It follows the legislation-monitor pattern:
fetch official sources on a schedule, parse with fail-loud guards, load
idempotently, serve point-in-time queries.

## Owner decisions (2026-08-16)

- **Lives in the compliance service** (new `app/rent_stats/` package),
  reusing its scheduler, tenant auth, usage counters, and production
  corpus-sync path.
- **NSW keeps per-lodgement detail; both jurisdictions get an
  aggregated statistics table** that the endpoint serves.
- **Public tenant endpoint** (`GET /v1/rent-statistics`), not an
  internal-only function — the SaaS and future external tenants share
  one door.
- **Full available history from 2021** (NSW annual 2021-2025 + monthly
  2026 onward; VIC every published quarter).

## Data model

`rent_bond_lodgements` (NSW detail):
`id, jurisdiction, period` (YYYY-MM), `postcode, dwelling_type, bedrooms,
weekly_rent (numeric), source_file, content_hash`; index on
(postcode, period). Rows are inserted per source file; re-ingesting a
file with the same `content_hash` is a no-op, a changed hash replaces
that file's rows in one transaction.

`rent_statistics` (both jurisdictions, query table):
`jurisdiction, period` (NSW `YYYY-MM`, VIC `YYYY-Qn`), `area_code`
(NSW postcode, VIC suburb name as published), `dwelling_type`
(normalised: `house`, `unit`, `townhouse`, `other`, `all`), `bedrooms`
(int, or null = all), `median, p25, p75` (numeric, nullable — VIC
publishes medians only), `sample_size`, `source_url, fetched_at`.
Unique on (jurisdiction, period, area_code, dwelling_type, bedrooms).

Dwelling-type normalisation is a per-source mapping table in the parser.
NSW codes (from the workbook's Definitions sheet): F -> unit, H ->
house, T -> townhouse, O -> other, U -> other; any other value (the 2025
annual file carries stray codes such as `1`) also maps to `other` and is
counted in the ingest summary — the source is agent-entered free data,
so unknown codes are data quality, not a format change. NSW rows whose
bedrooms or weekly rent are `U`/non-numeric are skipped and counted.
VIC sheet titles map to (dwelling_type, bedrooms): "N bedroom flat" ->
(unit, N), "N bedroom house" -> (house, N), "All properties" -> (all,
null). A missing expected sheet or header cell fails loud (format
change).

## Ingest

- `app/rent_stats/fetcher.py`: NSW — enumerate the annual files and the
  monthly URL pattern from 2026-01 to the current month, download those
  not already loaded (by content_hash); VIC — download only the suburb
  workbook for the newest completed quarter, discovered by probing
  candidate quarters backwards until one is published. The LGA workbook
  is not ingested: `area_code` carries no area-type dimension, so LGA
  and suburb names could collide on the `rent_statistics` unique key.
  httpx, same client shape as `fetcher_vic`.
- `app/rent_stats/parser.py`: openpyxl (new dependency, `uv add
  openpyxl`); one sheet-to-record mapper per source. Header rows are
  pinned per source; a header mismatch raises before any row is loaded
  (the legislation completeness-guard principle).
- `app/rent_stats/loader.py`: NSW rows into `rent_bond_lodgements`, then
  SQL aggregation (`percentile_cont` 0.25/0.5/0.75, count) grouped by
  (period, postcode, dwelling_type, bedrooms) plus `bedrooms = null` and
  `dwelling_type = 'all'` rollups into `rent_statistics`; VIC published
  medians straight into `rent_statistics`. Upsert on the unique key.
- CLI `uv run python -m app.rent_stats backfill` (all history) and
  `update` (new files only); `update` is added to the daily launchd
  monitor script alongside the legislation monitor. Both idempotent.

## API

`GET /v1/rent-statistics?jurisdiction=NSW|VIC&area=<code>&dwelling_type=<t>&bedrooms=<n>&periods=<N>`
(bedrooms optional -> all; periods default 8, max 40) ->

```json
{
  "jurisdiction": "NSW", "area": "2000", "dwelling_type": "unit", "bedrooms": 2,
  "series": [{"period": "2026-07", "median": 850, "p25": 750, "p75": 950, "sample_size": 312}],
  "source": {"name": "NSW Fair Trading rental bond lodgements", "url": "...", "licence": "...", "fetched_at": "..."}
}
```

Newest first. Unknown area returns 200 with an empty series (consumers
degrade gracefully). Auth: existing `X-API-Key` tenant dependency + rpm
bucket; usage recorded under a new endpoint class `rent_statistics`
(counted, not quota-limited in this sub-project — the daily quota
mechanism stays clause-audit-only until a pricing decision).

## Testing

- Parser tests on trimmed real-file fixtures for each source (checked
  into `tests/fixtures/rent_stats/`), asserting field mapping,
  dwelling-type normalisation, and the header guard tripping on a
  mutated fixture.
- Aggregation test: known lodgement rows -> exact median/p25/p75/count.
- Loader idempotency: same file twice -> zero new rows; changed hash ->
  replaced.
- API tests: auth, rpm, unknown area empty series, ordering, periods cap.
- Rollout: controller runs `backfill` against production, records row
  counts per jurisdiction in the ledger, and spot-checks one NSW postcode
  and one VIC suburb against the published workbook.

## Out of scope (YAGNI)

- Any LLM use — this sub-project is deterministic data plumbing.
- Suburb-to-postcode crosswalk (NSW is postcode-keyed, VIC suburb-keyed;
  consumers pass the key native to the jurisdiction).
- Advertised-rent sources, other states, refund/holdings datasets.
- Rate-limit tiers or quota for the new endpoint class.
