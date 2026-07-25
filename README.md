# lease-compliance-service

Deterministic NSW residential lease compliance audits with a temporal
legislation store. Output is general information, not legal advice.

## Usage

Configure comma-separated client keys via `API_KEYS` (env or `.env`), then:

```bash
API_KEYS=dev-key uv run uvicorn app.main:app
```

Create an audit (findings carry statutory citations and the as-at date):

```bash
curl -s -X POST http://localhost:8000/v1/audits \
  -H "X-API-Key: dev-key" -H "Content-Type: application/json" \
  -d '{
    "jurisdiction": "NSW",
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

## Legislation source and licensing

Legislation text is sourced from the NSW legislation website
(https://legislation.nsw.gov.au, Parliamentary Counsel's Office). Stored
sections carry source URLs and version dates for attribution. Per the site's
copyright page: "Unless otherwise noted, all copyright material available on
or through this website is licensed under a Creative Commons Attribution 4.0
International licence (CC BY 4.0)", attributing the State of New South Wales
as the source.
