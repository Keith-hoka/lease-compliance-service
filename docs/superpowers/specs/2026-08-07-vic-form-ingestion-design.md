# VIC form ingestion design

Sub-project (b) of the Regulation-schedules milestone ((a) NSW schedule
ingestion shipped 2026-08-07; (c) the standard-form comparison family
follows). VIC Regs 2021 Schedule 1 ("Schedule 1—Forms") holds 24
prescribed forms - including Form 1 (residential rental agreement of no
more than 5 years, 32 numbered terms) and Form 2 (more than 5 years,
40 terms), the two agreements sub-project (c) will compare leases
against - but contributes nothing to the corpus today: the forms' terms
are Normal-style paragraphs with literal tab-separated numbers, which
`parse_docx` does not recognise. Owner decision: ingest every numbered
item across all 24 forms (roughly 236 terms), matching the two prior
all-schedules decisions.

## Parser

Cache-verified structure (version 009): the Schedule 1 region opens
with a "Heading - PART"-style "Schedule 1—Forms" paragraph; inside it,
"New Form Heading"-style paragraphs matching `^Form (\d+[A-Z]?)\b` open
a form scope, the immediately following New Form Heading paragraph is
the form's title, and `PART ...` headings (same style) subdivide a form
without closing it. Terms are paragraphs matching `^(\d+[A-Z]?)\.\t` -
the same tab convention as VIC body sections - numbered continuously
across a form's PARTs.

`parse_docx` gains a form sweep inside schedule regions:

- A term yields `section_no="S{sch}-F{form}-T{term}"` (Schedule 1 today,
  so `S1-F1-T7` shapes; the form discriminator is forced by 24 forms
  sharing one schedule - a documented asymmetry with NSW's one-form
  `S{n}-T{m}`), heading = the term's own title after the tab, body = the
  paragraph's remaining text plus following paragraphs until the next
  term, form, PART heading, or schedule boundary.
- `part = "Schedule 1—Forms"` (the schedule identity, exactly as VIC's
  existing S3-S5 schedule rows already use `part`) and
  `division = "Form {form} <form title>"` (the form identity - the
  schedule-internal structure slot, matching VIC's own precedent of
  putting schedule-internal Parts in `division`). This keeps VIC
  internally consistent; the (a) final review flagged that NSW puts the
  schedule identity in `division` instead - a cross-jurisdiction
  asymmetry deliberately left for (c)'s citation-rendering design,
  where a formatter must humanise S-keys anyway (the review's "s
  S1-T5" finding).
- "Side Note"-style paragraphs (amendment annotations) are skipped.
- Existing S3-S5 numbered-clause schedule handling and body-section
  parsing are untouched.

Accepted limitation (the established VIC precedent): table content
inside forms does not enter term bodies - python-docx's paragraph
stream excludes tables, and body tables were already an accepted skip
at corpus build time. Term bodies are paragraph text.

Three further refinements landed during the Task 2 rebuild, once the
full historical cache exposed cases the single-version spike above
didn't cover. Form openers are matched case-insensitively - rescuing
the all-caps styled "FORM 3A"/"FORM 16A" openers in versions 005+ - and
version 001's Normal-styled "Form 5" opener is caught by an exact-text
fallback, itself case-insensitive. A form whose term
numbering restarts partway through - Form 3A's three independently
numbered PART sequences, present from version 005 onward - cannot be
keyed by the continuous-numbering model and is deliberately skipped in
its entirety, contributing no terms rather than colliding keys. A
term's after-tab text becomes its heading only when 150 characters or
fewer once cleaned; longer, free-flowing prose terms instead get an
empty heading with the full text preserved as the first line of the
body, so nothing is ever truncated or lost.

## Rebuild and rollout

The (a) pipeline shape, scoped to one instrument:

- Wipe only `residential-tenancies-regulations-2021` (sections ->
  ingested_versions -> act row); the RTA Act 1997 and both NSW
  instruments are untouched. Rebuild from the DOCX cache
  (`data/raw/vic/`), extending the existing rebuild script to take the
  VIC Regulations as a target.
- Probes: existing S3-S5 and body counts unchanged; Form 1 yields 32
  terms and Form 2 yields 40 in the newest version; total form terms
  219 (the pre-refinement estimate of ~236 included Form 3A's ~28 terms,
  now deliberately skipped); a Form 1 point-in-time boundary chosen
  empirically (the
  Form 1 side note records amendment by S.R. 123/2025, so a 2025
  version boundary exists) flips absent-to-hit.
- CI corpus dump refreshed; existing VIC rule and clause tests stay
  green.
- Production: rebuild over the ssh tunnel, endpoint acceptance for an
  `S1-F1-T{m}` term (today-hit plus the amendment boundary), monitor
  kickstart reporting no-new-versions - with the controller's own
  tunnel closed first (the (a) lesson: a held 15433 blocks the monitor
  wrapper's tunnel).

## Out of scope

- The comparison family (sub-project c).
- Table-content extraction for forms.
- Any NSW parser or corpus change; any rule or clause-audit change.
