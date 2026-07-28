# Regulation rule candidates

Survey of the Residential Tenancies Regulation 2019 (slug `sl-2019-0629`,
corpus as at 2026-07-28) for deterministic rule candidates. Classification
against the current `LeaseInput` fields (`rent_amount`, `rent_frequency`,
`start_date`, `end_date`, `bond_amount`, `rent_in_advance_amount`,
`holding_deposit_amount`, `other_security_amount`, `break_fee_amount`,
`rent_increases`, `fixed_term_increase_in_agreement`).

**Outcome: no clause is computable from the current lease payload.** The
Regulation regulates premises condition, documents and procedure — not the
money-and-dates shape the deterministic engine audits today. Holding fee
and break fee turned up zero hits, confirming V1's finding that both caps
live in the Act itself.

| Clause | Heading (short) | Obligation | Classification | Missing input -> supplying milestone |
|---|---|---|---|---|
| 10 | Water efficiency measures for usage charges (Act s 39(1)(b)) | Tenant pays water usage only if premises meet prescribed measures: "for shower heads—a maximum flow rate of 9 litres a minute", dual-flush 3-star toilets from 23 Mar 2025 | Needs new inputs | Premises water-efficiency data and a water-charging flag -> LLM clause audit (lease water clause) plus property data |
| 46 | Water efficiency measures | Companion definitions for cl 10 | Needs new inputs | Same as cl 10 |
| 7 | Condition reports (Act s 29(6)) | "A condition report is to be in the form set out in Schedule 2" | Needs new inputs | The report document itself -> LLM/document milestone (SaaS inspections could supply dates, not the form) |
| 32 | Condition reports reused | Prior report reuse conditions | Needs new inputs | Same as cl 7 |
| 14-17, 20 | Smoke alarm repair windows | E.g. cl 14: repair or replace a non-working alarm "within 2 business days"; annual checks | Needs new inputs | Alarm event/maintenance dates -> future SaaS maintenance mapping |
| 13, 19, 21, 53 | Smoke alarm entry/exemptions | Entry notice and exemption mechanics | Not rule-shaped | Procedural |
| 41 | Interest on rental bonds (Act s 173) | Secretary pays CBA Everyday Access rate, compounded | Not rule-shaped | Obligation sits with the Secretary, nothing lease-side to check |
| 23A-23ZB | Portable rental bonds scheme | Transfer mechanics between bonds | Not rule-shaped | Procedural/administrative |
| 36, 36B, 9A | Social housing charges and increases | Social housing specifics (Act ss 12, 38, 40) | Out of scope | No social-housing flag; private tenancies only |
| 18 | Smoke alarm reimbursement receipts (Act s 64A) | Landlord reimburses within 3 business days of receipt | Needs new inputs | Reimbursement events -> future SaaS maintenance mapping |

Corpus pointers for future milestones: the 2025-05-19 reform touched
cl 4, 5, 39, 40A; 2026-07-01 added 18 clauses (cl 3 definitions churn).
Re-run the term scan after major reforms — the monitor's corpus refresh
keeps this survey re-checkable at any time.

## Mandatory-content survey (LLM clause audit, 2026-07-28)

The Act states 37 sections as "a term of every residential tenancy
agreement". Six crisply decidable, universally present ones became
`MANDATORY_RULES` (ss 33, 50, 51, 52, 63, 70). The rest are excluded from
v1 because presence in a lease document is situational or procedural
rather than a universal content term:

- ss 27, 34, 35, 38-40, 43, 48: money/utility mechanics often expressed
  outside a dedicated clause (rent receipts, charge splits, rent
  reductions) — absence is not a reliable signal.
- ss 49, 53-59, 55A: occupation, sale, entry and access mechanics —
  multi-section clusters; a single missing clause is not decidable.
- ss 64, 64A, 66, 67, 71, 72: repairs/alterations/locks procedure details
  implied by law (s 21) whether or not restated.
- ss 73B-73I (2025 pets), 54A (DV), 74 (transfer): situational regimes.

Known window gap, documented deliberately: the specified-contractor
prohibition was prescribed by Regulation cl 5(a) from 2019-12-16 until it
moved into Act s 19(2)(f) on 2025-05-19. The rule
`nsw.clause.specified_contractor` cites the Act and applies from
2025-05-19; audits as at 2019-12-16..2025-05-18 do not flag the effect
(cl 5's current text no longer contains it, so a Regulation-cited rule
would resolve to the wrong statutory text).
