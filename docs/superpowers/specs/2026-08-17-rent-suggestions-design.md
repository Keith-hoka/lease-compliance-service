# Renewal Rent Suggestions Design

Date: 2026-08-17. Status: approved for planning.
Sub-project (b) of the rent-AI milestone. Depends on (a) AI consent
foundation (SaaS, done) and (b0) rent statistics ingest (service, done).

## Context and goal

When a landlord renews a lease, the SaaS should suggest a weekly rent
with a range, a short reasoning, and a law card — computed from the
lease's own renewal chain, the property's attributes, official
bond-derived market statistics, and the jurisdiction's rent-increase
rules. Deterministic before LLM: the market anchoring and the legal
check are exact computations; the model only picks a figure inside a
pre-computed range and writes the reasoning, and it can only cite
numbers it was given.

## Owner decisions

- 2026-08-16: lives in the compliance service as `POST
  /v1/rent-suggestions`, reusing the provider-failover judge; output =
  suggested figure + range + reasoning + law card; market evidence from
  the official statistics (b0), never model memory.
- 2026-08-17: **law card = hypothetical audit** — the suggestion is
  appended to the lease's `rent_increases` as a rent increase effective
  on the renewal start date and the existing rules run unchanged, only
  the rent-increase rules' findings are surfaced; **range is
  deterministic** (market band ∩ increase-cap band) and the **LLM only
  chooses within it**; **eval = structural property assertions +
  reasoning citation check** (every money figure in the reasoning must
  appear in the supplied evidence); **SaaS UI = one-click suggestion on
  the renew page** that can copy the figure into the rent field.

## API contract

`POST /v1/rent-suggestions` (tenant key, router-level rpm, usage class
`rent_suggestions`), request:

```json
{
  "jurisdiction": "NSW", "as_at": "2026-08-17",
  "property": {"area_key": "2000", "dwelling_type": "unit", "bedrooms": 2},
  "lease": { ...LeaseInput..., "rent_increases": [ ...history... ] },
  "renewal_start": "2026-10-01"
}
```

`property.area_key` is jurisdiction-native (NSW postcode, VIC suburb
label as published); `dwelling_type` in `house|unit|townhouse|other`;
`bedrooms` nullable. `lease` is the existing `LeaseInput` (the SaaS
already synthesises it from the renewal chain for audits;
`rent_increases` carries the chain's historical increases). It carries no
tenant identity fields by construction — the disclosure contract from
(a) holds without extra filtering.

Response:

```json
{
  "current_weekly": "600.00", "suggested_weekly": "630.00",
  "range": {"low": "600.00", "high": "690.00"},
  "market_gap": "within",
  "market": {"period": "2026-07", "median": "760.00", "p25": "697.50", "p75": "886.25",
             "sample_size": 170, "fallback": null,
             "source": {"name": "...", "url": "...", "licence": "..."}},
  "law_card": [{"rule_id": "nsw.rent_increase_frequency", "verdict": "green",
                "summary": "...", "citations": [...]}],
  "law_blocked": false,
  "reasoning": "...", "model": "claude-sonnet-5", "engine_version": "1.6.0",
  "disclaimer": "General information, not legal advice."
}
```

`market_gap` ∈ `within` (bands intersect), `above_cap` (market band
entirely above the cap band), `below_current` (market band entirely
below current rent), `no_data` (no statistics row found). `market` is
null when `no_data`. `market.fallback` names the rollup used when the
exact cell was thin: `null | "bedrooms_all" | "dwelling_all"`.

## Deterministic core (`app/rent_suggest/anchor.py`, `law.py`)

Money is `Decimal`; results rounded to whole dollars.

- `current_weekly` = `to_weekly_rent(lease.rent_amount, lease.rent_frequency)`
  (existing helper).
- **Market band**: newest `rent_statistics` row for (jurisdiction,
  area_key, dwelling_type, bedrooms). NSW: if `sample_size < 10`, fall
  back to bedrooms=NULL for the same dwelling type, then to
  `dwelling_type='all'`; band = [p25, p75]. VIC: newest row (VIC has no
  per-type bedrooms-NULL rollup — the only fallback is `('all', NULL)`);
  band = [median × 0.92, median × 1.08]. Missing entirely → `no_data`.
- **Cap band**: [current, current × 1.15].
- **Range** = intersection. Market entirely above cap → range = cap
  band, `above_cap`. Market entirely below current → range = [current,
  current], `below_current`. `no_data` → range = cap band.
- **Law card**: `hypothetical = lease.model_copy(update={"rent_increases":
  history + [RentIncrease(effective_on=renewal_start,
  new_amount=<range midpoint in the lease's own frequency>)]})`, run the
  existing `run_audit(session, jurisdiction, as_at, hypothetical)`, keep
  findings whose `rule_id` contains `rent_increase` or
  `fixed_term_increase`. Any red → `law_blocked=True` and the range
  collapses to [current, current] (the suggestion is "hold"; the red
  finding explains why — e.g. NSW 12-month frequency not yet elapsed).
  Findings keep their existing citations; `engine_version` from
  `app.rules`.

## LLM layer (`app/rent_suggest/judge.py`, prompts in `app/llm/prompts.py`)

- Skipped when the range is degenerate (`low == high`, i.e. `law_blocked`
  or `below_current`): `suggested_weekly = current`, reasoning from a
  template that names the cause. No model call, no cost.
- Otherwise one call through `make_judge()` (failover, usage log,
  `job.model`-style model recording in the response). Output model built
  per request: `suggested_weekly: Decimal = Field(ge=low, le=high)`,
  `reasoning: str` — the schema makes an out-of-range figure impossible.
- Prompt evidence block: the range and how it was derived (market band,
  cap band, gap), the last 4 market periods for the cell used (period,
  median, p25/p75 when present, sample size, fallback note), the
  renewal-chain history (each past rent and increase percentage; no
  dates beyond year-month), property attributes, the law card summaries.
  Instructions: choose one figure inside the range; write 2–3 sentences;
  cite only numbers present in the evidence; when `above_cap`, choose
  the upper part of the range and note that the market sits above the
  cap so a staged approach may follow; when the newest market period is
  older than 6 months, say so; never mention tenants by name (there are
  none to mention).
- Failure semantics: `JudgeError`/`ProviderDown` (after the failover
  wrapper has done its work) become HTTP 502 `{"detail": {"code":
  "judge_unavailable"}}` — no partial response, one shape. This is the
  service's first synchronous LLM endpoint; the SaaS proxy passes the
  status through and the renew page shows "Suggestion unavailable, try
  again" without blocking the manual rent entry.

## Eval

- Deterministic core: exact-assert pytest cases (~15) over seeded
  `rent_statistics` rows: NSW within/above_cap/below_current/no_data,
  thin-sample fallback chain, VIC ±8% band, law_blocked collapse (NSW
  frequency red), rounding, frequency conversion.
- LLM layer (`-m llm_eval`, ~20 golden scenarios generated from the same
  seeds): for each, assert `suggested_weekly` within range (defence in
  depth beyond the schema); every money figure in `reasoning` (regex
  `\$?\d[\d,]*(\.\d+)?`, normalised) is a member of the evidence set
  supplied to the prompt; direction property per scenario (`above_cap` →
  suggestion in the upper half; `within` → any). Gate: fraction of
  scenarios passing all properties ≥ 0.9; no threshold below that.
  Recorded in `docs/model-evals.md`; the (b0) primary/backup models are
  both run through it once (backup must pass too, since failover serves
  it).

## SaaS consumer

- Backend proxy `POST /api/v1/leases/{lease_id}/rent-suggestion`
  (manager roles, `require_ai_consent(AiFeature.rent_ai)`): builds the
  request from `chain_to_audit_payload` + property attributes + the
  renewal start date from the request body, calls the service, returns
  the response verbatim; records nothing durable (suggestions are not
  stored — YAGNI until a use case appears).
- Renew page: "Suggest rent" beside the rent field; unconsented → the
  same prompt card pattern as clause audits, linking to AI settings.
  Result card shows figure, range, reasoning, law card rows (verdict
  colour + citation label), market line with period/sample and the
  source name; VIC results render the CC BY 4.0 attribution line. "Use
  suggestion" converts weekly → the form's frequency and fills the field.
- Playwright e2e with the service mocked (unconsented card; consented
  flow fills the field), plus one LIVE-gated run.

## Rollout

Service: deploy after eval gate; no schema change. SaaS: no production
environment yet — dev DB only. Both `.env` values already exist. Docs:
`deploy/README.md` endpoint note; `docs/model-evals.md` eval record.

## Out of scope (YAGNI)

- Storing suggestions or tracking acceptance.
- Multi-scenario (conservative/aggressive) outputs.
- Configurable cap (15% is a constant with a docstring).
- Market estimation for properties without a lease — that is (c).
- Any change to the rent-increase rules themselves.
