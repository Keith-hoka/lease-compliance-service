# Regulation Ingestion Design

Bring the Residential Tenancies Regulation 2019 (NSW) into the temporal
legislation store. The corpus, the daily monitor and the point-in-time
lookup API then cover both NSW instruments; rules gain a candidate survey
grounded in the actual Regulation text, with any rule computable from the
current `LeaseInput` implemented in this milestone.

## Grounded site facts (verified in a real browser on 2026-07-28)

- Landing page: `https://legislation.nsw.gov.au/view/html/inforce/current/sl-2019-0629`
  — title "Residential Tenancies Regulation 2019", the same
  `#pointInTimeBar` timeline, 20 point-in-time versions from 2019-12-16 to
  2026-07-01. `parse_version_dates` works unchanged.
- Whole-view page: same `div.frag-clause` markup with `sec.<n>` ids for the
  98 body clauses — `parse_whole_act` works unchanged. Schedules render as
  only 7 `sch.1-sec.<n>` clauses (the standard form's substance is not in
  them) and stay excluded, as for the Act.
- Re-verify both facts at execution time before locking the tasks; adjust
  and report if the site changed.

## Decisions (brainstorm outcomes)

- **Scope: corpus + evidence-driven rule survey.** Full history ingest,
  monitor coverage and lookup for the Regulation. Rules follow the V1
  break-fee philosophy: pin the text first, implement only what the
  current `LeaseInput` can compute, record the rest for later milestones.
- **Schedules stay out** — the standard-form comparison belongs to the LLM
  clause-audit milestone.
- **The `Act` model and table keep their names.** An `acts` row is a
  legislative instrument; the Regulation becomes a second row
  (jurisdiction NSW, slug `sl-2019-0629`). Renaming the table is churn
  with no behavior change.
- **The SaaS is untouched.** Citations already carry the instrument title.

## Registry

`app/ingest/registry.py` replaces the single `NSW_ACT` dict with:

- `NSW_INSTRUMENTS: list[dict]` — the Act entry (unchanged values) and the
  Regulation entry (`jurisdiction "NSW"`, `slug "sl-2019-0629"`, `title
  "Residential Tenancies Regulation 2019"`).
- `ensure_act(session, instrument: dict) -> Act` — same get-or-create,
  parameterised by the instrument dict.

Existing imports of `NSW_ACT` migrate to the list; nothing else changes
shape.

## Ingest and monitor loops

Both CLIs iterate `NSW_INSTRUMENTS` and run the existing per-instrument
pipeline (landing fetch -> timeline -> subtract ingested -> fetch missing
versions -> SCD-2 load). The parser, loader, fetcher and cache layout
(`data/raw/nsw/<slug>/`) are untouched. Output lines gain the slug so a
two-instrument run reads unambiguously. The monitor's re-run/diff stage is
instrument-agnostic already (it re-runs audits, which resolve citations by
slug) and does not change.

One-off backfill: `uv run python -m app.ingest nsw` after the change
fetches and loads the Regulation's 20 versions (Act lines all skip).

## Lookup

No code change: `GET /v1/legislation/sections?act=sl-2019-0629&section_no=…&as_at=…`
works once the corpus is loaded. The milestone's spot-check exercises it.

## Rule candidate survey

After the backfill, pin the Regulation clauses touching the lease domain —
water usage charge conditions, rent receipts, condition reports, holding
fee mechanics, break-fee-related clauses, anything the survey turns up —
and classify each candidate:

- **Computable now** (current `LeaseInput` fields suffice): implement in
  this milestone, V1 style — pinned statutory text in the docstring,
  commencement dates from the corpus windows, red/green/skipped tests on
  the real corpus, golden set extension.
- **Needs new inputs**: record in `docs/rule-candidates.md` with the
  clause citation, the missing input, and which milestone would supply it
  (SaaS form fields or LLM clause audit).

The survey's honest outcome may be zero new rules; the deliverable is the
classification, not a rule count.

## Testing

- Registry: `ensure_act` creates then reuses per instrument; two
  instruments produce two `acts` rows.
- Ingest idempotency: a full re-run prints `skipped=True` for every
  version of both instruments (manual verification step, like V1).
- Corpus spot-checks (skip-guarded like `corpus_session`): a Regulation
  clause resolves at two different as-at dates with sensible windows; the
  lookup endpoint returns it; a pre-2019-12-16 date returns 404.
- Any new rule ships with the standard exact-assert set and a golden
  extension.
- Tests and CI never fetch the live site; fixtures stay as they are.

## Out of scope

Schedule ingestion, the Residential Tenancies Regulation 2010 (the
2019 instrument's history covers 2019-12-16 onward; older point-in-time
audits keep resolving Act citations only), VIC, LeaseInput extensions,
and any SaaS change.
