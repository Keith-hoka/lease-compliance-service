# VIC deterministic rules design

Sub-project (b) of the VIC milestone (a corpus - done; b this; c clause
audit; d SaaS wiring). Adds a VIC rule module against the Residential
Tenancies Act 1997 (Vic), opens the audits API to `jurisdiction: VIC`,
and ships exact-assert evals. Every rule below is grounded in the
production corpus text (as at 2026-08-04); rules pin their thresholds in
code with citations - the corpus-reading alternative was rejected
(fragile parsing; the daily monitor already tells us when a prescribed
amount changes, and changing a pinned constant is the intended
response).

## Rules (`app/rules/vic.py`)

Constants: `ACT = "residential-tenancies-act-1997"`,
`REGS = "residential-tenancies-regulations-2021"`,
`RENT_THRESHOLD_WEEKLY = Decimal(900)` (reg 17: "For the purposes of
section 31(3) of the Act, the prescribed amount is $900", whose note
extends the same amount to s 40). Monthly rent is derived from the
lease's own frequency with a single rounding (monthly = the stated
amount; fortnightly x 26 / 12; weekly x 52 / 12), rather than
round-tripping through a cent-rounded weekly figure.

| rule_id | citations | logic | required_inputs |
|---|---|---|---|
| `vic.bond_max_1_month` | s 31 + reg 17 | s 31(1)(a): bond must not exceed one month's rent; s 31(3): the cap "does not apply ... if the weekly amount of rent payable under the agreement exceeds the prescribed amount". Weekly rent <= 900: bond > monthly rent -> red, else green. Weekly rent > 900: skipped ("the statutory cap does not apply at this rent"). | bond_amount |
| `vic.advance_max_1_month` | s 40 + reg 17 | s 40(1): "must not solicit or otherwise invite a renter to pay rent ... more than 1 month in advance"; s 40(2) disapplies s 40(1) only, above the prescribed weekly amount. Same threshold shape as the bond rule. s 40(3) (in force 2025-11-25) separately prohibits accepting unsolicited advance payment and is not disapplied by s 40(2); not modelled, since structured input cannot distinguish solicited from unsolicited payment. | rent_in_advance_amount |
| `vic.rent_increase_frequency` | s 44 | s 44(4A): "must not increase the rent ... at intervals of less than 12 months". Pairwise over `rent_increases` (the NSW frequency pattern); any gap < 365 days -> red. `applies_from` is pinned to the corpus's ingestion floor (2020-04-06): the corpus shows the 12-month interval present at every version back to that floor, and its own amendment note records (4A) as inserted in 2002 by No. 45/2002, predating the corpus's coverage - so the floor is a data-availability boundary, not a legislative commencement date. The pre-reform 6-month interval is not modelled - audits with as_at before the floor skip. | rent_increases |
| `vic.fixed_term_increase_provision` | s 44 | s 44(4): within a fixed term the rent may only be increased if the agreement provides for it (amount or method). An increase dated inside [start_date, end_date] with `fixed_term_increase_in_agreement` not true -> red; true -> green; no in-term increases -> green. Requires `end_date` (no fixed term without one -> rule skipped by required_inputs). | rent_increases, fixed_term_increase_in_agreement, end_date |

Not modelled, and why: s 50 holding deposits (a refund duty, not an
amount cap - not decidable from `holding_deposit_amount`); s 27 invalid
terms (clause-audit territory, sub-project c); the 90-day increase
notice (no notice-date field in `LeaseInput`).

Each rule follows the NSW conventions in shape: a docstring quotes the
corpus text with its as-at date; a `CheckResult` verdict is red, green,
or skipped, paired with an evidence dict of fields and computed values
(a skip the check function itself produces carries its own skip_reason
and the full evidence dict, not just the engine's generic required-input
or applies_from skip text); `required_inputs` drives the engine's skip
behaviour for absent fields; messages state the computed cap. Four
points deliberately diverge from NSW: monthly rent is derived once per
the lease's own frequency rather than round-tripping through a
cent-rounded weekly figure; the bond and advance checks produce their
own above-threshold "skipped" verdict, rather than leaving all skips to
the engine; the fixed-term window's end bound is inclusive, so an
increase effective on `end_date` counts as in-term, where NSW's
disclosure check is exclusive; and `fixed_term_increase_in_agreement` is
itself a required input, so the rule skips when it is unknown, rather
than NSW's disclosure check, which treats an absent value as not
provided for.

## Engine and API

- The engine already dispatches on `rule.jurisdiction`; `ALL_RULES` in
  `app/rules/__init__.py` gains the VIC list.
- `AuditCreate.jurisdiction` becomes `Literal["NSW", "VIC"]`.
  `ClauseAuditCreate.jurisdiction` stays `Literal["NSW"]` until
  sub-project (c) ships VIC clause rules - accepting a VIC clause audit
  today would produce zero findings.
- `ENGINE_VERSION` bumps to `1.3.0`.

## Testing

`tests/test_rules_vic.py`, exact-assert (the eval for a deterministic
capability), covering per rule: red, green, boundary-equal (bond exactly
one month's rent is green; weekly rent exactly 900 keeps the cap;
interval of exactly 365 days is green), the skipped paths (rent above
threshold; missing required inputs), and `applies_from` gating for the
frequency rule. Plus one engine-level test: a VIC audit request returns
only VIC rules' findings and an NSW request is unchanged.

## Rollout

Deploy (migrationless), then one production curl: a VIC audit with an
oversized bond returns `vic.bond_max_1_month` red citing s 31, and the
existing NSW acceptance body still returns its s 159 red. Record in the
ledger.

## Out of scope

- VIC clause audit rules, prompts, golden sets (sub-project c)
- SaaS state-to-jurisdiction mapping (sub-project d)
- Any NSW rule change
- Historical pre-reform intervals (6-month era) for s 44(4A)
