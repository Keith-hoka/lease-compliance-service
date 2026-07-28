# LLM Clause Audit Design

Accept a lease document (PDF or plain text), judge its clauses against NSW
law with an LLM, and return findings that carry statutory citations and may
abstain (`yellow`). Audits run as async jobs; documents are wiped once the
job finishes. Service-side only — the SaaS tail (submitting stored PDFs,
rendering clause findings) is a later milestone.

## Decisions (brainstorm outcomes)

- **Input: both PDF and plain text**, exactly one per job.
- **Three check families**: prohibited terms, field cross-check, mandatory
  terms — each landing with its own eval before the milestone closes.
- **Async job API.** A clause audit makes three LLM calls and can take
  minutes on a PDF; POST returns a job id, GET polls.
- **Model: `claude-opus-4-8`**, pinned in settings. Strong model first to
  build a trustworthy golden-set baseline; cheaper candidates (Sonnet,
  Haiku, OpenAI mini-class, DeepSeek) are gated later by the same eval.
  The model id is a config value; switching is a config change plus an
  eval rerun and an `engine_version` bump.
- **Documents are discarded after processing** — success and failure alike,
  in the same commit that finalises the job. Results keep only clause
  quotes. Re-audit after a legislation change means the client resubmits
  (the SaaS stores the original files anyway).

## API

`POST /v1/clause-audits` (multipart/form-data, same `X-API-Key` tenancy as
`/v1/audits`):

- `file` (PDF, max 10 MB) **or** `text` (max 200k chars) — exactly one;
  violations return 413/422.
- `payload` (JSON part): `jurisdiction` (`NSW` only), `as_at` (optional,
  default Sydney today), `client_ref` (optional), `lease` (optional —
  presence gates the field cross-check family; money/date subset of
  `LeaseInput`).
- Returns `202 {id, status: "pending"}`.

`GET /v1/clause-audits/{id}` (tenant-scoped):

```
{ id, status: pending|running|succeeded|failed,
  jurisdiction, as_at, engine_version, model, client_ref,
  findings: [ { rule_id, verdict: red|green|yellow, summary, evidence,
                clause_quote,          # lease text excerpt; may be null
                citations: [{act, section_no, as_at, section_id}],
                skip_reason } ],       # the existing Finding + Citation shapes
  discrepancies: [ {field, document_value, submitted_value} ],
  error, created_at, completed_at }
```

`GET /v1/clause-audits?client_ref=` — list endpoint mirroring the audits
list, for SaaS parity.

- `Finding.verdict` (`app/rules/base.py`) formally gains `yellow`; the
  deterministic engine never emits it.
- `discrepancies` carry no citation: they are data-integrity findings, not
  legal judgments. The "every finding carries a citation" rule holds for
  `findings` in full.

## LLM layer (`app/llm/`)

- **Three calls share one prompt cache.** The system prompt is identical
  across families (role, abstention discipline, cite-only-provided-text,
  general-information-not-legal-advice framing). The first user content
  block is the document (text block, or base64 PDF `document` block) with
  `cache_control`; the family-specific instruction follows after the
  breakpoint. First call writes the cache, the other two read.
- **Statutory text comes from the corpus, not model memory.** The family
  instruction embeds each rule's section text fetched via
  `section_at(slug, section, as_at)` at the job's `as_at` — the model
  judges against the text actually in force.
- **The model never produces citations.** Each rule pins act + section in
  code, exactly like the deterministic rules; the worker resolves the full
  citation through `section_at`. `None` (repealed / not yet commenced at
  `as_at`) skips the rule, matching applies-window semantics.
- **Structured output** via `client.messages.parse()` with per-family
  Pydantic schemas; rule ids are enum-locked so the model cannot invent
  rules.
- **Evidence discipline.** A prohibited-terms `red` must carry
  `clause_quote`. On the text path the quote is normalised and matched
  against the document; no match downgrades the finding to `yellow`, as
  does a `red` with no quote at all. The native-PDF (vision) path has no
  full text to match against and skips quote verification — a known
  limitation.
- **Text-first rendering.** PDFs go through `pypdf` text extraction first;
  if the text layer averages >= 200 chars/page the cheap text path is
  used, otherwise the PDF is sent natively as a base64 `document` block.
  Plain-text input always takes the text path.
- **Call parameters**: model from settings (`clause_audit_model`),
  `thinking={"type": "adaptive"}` (explicit — 4.8 defaults off),
  `max_tokens=8000`, non-streaming, no temperature (removed on 4.8;
  determinism comes from prompts and enum locking). SDK default retries
  cover transient errors; `stop_reason == "refusal"` or a parse failure
  fails the job with an error message.
- New dependencies: `anthropic`, `pypdf`.

## Check families

**Family 1 — prohibited terms** (`findings`, cited). Act s 19's
enumerated terms (professional carpet cleaning, fumigation, specified
insurance) plus any terms prescribed under s 19(1) by the Regulation. The
rule list is fixed by the milestone's first implementation task: pin s 19
from the corpus, scan the Regulation for prescribed prohibited terms, give
every rule a pinned-text docstring and its own applies window (post-2019
additions commence when they commenced). Verdicts: `red` = a term "having
the effect" is present (semantic matching is the LLM's job) with a quote;
`green` = absent; `yellow` = unsure.

**Family 2 — field cross-check** (`discrepancies`, uncited). Runs only
when `payload.lease` is present. Fields: `rent_amount` + `rent_frequency`,
`start_date`, `end_date`, `bond_amount`, `rent_in_advance_amount`,
`holding_deposit_amount`, `other_security_amount`, `break_fee_amount`.
The model only extracts (per-field document value or null, with a quote);
the comparison is code: ISO dates, `Decimal` amounts, frequency enum
normalisation. A mismatch emits `{field, document_value, submitted_value}`;
not-found-in-document is not a mismatch.

**Family 3 — mandatory terms** (`findings`, cited). The Regulation's
Schedule 1 standard form is not in the corpus (the parser excludes
`sch.*`), so v1 uses a hand-curated checklist in code: scan the Act for
"must" requirements on agreement content, keep 5–8 crisply decidable
items, each a rule citing the Act section that imposes the obligation,
statutory text pinned in the docstring. Vague candidates are excluded and
recorded, `docs/rule-candidates.md` style. Verdicts: `red` = required term
absent (no quote — absence has none); `green` = present, with a quote;
`yellow` = unsure. True standard-form comparison arrives with schedule
ingestion in a later milestone.

## Job worker

Table `clause_audit_jobs`: `id`, `client_id` (indexed), `client_ref`
(nullable, indexed), `jurisdiction`, `as_at`, `status`
(`pending|running|succeeded|failed`), `document` (nullable bytea — wiped
at completion), `document_kind` (`pdf|text`), `lease` (nullable JSON),
`findings` (JSON), `discrepancies` (JSON), `engine_version`, `model`,
`error` (nullable), `created_at`, `started_at`, `completed_at`.

- A FastAPI lifespan task runs the worker loop; shutdown cancels it
  gracefully. Claiming uses `FOR UPDATE SKIP LOCKED` on the oldest
  `pending` row; idle polling every 2 s; one job at a time (scaling means
  more worker tasks later — `SKIP LOCKED` already permits it).
- Success writes findings/discrepancies, `succeeded`, and `document =
  NULL` in one commit. Failure (refusal, parse error, timeout) writes
  `failed` + error and wipes the document the same way. No job-level retry
  queue: the SDK's transient retries are the retry story; a failed job is
  resubmitted by the client.
- The whole job runs under `asyncio.wait_for` (900 s) so a hung call
  cannot occupy the worker forever.
- Startup sweep: leftover `running` jobs (process died mid-flight) become
  `failed` ("interrupted by restart") with documents wiped; `pending` jobs
  survive restarts untouched and are simply picked up.
- Job rows persist as audit history — after the wipe they hold no personal
  data beyond the approved clause quotes.

## Eval and testing

**Layer 1 — mocked LLM, runs in CI.** A fake client returns canned parsed
outputs; tests cover job claiming and the status machine, document wiping
(success, failure, startup sweep), quote-mismatch and missing-quote
downgrades, citation resolution and the `section_at` -> `None` skip, field
comparison normalisation, family-2 gating, API contract (202, tenant
isolation, 413/422, exactly-one-input), and the text-extraction threshold
with two fixture PDFs (text-layer and image-only).

**Layer 2 — real model, opt-in.** Marked `@pytest.mark.llm_eval`, skipped
without `ANTHROPIC_API_KEY` (the corpus-test skip pattern).

- Golden sets in `tests/golden/clauses.py` (Python data modules, the
  existing `tests/golden/leases.py` convention): per rule ~8–10 cases of
  mini-lease text + expected verdict; positives include paraphrases,
  negatives include hard look-alikes ("keep the carpet clean" is not
  "professionally cleaned"). Family 2 cases pair mini-lease text with
  submitted fields and expected discrepancies; family 3 cases include and
  omit mandatory terms.
- Scoring: precision = red-correct / all-red-calls (red on a green case is
  the FP); recall = red-called / all-red-cases, where `green` **or**
  `yellow` on a red case counts as a miss. v1 thresholds per rule:
  precision >= 0.9, recall >= 0.8, adjustable per rule in the golden file;
  yellow rate is reported, not gated.
- Cost: text-path mini-leases keep a full run around US$2–3 on Opus. Run
  manually before shipping and whenever prompts, rules, or the model
  change — never in regular CI.
- Model comparison: `CLAUSE_AUDIT_MODEL=... uv run pytest -m llm_eval`
  runs the same golden sets against a candidate; the harness prints a
  per-rule P/R table. A switch requires meeting thresholds and bumps
  `engine_version`.
- PDF smoke: one seeded-violation lease as a text-layer PDF and a
  rasterised scan of the same document, run end-to-end against the real
  model, asserting the seeded violation is found.

**Eval-first discipline:** a new rule ships with its golden cases in the
same commit; prompt, rule-list, or model changes bump `engine_version` and
rerun layer 2.

## Out of scope

The SaaS tail (upload stored PDFs, render clause findings and `yellow`),
Regulation schedule ingestion and true standard-form comparison, automatic
clause-audit re-runs on legislation change, the model downgrade decision
itself (the eval enables it later), VIC, and any change to the
deterministic audit pipeline.
