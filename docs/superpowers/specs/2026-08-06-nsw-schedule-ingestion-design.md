# NSW schedule ingestion design

Sub-project (a) of the Regulation-schedules milestone ((b) VIC Form 1
ingestion and (c) the standard-form comparison family follow). The NSW
corpus has zero schedule content: `parse_whole_act` only walks
`id="sec.N"` fragments, so Reg 2019 Schedule 1 - the Standard Form
Agreement that (c) will compare leases against - was never captured.
The whole-view HTML carries schedules in full (`div.frag-schedule`
containers holding both schedule-level clauses and, inside `frag-form`,
the standard form's 59 numbered terms; 1163 sch-prefixed ids in the
current Regulation cache alone). Owner decision: ingest every parseable
schedule clause across both NSW instruments, matching the VIC
all-schedules precedent.

## Parser

Cache-verified structure (2026-07-30 Regulation version): schedules
carry two distinct clause shapes with colliding number domains, so they
get two keyspaces.

`parse_whole_act` gains a second sweep over `div.frag-schedule`
containers. For each, the schedule number and heading come from the
container's heading, and two extractions run inside it:

1. Schedule-level clauses - `frag-clause` fragments with id
   `sch.{n}-sec.{m}` (trailing dot present or absent; Schedule 4's ids
   omit it) yield `section_no="S{n}-{m}"`, heading from the clause
   heading. Schedule 1 has six of these (Definitions, Continuation,
   Ending, the notice clauses, Warning); Schedule 4 has one.
2. Form terms - inside `frag-form`, the standard form's numbered terms
   are `frag-li` items whose `frag-no` matches `^\d+[A-Z]?\.$` (59
   unique terms 1-59 in Schedule 1; 5 in Schedule 3). Each yields
   `section_no="S{n}-T{m}"` (T disambiguates the term domain from the
   clause domain - both start at 1 in Schedule 1), heading = the
   nearest enclosing `frag-blockgroup` heading (RENT, SMOKE ALARMS,
   ...), body = the term's full text including nested sub-paragraphs.

Both shapes set part=None and division="Schedule {n} <heading>",
mirroring VIC's use of division for schedule-internal structure.
History notes are decomposed as today. Schedules with neither shape
(Schedule 2's condition-report table) contribute nothing - the VIC
precedent. Body-section parsing is untouched. The comparison family
(sub-project c) will cite the form terms as S1-T{m}.

## Historical backfill: wipe and rebuild from cache

The loader deliberately skips already-ingested (act, version_date)
pairs, so schedules cannot be appended to existing version rows.
Strategy: delete the two NSW instruments' rows (acts + sections) and
re-ingest every version from the local cache (`data/raw/nsw/` holds
all fetched versions; zero live refetches). Each historical version's
schedules land with correct point-in-time windows. VIC is untouched.

Accepted consequences:
- Stored audit findings' NSW `section_id` UUIDs become dangling - no
  code path resolves those UUIDs (the SaaS and the API render act +
  section_no straight from the findings JSON), so this is inert
  historical residue.
- The CI corpus dump must be refreshed afterwards
  (`scripts/refresh-corpus-dump.sh`).
- Production takes the rebuilt corpus over the established ssh-tunnel
  sync path.

## Testing

- Parser tests on a synthetic HTML fixture: schedule container with a
  trailing-dot clause id, a `frag-form`-nested term, a definitions
  sub-block, and a history note - asserting the `S1-1` key, heading,
  division "Schedule 1 Standard Form Agreement", full body text, and
  that a coexisting ordinary section still parses identically.
- Rebuild probes (recorded in the task report): body-section counts
  unchanged before/after; S-prefix row counts added; a Schedule 1 term
  resolves point-in-time today; a version boundary chosen empirically
  in-task (a form term present in the newest cached version and absent
  in the oldest - the form gained terms across the reform years) flips
  absent-to-hit across its version date.
- Existing NSW corpus tests stay green (they query by slug +
  section_no; UUID churn from the rebuild is invisible to them).
- The loader's integrity guards (duplicate keys, zero sections) stand
  watch over the rebuild unchanged.

## Rollout

1. Local wipe + rebuild from cache, probes, full suite, CI dump
   refresh, commit - CI runs the corpus tests against the refreshed
   dump.
2. Production corpus sync via the tunnel; acceptance: a point-in-time
   legislation query for a Schedule 1 term hits on
   api.leasekoala.com and respects the amendment boundary; the daily
   monitor's next kickstart reports no-new-versions for all four
   instruments. Ledger and memory.

## Out of scope

- VIC Form 1 ingestion (sub-project b) and the comparison family (c).
- Any handling for schedules without numbered clauses beyond skipping
  them.
- Rule or clause-audit changes.
