# lease-compliance-service

Deterministic NSW residential lease compliance audits with a temporal
legislation store. Output is general information, not legal advice.

## Usage

Configure comma-separated `key:client_id` pairs via `API_KEYS` (env or
`.env`); the `client_id` identifies the tenant every audit belongs to:

```bash
API_KEYS=dev-key:rentalapp uv run uvicorn app.main:app
```

Create an audit (findings carry statutory citations and the as-at date;
pass your own lease id as `client_ref` to enrol the lease in change
monitoring):

```bash
curl -s -X POST http://localhost:8000/v1/audits \
  -H "X-API-Key: dev-key" -H "Content-Type: application/json" \
  -d '{
    "jurisdiction": "NSW",
    "client_ref": "lease-123",
    "lease": {
      "rent_amount": "600",
      "rent_frequency": "weekly",
      "start_date": "2026-01-01",
      "bond_amount": "3000"
    }
  }'
```

Look up the section text in force at a date:

```bash
curl -s "http://localhost:8000/v1/legislation/sections?act=act-2010-042&section_no=159&as_at=2024-10-31" \
  -H "X-API-Key: dev-key"
```

Populate the legislation store (fetches every point-in-time version of the
Residential Tenancies Act 2010 with a real browser, then loads it):

```bash
uv run python -m app.ingest nsw
```

## Change monitoring

Re-run monitored leases (audits created with a `client_ref`) against the
law as at today, after refreshing the corpus with any newly published
versions (`--skip-fetch` skips the refresh):

```bash
uv run python -m app.monitor nsw
```

A change is recorded only when a verdict differs; poll them tenant-scoped,
ascending, passing the last seen `created_at` as `since`:

```bash
curl -s "http://localhost:8000/v1/audit-changes?since=2026-07-26T00:00:00Z" \
  -H "X-API-Key: dev-key"
```

To run the monitor on a daily launchd schedule, see
[deploy/launchd/README.md](deploy/launchd/README.md).

## Legislation source and licensing

Legislation text is sourced from the NSW legislation website
(https://legislation.nsw.gov.au, Parliamentary Counsel's Office). Stored
sections carry source URLs and version dates for attribution. Per the site's
copyright page: "Unless otherwise noted, all copyright material available on
or through this website is licensed under a Creative Commons Attribution 4.0
International licence (CC BY 4.0)", attributing the State of New South Wales
as the source.
