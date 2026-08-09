# Standard-form comparison design

Sub-project (c) of the Regulation-schedules milestone, closing it ((a)
NSW schedule ingestion and (b) VIC form ingestion shipped the prescribed
terms into the corpus). The clause audit's mandatory family becomes a
true standard-form comparison: every prescribed term of the governing
form is checked against the lease, replacing the 6 hand-written NSW
mandatory rules written before the corpus held the forms. Owner
decisions: coverage AND adverse-alteration detection (not
coverage-only); the 6 old rules are replaced, not kept; eval gates are
per-term (the strictest option); all three carry-ins are in scope
(S-key citation formatter, VIC clause golden enrichment, monitor-port
split).

## Pipeline

A new family runner `run_standard_form` replaces `run_mandatory` inside
the existing clause audit pipeline (job model, worker, LLM plumbing,
yellow verdict, eval harness all reused).

1. **Term set selection.** Prescribed terms are fetched point-in-time
   (as_at) from the corpus: NSW `S1-T*` (59 today); VIC picks the form
   by lease term length - more than 5 years selects `S1-F2-T*` (40),
   otherwise `S1-F1-T*` (32). Term length derives from the lease's
   start and end dates; when no end date exists (periodic) or fields
   are missing, VIC defaults to Form 1 and the findings note the
   assumption. Terms not yet in force at as_at are naturally absent
   from the set.
2. **Deterministic screen** (below). Verbatim or near-verbatim terms
   are green with zero LLM cost.
3. **LLM residual** (below). Screen misses are judged in batches.
4. **Merge.** One finding per term, every finding carries the term's
   S-key citation and as_at; the six terms mapped to the retired Act
   duties carry dual citations (term + Act section).

## Rules and citations

- rule_ids are corpus-generated but stable: NSW `nsw.clause.sf_t{n}`
  (e.g. `sf_t5`, `sf_t30a`); VIC `vic.clause.sf_f1_t{n}` /
  `vic.clause.sf_f2_t{n}` (F1/F2 numbering is independent, so the form
  is part of the identity). Per-term eval gates and goldens key on
  these ids.
- The six retired rules (`nsw.clause.states_rent_payment`,
  `quiet_enjoyment_term`, `tenant_use_term`, `habitability_term`,
  `repairs_term`, `locks_security_term`) are deleted. A small static
  map {standard-form term_no: Act section} - rent s 33, quiet enjoyment
  s 50, use s 51, habitability s 52, repairs s 63, locks s 70, term
  numbers pinned against the standard form text at implementation time -
  gives those findings dual citations so the Act-level reference
  survives.
- **Citation formatter** (carry-in): a service-side `format_citation`
  helper renders machine keys to human labels, emitted as a new
  `citation_label` field on findings (additive; raw fields unchanged).
  Shapes: `52` -> "s 52"; `S1A-2` -> "Sch 1A cl 2"; `S1-T5` ->
  "Sch 1 term 5"; `S1-F1-T5` -> "Sch 1 Form 1 term 5". The NSW/VIC
  part/division asymmetry (NSW keeps schedule identity in division,
  VIC in part) is absorbed entirely inside the formatter.

## Deterministic screen

Both sides normalised the same way: lowercase, whitespace collapsed,
punctuation unified (curly quotes, em-dashes), template placeholders
stripped (`[insert ...]` brackets and `*alternative` markers are
instructions to the form-filler, not lease content). The term's
prescribed text (heading + body) is shingled into 8-token windows and
the containment ratio against the rendered lease text (the existing
clause audit document rendering) is computed - stdlib only, no
dependencies, immune to clause reordering. Containment >= 0.9 makes
the term deterministically green with evidence
`{method: "verbatim", containment: <ratio>}`.

Two term classes always skip the screen and go to the LLM: terms whose
normalised prescribed text is under 12 tokens (too few shingles), and
terms whose prescribed body is empty or under 12 tokens on its own
(the VIC table-content limitation, e.g. Form 1 term 6 "Rent" whose
substance is a table) -
for those the LLM question is built from the heading and form context,
plus the Act duty context for the six mapped terms.

The 0.9 threshold is owned by the eval matrix, not by feel: verbatim
documents must screen all-green and seeded-altered terms must fall
through to the LLM, so a drifting threshold breaks per-term gates
loudly.

## LLM residual

Residual terms are batched 8 per call: neutral Australian SYSTEM +
rendered lease + per-term {rule_id, term number, heading, prescribed
text} + the verdict rubric; structured output returns
{verdict, reasoning, quotes} per rule_id through the existing parsing
conventions. Four outcomes per term: covered -> green (must quote the
lease text relied on); missing -> red; altered_adverse -> red (must
quote both texts and name the adverse departure); uncertain -> yellow
(abstain rule, including unrenderable table-substance terms). Model is
`CLAUSE_AUDIT_MODEL` (currently sonnet-5); call failures use the
existing processor retry/failure states - infrastructure failure is
never disguised as yellow. Cost envelope: honest near-verbatim leases
leave 5-15 residual terms (1-2 calls); worst case NSW 59 terms is 8
calls, VIC F2 is 5, on top of the existing prohibited/fields calls.
Quota accounting stays per-audit.

## Eval (per-term gates)

Goldens are corpus-driven and generated, not hand-written per document:

1. **Verbatim baselines** - the full form assembled verbatim with
   placeholders filled, 2 per jurisdiction/form. Must be all green and
   entirely via the screen (calibrates precision at zero LLM cost).
2. **Seeded-missing** - ~10 deletions per document, scheduled so every
   term is missing in at least 2 documents (NSW ~12 docs, VIC F1+F2
   ~15). Must be red-missing.
3. **Seeded-altered** - every term altered adversely in at least 2
   documents; simple alterations generated programmatically (shortened
   notice periods, inverted obligations, dropped negations), judgment
   alterations hand-curated. Must fall through the screen and be
   red-altered.
4. **Paraphrase** - faithfully rewritten terms that must stay green
   (the hard precision face), 2-4 documents per jurisdiction covering
   high-risk terms first.

Gates: per rule_id P >= 0.9 / R >= 0.8 over its own case set - small n
per term makes the gate coarse but loud (one missed recall case out of
two reads 0.5 and names the term). The eval report emits a per-term hit
table. A full run is ~60 documents and an estimated 200-300 LLM calls
(the screen zeroes the untouched terms); screen calibration cases are
also exact-assert pytest (verbatim screens green, altered screens out)
so CI exercises the deterministic layer free, and LLM evals keep the
existing `llm_eval` deselect marker.

VIC clause golden enrichment rides the same eval wave: breach_penalty
question wording, the two giveaway cleaningreq reds, and extra reds per
existing prohibited rule for recall slack.

## Surfacing and ops

- API: the clause audit family set replaces `mandatory` with
  `standard_form`; findings gain `citation_label`. SaaS: family label,
  label passthrough, and VIC now shows this family. Check the SaaS's
  behaviour on unknown families first; if it degrades, ship SaaS labels
  before enabling the service side. Product copy keeps the
  general-information disclaimer.
- **Monitor-port split** (carry-in, independent ops task): the monitor
  wrapper's tunnel moves to dedicated port 15434 (wrapper script +
  launchd plist DATABASE_URL), so a controller-held 15433 can never
  again block the daily run; runbook updated, the 2026-08-07 warning
  becomes a historical note.

## Out of scope

- Remediation suggestions for altered terms (legal-advice territory).
- Table-content extraction for VIC forms (limitation stands; empty-body
  terms take the LLM path).
- VIC forms other than F1/F2 (notices are not lease content).
- Auto-resubmit of audits on legislation change (backlog, unchanged).
