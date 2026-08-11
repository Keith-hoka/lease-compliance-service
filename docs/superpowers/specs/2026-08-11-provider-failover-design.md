# Provider Failover Design

Date: 2026-08-11. Status: approved for planning.

## Context and goal

Every LLM judgment in the clause audit runs through one Anthropic client
(`app/llm/client.py`). An Anthropic-wide outage takes out Sonnet and Opus
alike, so the existing one-line `CLAUSE_AUDIT_MODEL` switch is not
provider-level redundancy. This milestone adds an OpenAI adapter behind the
judge interface with automatic runtime failover, and merges the client
hardening backlog (recover usage/stop_reason on parse failures) because
failover detection depends on classifying failures correctly.

Findings produced by the backup are full-quality results: the backup model
must pass the same eval gate as any model change before it is configured.

## Owner decisions (2026-08-11)

- **Automatic runtime failover**, not manual config switching. Supersedes
  the 2026-07-29 note for the cross-provider case; the rejection of an
  intra-Anthropic auto-fallback stands.
- **Chain: Anthropic -> OpenAI, single backup.** No DeepSeek, no
  intra-Anthropic hop. DeepSeek stays a future cost-sweep candidate.
- **Client hardening merged into this milestone** (`messages.create` + own
  validation on the Anthropic path).
- **OpenAI API key is available now**; eval runs are unblocked.
- **Backup tier: evaluate both.** Run the mini tier first; if it passes the
  gate it is the backup. If it fails for model-side reasons, escalate to
  the flagship tier and rerun.

## Architecture

Four short modules, one responsibility each:

- `app/llm/client.py` — public face, unchanged imports for `main.py` and
  the eval harness: `JudgeFn` type, `JudgeError` (content-level),
  new `ProviderDown(JudgeError)` (infrastructure-level), model-ref parsing,
  and the `make_judge()` factory.
- `app/llm/providers/anthropic.py` — hardened Anthropic adapter;
  `document_block` and the request builder move here.
- `app/llm/providers/openai_.py` — OpenAI adapter, same shape (trailing
  underscore avoids shadowing the `openai` package).
- `app/llm/failover.py` — circuit breaker plus the composing wrapper.

Each provider module exposes `make_<provider>_judge(model: str) -> JudgeFn`
(closure holding the SDK client). `make_judge()` reads settings, builds the
primary judge (and the backup judge when configured), and returns a single
wrapper object.

**The wrapper is always returned**, even with no backup (backup=None,
breaker inert): callers get one uniform type with no hasattr branching. It
is callable (satisfies `JudgeFn` — signature unchanged, so
processor/standard_form/worker call sites need zero changes) and exposes
`state` (`closed`/`open`/`half_open`) and `drain_models_used() -> list[str]`.

**Model refs carry a provider prefix**: `anthropic:claude-sonnet-5`,
`openai:gpt-5-mini`. A bare ref means anthropic (existing `.env` values
keep working). Unknown prefix raises at startup.

## Error taxonomy and circuit breaker

Classification is the sole trigger for failover:

| Class | Examples | Handling |
|---|---|---|
| `ProviderDown` (infra) | connection error, timeout, HTTP 5xx, 429 after SDK retries | counts toward the breaker, may switch |
| `JudgeError` (content) | refusal, `max_tokens` truncation, validation failure after retry, 4xx client errors | fails the job as today, never switches |

`ProviderDown` subclasses `JudgeError`, so the worker's existing
`except JudgeError` covers both — worker error handling is unchanged.
4xx (400/401/403) are our own request/config mistakes: fail fast and
surface them, never mask them by switching.

**Partial-200 handling** (HTTP 200, unparseable output): the adapter
retries once on the same provider; a second failure raises `JudgeError`
(no switch). One-off flakes are absorbed; a genuinely problematic document
does not trigger failover.

**Breaker state machine** (state lives in the wrapper; worker restart
resets it, which is acceptable — a restart is itself a fresh probe):

- `closed`: calls go to primary. 3 consecutive `ProviderDown` -> `open`.
  Any success resets the counter.
- `open`: all calls go to backup; primary untouched. After a 300 s
  cooldown -> `half_open`. The counter tracks primary only: a backup
  failure raises to the caller without touching breaker state (there is
  nothing further to switch to).
- `half_open`: the next real call probes primary — `ProviderDown` ->
  `open` with a fresh cooldown; any response, including a content-level
  `JudgeError`, proves primary is reachable -> `closed` (the content
  error still propagates to the caller).
- Backup also raises `ProviderDown` -> the call raises; the job fails with
  the existing semantics (manager notified, retry button). Failover adds a
  layer in front of the existing failure semantics; it does not replace
  them.

Rationale for the constants: 3 consecutive failures completes detection
within a single job (~13 calls max) without tripping on one-off flakes;
300 s avoids hammering a down API while not lingering on backup. The
breaker clock is injectable for tests.

Transitions log WARNING (`open`, `half_open`, back to `open`); recovery to
primary logs INFO.

## Anthropic adapter (hardened)

Switch from `messages.parse(output_format=...)` to
`messages.create(output_config={"format": <json_schema>})`. Server-side
constrained decoding is preserved (`output_format` is the deprecated
spelling of the same capability); the difference is that the response
object is in hand before validation, so usage/stop_reason no longer vanish
on parse failures. The exact `output_config` schema shape is pinned from
current SDK docs at planning time.

Response processing order (each step leaves diagnostics):

1. Log usage in the existing format (fields unchanged — monitoring and
   cost tracking depend on this line).
2. Classify `stop_reason`: `refusal` -> `JudgeError` (as today);
   `max_tokens` -> `JudgeError` with output_tokens in the log. Truncation
   previously masqueraded as a bare parse failure with zero diagnostics.
3. Take the last `text` content block (skip thinking blocks).
4. `output_model.model_validate_json(text)`; on failure retry once on the
   same provider; on second failure raise `JudgeError`, logging
   stop_reason and the first 200 characters of the output.

SDK exception mapping: connection/timeout/5xx/429-exhausted ->
`ProviderDown`; 4xx -> `JudgeError`.

Unchanged: `SYSTEM` prompt, `thinking: {"type": "adaptive"}`,
`cache_control: ephemeral` placement, PDF document block,
`max_tokens=8000`, SDK connection-level retries.

## OpenAI adapter

Same processing shape as the Anthropic adapter: create, log usage first,
classify status, extract output, validate ourselves, same single retry.
Dependency: `uv add openai`. Uses the Responses API; exact parameter names
pinned from current docs at planning time.

- **Documents**: text leases framed in `<lease_document>` as today; PDF
  via `input_file` (base64). The eval PDF smokes verify this path.
- **Shared prompt assets**: the same `SYSTEM` (already neutral
  "Australian"), instruction text, and output-model schemas — the eval
  compares models, not prompts.
- **Structured output**: strict json_schema built from
  `model_json_schema()`. Our schemas are Literal/str fields, compatible
  with strict-mode restrictions (all fields required,
  `additionalProperties: false`).
- **Reasoning**: pinned `effort: "medium"` (the analogue of adaptive;
  explicit so provider default drift cannot change behaviour).
- **`max_output_tokens: 16000`**: OpenAI reasoning tokens count against
  the output cap, so 8000 is tight for reasoning plus full JSON.
  Truncation is now visible (`incomplete` status); the eval verifies the
  headroom.
- **Caching**: OpenAI prompt caching is automatic; no `cache_control`
  analogue. Usage log keeps the same line format (cache_read from
  `cached_tokens`, cache_write 0).

Status mapping: `incomplete` (max_output_tokens) -> truncation
`JudgeError` with diagnostics; refusal output -> `JudgeError`;
connection/timeout/5xx/429-exhausted -> `ProviderDown`; 4xx ->
`JudgeError`. Same table as the Anthropic adapter.

**Config guard**: a configured failover model with no `OPENAI_API_KEY`
raises in `make_judge()` at startup — no silently disabled backup that
looks like insurance but is not.

Candidate model IDs (starting at the mini tier) are pinned in the plan
after checking current OpenAI models; the spec pins the mechanism only.

## Config, job.model, observability

New settings in `app/core/config.py`:

- `openai_api_key: str = ""`
- `clause_audit_failover_model: str = ""` — empty disables failover
  (today's behaviour); e.g. `"openai:gpt-5-mini"` enables it.

**job.model records reality**: submission still records the configured
model (`app/routers/clause_audits.py`). After processing, the worker calls
`drain_models_used()` and rewrites `job.model` when it differs — a job
that switched mid-flight records
`"claude-sonnet-5+openai:gpt-5-mini"`. The column drives which audits to
re-run on model changes, so the honest record wins. The worker is a single
asyncio task processing one job at a time, so the drain is race-free.

**Eval pollution guard**: the eval measures a single model; a mid-run
Anthropic flake silently switching to OpenAI would corrupt the scores.
`test_llm_eval.py` gets an autouse guard that fails loudly when
`clause_audit_failover_model` is non-empty, with a message naming the fix.

**Observability**: transition logs as above, plus `/health` gains
`llm_failover: {"state": ..., "active_model": ...}` — `main.py` stashes
the wrapper on `app.state`, the health handler reads it. UptimeRobot uses
HEAD and is unaffected.

## Eval gating and test strategy

Free layer (unit/integration, fake judges and mocked SDKs):

1. Breaker: 3 consecutive `ProviderDown` switch; 2 failures + success
   resets; `JudgeError` never counts; open routes to backup; half-open
   probe recovers or re-opens; double failure raises; backup=None raises
   immediately (today's behaviour); `drain_models_used()` reports
   correctly. Injected clock, no sleeps.
2. Anthropic adapter: `max_tokens` -> `JudgeError` with diagnostics;
   validation failure -> one retry -> raise; retry success returns;
   exception mapping row by row; usage log format unchanged.
3. OpenAI adapter: the same suite, plus a strict-schema compatibility
   check (`model_json_schema()` output accepted by strict mode, no
   network).
4. Config: prefix parsing, missing-key raise, eval guard trips.
5. Worker: fake wrapper reporting mixed models -> `job.model` rewritten to
   the `+`-join.

Paid layer (eval; gates unchanged and never lowered — pooled prohibited
P>=0.9/R>=0.8, standard-form per-term at n=6, fields, PDF smokes):

- **Anthropic regression run** after the hardening rewrite: any request-
  shape change is eval-gated, and this run also clears the standing
  backlog debt (NSW standard-form gates unmeasured under the shipped
  13ebabb prompt).
- **OpenAI mini run**:
  `CLAUSE_AUDIT_FAILOVER_MODEL= CLAUSE_AUDIT_MODEL=openai:<mini> uv run
  pytest -m llm_eval -v -s`. Pass -> backup is the mini tier. Fail ->
  per-case diagnosis under the established discipline (goldens survived
  30 runs; default suspicion is the model side); genuine model-side
  failure -> flagship tier, rerun.
- Results recorded in `docs/model-evals.md`.
- Budget: 2-3 full eval runs.
- The harness-level `_run_standard_form_resilient` retry stays as is:
  harness retries cover infra flakes during measurement, the adapter's
  single validation retry is production behaviour — different layers.

## Deployment and rollout

Increasing-risk order, each step verifiable:

1. **Code lands first, failover unconfigured**: deploy the adapter +
   breaker + hardening with no failover settings in production `.env` —
   behaviour equals today via the inert wrapper. Risk is the hardening
   rewrite alone, covered by the Anthropic regression eval and unit
   tests.
2. **Standard production acceptance**: one real NSW and one real VIC
   audit; findings normal, usage log unchanged, `/health` shows
   `state=closed`.
3. **Enable the backup after the eval gate**: add `OPENAI_API_KEY` and
   `CLAUSE_AUDIT_FAILOVER_MODEL` to production `.env`, restart. Verify
   `/health` shows the backup configured and normal traffic still runs on
   Anthropic.
4. **Real backup smoke** (no waiting for a real outage): temporarily set
   `CLAUSE_AUDIT_MODEL=openai:<model>` and run one real audit — this
   exercises the OpenAI adapter's full production path (network, key,
   dependencies); verify finding shapes, then switch back. The breaker
   path itself is covered by unit tests, not production drills. Secrets
   handling follows the established convention (into `.env`, never
   printed, never in shell history).

Deploy mechanics unchanged (`deploy.sh sha-<short>`, 502 during boot
window is normal, verify image via docker inspect).

Documentation: `docs/model-evals.md` gains the eval rows and this
decision record; `deploy/README.md` gains the two env vars and the backup
smoke procedure.

## Out of scope (YAGNI)

- DeepSeek adapter — single backup decided; the provider prefix already
  leaves room for a third provider.
- Active notification (email/webhook) on failover — WARNING logs plus
  `/health` suffice; the service has no mail machinery and gains none.
- SaaS changes — none; the `+`-join model string displays as-is.
- Breaker state persistence — reset on restart is acceptable.
- Fine-grained 429 throttling/queueing — SDK retries cover it; beyond
  that, `ProviderDown` semantics apply.
