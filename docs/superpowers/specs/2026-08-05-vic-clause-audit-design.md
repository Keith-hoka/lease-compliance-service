# VIC clause audit design

Sub-project (c) of the VIC milestone (a corpus - done; b deterministic
rules - done; c this; d SaaS wiring). Extends the LLM clause-audit stage
to VIC: sixteen prohibited-terms rules against RTA 1997 (Vic) s 27B and
Regulations 2021 reg 11, jurisdiction dispatch in the processor,
`ClauseAuditCreate` opened to VIC, and seeded golden sets whose per-rule
precision/recall eval gates the rollout. VIC has no NSW-style
"term of every agreement" mechanism - its mandatory side is the
prescribed standard form (s 26), which is milestone 4's form-comparison
work - so VIC ships the prohibited family only, plus the
jurisdiction-agnostic fields family.

## Rules (`app/clause_audit/rules_vic.py`)

`ClauseRule` gains `jurisdiction: Literal["NSW", "VIC"]`; the fifteen
NSW rules in `rules.py` are stamped `"NSW"` mechanically. VIC rules live
in the new module (rules.py stays NSW-only at 277 lines), with the
module docstring quoting the corpus text as at the implementation date.

`VIC_COMMENCED = date(2021, 3, 29)` - corpus-verified: s 27B is absent
at 2021-03-28 and present at 2021-03-29. Every rule sets
`applies_from = VIC_COMMENCED`; citation resolution is the second line
of defence (both instruments are absent from the corpus before then).

| rule_id (vic.clause.*) | source | effect judged |
|---|---|---|
| `renter_insurance` | s 27B(1)(a) | renter must take out any form of insurance |
| `provider_liability_exemption` | s 27B(1)(b) | exempts the provider from liability for acts of the provider, agent, or persons acting for them |
| `breach_penalty` | s 27B(1)(c) | on contravention the renter pays remaining rent, increased rent, a penalty or liquidated damages |
| `professional_cleaning_required` | s 27B(1)(d) | premises must be professionally cleaned at end of tenancy, unless the term is the standard form's s 27C conditional shape |
| `professional_cleaning_cost` | s 27B(1)(e) | renter pays professional-cleaning costs at end of tenancy, same s 27C carve-out |
| `no_breach_rent_inducement` | s 27B(1)(f) | rent reduced or rebate/benefit paid if the renter does not contravene |
| `preparation_costs` | s 27B(2) | a party bears the other party's costs of preparing the agreement |
| `unreviewed_contract` | reg 11(a) | binds the renter to a contract not agreed in writing after opportunity to review |
| `renter_indemnity` | reg 11(b) | renter must indemnify the provider |
| `late_availability_claim_waiver` | reg 11(c) | prevents compensation claims when the premises are unavailable at commencement |
| `costly_payment_method` | reg 11(d) | rent must be paid by a method carrying additional costs, other than the renter's own bank or account fees |
| `third_party_services` | reg 11(e) | renter must use a provider-nominated third-party service, other than an embedded network |
| `safety_maintenance_transfer` | reg 11(f) | fees for, or delegation of, safety-related maintenance that is the provider's responsibility |
| `tribunal_costs_transfer` | reg 11(g) | renter liable for the provider's Tribunal filing costs |
| `insurance_excess_transfer` | reg 11(h) | renter liable by default for an excess under the provider's insurance |
| `fixed_break_fees` | reg 11(i) | fixed early-termination fees, unless the calculation basis is set out in the agreement |

Question texts state the statutory effect with its carve-outs, the NSW
animal-consent precedent: the two cleaning rules describe the s 27C
conditional shape as not-breached (exact s 27C text pinned from the
corpus during implementation); `third_party_services` excludes embedded
networks; `costly_payment_method` excludes the renter's own bank or
account fees; `fixed_break_fees` is not breached where the agreement
sets out the calculation basis. `breach_penalty`'s question notes that a
lawful break-fee provision is a separate matter (the NSW trap, VIC
edition).

Excluded, recorded in `docs/rule-candidates.md`: s 27 invalid additional
terms (requires knowing the standard form's own terms), s 28 harsh and
unconscionable terms (Tribunal discretion, not clause-readable),
regs 39/53/73 prohibited terms (rooming houses, caravan parks, site
agreements - other tenure types), and standard-form comparison
(milestone 4).

## Dispatch and API

- `run_prohibited` / `run_mandatory` take the rules list as a parameter;
  `_run_clause_family` and `run_fields` are unchanged.
- `process_job` dispatches on `job.jurisdiction`: NSW runs prohibited +
  mandatory + fields; VIC runs prohibited + fields. No empty family
  calls.
- `PROHIBITED_GUIDANCE` must be jurisdiction-neutral; if it carries
  NSW-specific vocabulary, neutralise it (rule questions carry each
  jurisdiction's own terminology). The NSW eval is the regression gate
  for any guidance change.
- `ClauseAuditCreate.jurisdiction` becomes `Literal["NSW", "VIC"]`; an
  unsupported jurisdiction still 422s.
- `ENGINE_VERSION` bumps to `1.4.0`.
- Untouched: worker, queue, quotas, document handling, quote
  verification, `ClauseAuditJob`. No migrations.

## Golden sets and evals

`tests/golden/clauses_vic.py` follows the NSW cross-scoring contract:
each case asserts its target rule's expected verdict and stands as a
hard negative for the other fifteen.

- Three reds per rule: plain wording, a cost/variant form, and a
  paraphrase.
- Hard greens for every carve-out rule, priced to punish precision:
  s 27C-shaped conditional cleaning terms, an embedded-network
  nomination, bank-account-fee-only payment wording, a break-fee clause
  that sets out its calculation basis, and a lawful break-fee case
  against `breach_penalty`.
- Roughly 54 cases (48 reds + 6 hard greens). Terminology mixes post-2021 "residential rental
  provider"/"renter" with some "landlord"/"tenant" cases - real VIC
  templates blend both and recall must hold on either.
- `THRESHOLDS` stays at the default (precision 0.9, recall 0.8);
  per-rule overrides only after eval evidence, never pre-emptively.
- `tests/test_llm_eval.py` gains a VIC prohibited test sharing
  `_score_family`; the eval fixture gates on both corpora being present.
- Deterministic tests (CI-runnable): rule-structure assertions (sixteen
  unique `vic.clause.*` ids, all jurisdiction VIC, `rule_active` flips
  across 2021-03-28/29), `resolve_rule` against the dev corpus
  (self-skipping in CI), processor dispatch shape with a stub judge
  (VIC job runs prohibited + fields only), API acceptance (VIC clause
  audit accepted, unsupported jurisdiction 422).

## Rollout

1. Eval gate before any deploy: run the full `llm_eval` suite (NSW and
   VIC). Every rule must meet its thresholds; failures iterate the
   question text and re-run (iterations recorded in the ledger). No
   threshold lowering. The NSW run doubles as the regression check for
   guidance neutralisation.
2. Deploy (migrationless), health 200.
3. Production acceptance: a seeded VIC document with a renter-insurance
   clause returns `vic.clause.renter_insurance` red citing s 27B with a
   point-in-time section id and a verified quote; an NSW control
   document still returns its known findings; clause-quota usage
   increments for the VIC job.
4. Ledger records completion with the eval precision/recall table;
   milestone memory marks (c) done; `rule-candidates.md` gains the VIC
   exclusions.

## Out of scope

- SaaS state-to-jurisdiction mapping and submit wiring (sub-project d,
  including the deferred skip_reason convention and capability-matrix
  decisions)
- Standard-form comparison (milestone 4) and s 27 invalid-terms rules
- Rooming house / caravan park / site agreement tenure types
- Any NSW clause-rule change beyond the jurisdiction stamp and neutral
  guidance wording
