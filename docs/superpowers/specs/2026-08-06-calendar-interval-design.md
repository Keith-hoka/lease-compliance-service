# Calendar interval semantics design

Both Acts say "12 months"; the engine approximates with
`timedelta(days=365)`. A 365-day window spanning a leap day is one day
shorter than the statutory 12 calendar months, so the frequency rules
falsely green an increase made one day before the anniversary (flagged
by the VIC final whole-branch review as a cross-jurisdiction decision).
Chosen fix: calendar-anniversary reckoning at every site that measures
statutory months or years.

Legal grounding: the Interpretation Act 1987 (NSW) and the
Interpretation of Legislation Act 1984 (Vic) both define month as
calendar month, and periods of months are reckoned by the
corresponding-date rule (Dodds v Walker): a 12-month period ends on the
corresponding day of the twelfth month after, falling back to that
month's last day when no corresponding day exists.

## Helper

`add_months(d: date, months: int) -> date` in `app/rules/base.py`,
stdlib only: target year/month arithmetic with the day clamped to
`calendar.monthrange`. Examples fixed by tests: Jan 31 + 1 month ->
Feb 28 (Feb 29 in leap years); Feb 29 + 12 months -> Feb 28. The
docstring cites the corresponding-date rule and both interpretation
acts - it is the legal basis for every caller.

## Call sites (inequality directions unchanged)

1. `nsw.rent_increase_frequency`: red when any adjacent pair has
   `later < add_months(earlier, 12)`.
2. `nsw.rent_increase_first_year`: red when
   `first < add_months(start_date, 12)`.
3. `vic.rent_increase_frequency`: same shape as 1.
4. NSW fixed-term disclosure gate: `end_date < add_months(start_date, 24)`
   replaces `term_days < 730`.
5. NSW break-fee scale gate: `end_date <= add_months(start_date, 36)`
   replaces `term_days <= 1095`.

The `YEAR` constants in both rule modules are removed once no consumer
remains. Summaries keep their wording ("less than 12 months apart" -
now literally true); evidence keeps `gaps_days` as information. An
increase on the anniversary day itself is green: NSW's "more than once
in any period of 12 months" and VIC's "intervals of less than 12
months" both exclude the exact-anniversary case. Rule docstrings gain
one line noting months are reckoned as calendar months under the
corresponding-date rule.

## Testing

- `add_months` unit tests covering the clamp cases exhaustively
  (month-end overflow, Feb 29 anniversary, negative-free usage).
- Per call site, a leap-boundary red/green pair: the
  2023-03-01 -> 2024-02-29 gap (365 days, one day before the
  anniversary) is the nail for this fix and must be red for both
  frequency rules; a Feb 29 start greens an increase on the clamped
  anniversary (2025-02-28) and reds one on 2025-02-27; term-length
  leap boundaries for sites 4 and 5.
- Existing 365-day boundary tests stay untouched and passing -
  non-leap windows are semantically identical.
- These run in CI against the corpus dump (the fixture landed
  2026-08-06), so the new boundaries are CI-enforced.

## Version and rollout

- `ENGINE_VERSION` bumps to `1.5.0` (verdict-affecting semantics).
- Migrationless deploy; production acceptance is one audit with a
  leap-spanning increase pair returning the frequency red in each
  jurisdiction's rule.
- Stored audits are not re-audited: an engine semantics change does not
  flow through the legislation monitor, boundary flips are one day
  wide, and new audits use the corrected reckoning (accepted at the
  approach decision).

## Out of scope

- Any other rule logic, thresholds, or messages.
- Clause-audit rules (no day arithmetic).
- Re-auditing stored results.
