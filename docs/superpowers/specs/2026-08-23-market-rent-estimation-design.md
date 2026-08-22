# Market Rent Estimation Design

Date: 2026-08-23. Status: approved for planning.
Sub-project (c) of the rent-AI milestone. Depends on (b0) rent
statistics ingest and on (b) renewal rent suggestions, whose market
anchoring it reuses unchanged.

## Context and goal

A landlord wants to know what a property would rent for today,
independent of any lease: when setting the rent for a new or vacant
property, and as a standing "is my rent at market?" reading on every
property they manage. The official bond-derived statistics (b0) answer
that directly; (b) already turned them into a deterministic market band
for renewals. This sub-project exposes the same reading as a market-rent
estimate and shows it on the SaaS property page.

## Owner decisions (2026-08-23)

- **Use case**: a market-rent card on every property page (estimate,
  band, trend), with the gap to the current rent when a lease exists.
- **No LLM**: the estimate is the official median, the band the official
  percentiles (VIC: +-8%), the trend a difference of two published
  medians - all computable from structured data, so nothing goes
  through a model. Zero model cost, no eval beyond exact-assert tests.
- **No consent gate**: nothing is sent to a third-party model; the card
  queries the compliance service's statistics the way audits do. The AI
  disclosure page's Rent AI copy keeps describing renewal suggestions
  only.
- **Architecture A**: a thin new service endpoint `GET /v1/market-rent`
  reusing (b)'s `resolve_area`, `market_cell`, `band_for`, `is_stale`
  and `period_end`, so the renew page and the property page can never
  disagree; the SaaS adds a proxy and a card.

## Service API

`GET /v1/market-rent?jurisdiction=NSW|VIC&area=<key>&dwelling_type=<t>&bedrooms=<n>[&as_at=YYYY-MM-DD]`
(tenant key, router-level rpm, usage class `market_rent` - counted, no
daily quota, like `rent_statistics`). `area` is jurisdiction-native
(NSW postcode, VIC suburb; VIC keys resolve against the published
grouped labels exactly as in (b)). `as_at` defaults to the Sydney date.

```json
{
  "jurisdiction": "VIC", "area": "albert park",
  "area_label": "Albert Park-Middle Park-West St Kilda",
  "dwelling_type": "unit", "bedrooms": 2,
  "estimate_weekly": "643", "band": {"low": "592", "high": "694"},
  "basis": "median",
  "period": "2025-Q3", "period_end": "2025-09-30", "stale": true,
  "sample_size": 144, "fallback": null,
  "series": [{"period": "2025-Q3", "median": "643.00", "p25": null, "p75": null, "sample_size": 144}],
  "trend": {"from_period": "2024-Q3", "from_median": "650.00", "change_pct": "-1.1"},
  "source": {"name": "...", "url": "...", "licence": "CC BY 4.0"},
  "disclaimer": "General information, not legal advice."
}
```

- `estimate_weekly` is the median of the statistics cell actually used,
  rounded to whole dollars; `band` is NSW [p25, p75] / VIC [median x
  0.92, median x 1.08], whole dollars - the same `market_cell` +
  `band_for` code path as (b)'s market band.
- `fallback` as in (b): `null | "bedrooms_all" | "dwelling_all"`.
  `area_label`, `period_end` and `stale` (period ended more than six
  calendar months before `as_at`) as defined by the (b) follow-ups.
- `series` is the newest 8 periods of the cell used (NSW months, VIC
  quarters), newest first. `trend` compares the newest period with the
  same period one year earlier (`2026-07` -> `2025-07`, `2025-Q3` ->
  `2024-Q3`), matched exactly in the rows fetched for the cell - the
  estimate fetches 13 periods so that comparison period is available
  for monthly and quarterly series alike, and returns the newest 8;
  when the comparison period is absent `trend` is null.
  `change_pct = (latest - from) / from x 100`, one decimal.
- `basis` is the constant `"median"`, an explicit marker for any future
  estimator; no other estimator exists.
- No data (unresolvable area, or no rows at any fallback level) returns
  **200** with `area_label`, `estimate_weekly`, `band`, `period`,
  `period_end`, `sample_size`, `fallback`, `trend` all null,
  `stale` false, `series` empty, while `jurisdiction`, `area`,
  `dwelling_type`, `bedrooms` echo the request and `basis`, `source`,
  `disclaimer` keep their constant values. Never 404: consumers degrade.

## Deterministic core (`app/market_rent/`)

A sibling package of `app/rent_suggest/`, not an extension of it.

- `estimate.py`: `async def estimate(session, jurisdiction, area_key,
  dwelling_type, bedrooms, as_at) -> MarketEstimate | None`:
  `resolve_area()` (None -> None), `market_cell(..., periods=8)`,
  `band_for()`, `dollars()`, `period_end()`, `is_stale()`, `trend()`.
  `market_cell` gains a `periods` parameter defaulting to the current
  4, so (b) is unchanged.
- `trend(series, jurisdiction) -> Trend | None`: pure; derives the
  comparison period from the newest period string and looks it up in
  the series; `from_median` comes from the statistics table and is
  always positive, so no zero guard.
- `app/schemas/market_rent.py` and `app/routers/market_rent.py` are thin
  layers over the above; `record_usage(session, tenant_id,
  "market_rent")` once per request.

No new statistics queries: every read goes through the (b)-reviewed
`market_cell`.

## SaaS consumer

Backend proxy `GET /api/v1/properties/{property_id}/market-rent`
(manager roles; `get_owned_property`; `property_jurisdiction`
three-state - unresolved state -> 422 like clause audits):

- Query built from the property: `area` = postcode (NSW) / `city`
  (VIC); `dwelling_type` via the shared `dwelling_type_for`, which is
  corrected here so `apartment` and `condo` map to `unit` (today they
  fall to `other`, the sparsest NSW cell and absent in VIC; (b)'s
  renewal payload benefits from the same fix); `bedrooms` as stored
  (0 is the studio cell; the card names the cell used).
- Calls a new `compliance.get_market_rent(params)` shaped like
  `create_rent_suggestion` (10 s timeout is enough; no model call);
  503 when compliance is disabled, 502 on service errors.
- Returns a wrapper, not the service body verbatim:
  `{"market": <service response>, "current_weekly": "600" | null,
  "gap_pct": "-6.7" | null}`. `current_weekly` is the rent of the
  property's current lease - the lease the SaaS treats as active for the
  property today (its existing active-lease lookup; none -> null) -
  converted to weekly (weekly as is, fortnightly / 2, monthly x 12 / 52,
  whole dollars) by a small backend helper;
  `gap_pct = (current_weekly - estimate_weekly) / estimate_weekly x
  100`, one decimal; null when there is no active lease or no estimate.
  Nothing is stored.

Frontend: `lib/marketRent.ts` (`getMarketRent(propertyId)`) and a
"Market rent" card on `/app/properties/[id]`, loaded on page open (a
data read, no button):

- estimate per week, band, the cell used (e.g. `unit, 2 bedrooms -
  Albert Park-Middle Park-West St Kilda`, with the fallback level when
  one applied);
- period and sample size; the stale warning line; the trend line
  ("-1.1% vs 2024-Q3");
- with an active lease: "Current rent $600/week, 6.7% below the market
  median" (above/below wording);
- source name, the CC BY 4.0 attribution for VIC, and the
  general-information disclaimer;
- three degraded states that never block the rest of the page: no data
  ("No market data for this area"), incomplete property data (422 -
  prompt to complete state/postcode/suburb, linking the edit page), and
  service failure ("Market data unavailable").

Playwright e2e with the proxy mocked: a property with a lease shows the
estimate and the gap line; a no-data variant; one LIVE-gated run
against the real service.

## Error handling

Service: parameter validation is pydantic's (422), auth 401, rpm 429;
missing data is the 200 empty shape, never an exception; a statistics
query failure surfaces as 500 unwrapped. SaaS proxy: 422 / 503 / 502 as
above; the card maps 422 to the complete-your-property prompt and
everything else to "Market data unavailable".

## Testing

- Service: `trend()` table (month and quarter shapes, absent comparison
  period -> null, negative change); `estimate()` over seeded rows (NSW
  exact cell, `bedrooms_all`, `dwelling_all`, VIC, unresolvable area,
  stale); API (401, unknown area -> 200 empty, 8-period series, usage
  recorded once); the `periods` parameterisation leaves every existing
  (b) test untouched.
- SaaS backend: `dwelling_type_for` new mapping with the (b) tests
  updated; the weekly-rent helper; proxy 401/403/422/200/502;
  `current_weekly`/`gap_pct` with and without an active lease.
- Frontend: lint, tsc, the three e2e cases.
- No LLM -> no `-m llm_eval`; CI is the gate.

## Rollout

Service: deploy, then smoke `/v1/market-rent` for NSW 2000/unit/2 and
VIC albert park/unit/2; `deploy/README.md` gains a "Market rent"
section (usage class, shared market-band code with (b), VIC attribution
requirement). SaaS: local dev only (no production environment); the AI
disclosure copy is unchanged. Ledger and memory as usual.

## Out of scope (YAGNI)

Storing estimates or tracking them over time; multi-cell or weighted
estimators; the estimate inside the new-property or new-lease forms; a
portfolio-wide table; tenant-role visibility.
