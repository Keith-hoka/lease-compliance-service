# Change Monitor Design

Detect when the compliance conclusions of monitored leases change, and let
API clients discover those changes by polling. The monitor exploits the two
assets V1 built: the temporal legislation store and persisted audits.

A stored audit is point-in-time and historically stable: a newly published
legislation version never rewrites what was in force at an old `as_at`.
What changes is the *current* conclusion for the same lease. Verdicts move
for three reasons: a new legislation version, an engine upgrade, and plain
date passage (a rule's `applies_from` or repeal date crossing today). The
monitor therefore always re-runs and compares; it never tries to predict
which of the three causes applies.

## Decisions (brainstorm outcomes)

- **Execution form: CLI** — `uv run python -m app.monitor nsw
  [--skip-fetch]`. Fetching needs headed real Chrome (Cloudflare blocks
  headless), so scheduled runs live on a GUI machine via the OS scheduler
  (launchd/cron), not inside the API process.
- **Semantics: re-run to today and compare.** For each monitored lease,
  re-run its payload at today's date (Australia/Sydney) and diff verdicts
  against the stored findings. Record only differences.
- **Lease identity: `client_ref`.** Callers pass their own lease id when
  creating an audit. The monitor re-runs the latest audit per identity;
  audits without `client_ref` are one-off queries and are not monitored.
- **Tenant isolation: API key = tenant, now.** The service is a public
  multi-client API. Keys carry a tenant label; all reads and the monitor
  grouping are tenant-scoped. Without this, `client_ref` values from
  different companies would collide and `audit-changes` would leak across
  tenants.
- **Notification: persist + poll.** Changes are rows served by
  `GET /v1/audit-changes`. The CLI prints a run summary. Webhooks wait
  until a deployment exists.

## Auth: keys carry tenant identity

`API_KEYS` becomes comma-separated `key:client_id` pairs, e.g.
`abc123:rentalapp,xyz789:acme`. `require_api_key` resolves the presented
key and returns its `client_id`; routes receive it as
`ClientDep = Annotated[str, Depends(require_api_key)]`. Tenant identity is
derived server-side from the key — callers cannot forge it.

## Data model

`audits` gains two indexed columns:

- `client_id: str` (String(50), not null) — the authenticated tenant.
  Migration backfills existing rows with `"legacy"`.
- `client_ref: str | None` (String(100)) — the caller's own lease id.

New table `audit_changes`:

| column | type | notes |
|---|---|---|
| `id` | UUID pk | |
| `client_id` | String(50), indexed | denormalised for tenant-scoped polling |
| `client_ref` | String(100), indexed | |
| `old_audit_id` | FK audits.id | baseline audit |
| `new_audit_id` | FK audits.id | audit produced by the monitor run |
| `changes` | JSON | `{rule_id: {"from": verdict-or-null, "to": verdict-or-null}}` |
| `created_at` | timestamptz, server default | the `since` polling cursor |

One hand-written Alembic revision adds the columns, the table and the
indexes; downgrade reverses them.

## Monitor pipeline

Module layout: `app/monitor/runner.py` holds the testable logic;
`app/monitor/__main__.py` is a thin CLI.

One run does, in order:

1. **Corpus refresh** (skipped with `--skip-fetch`): fetch the landing
   page, parse timeline dates, subtract already-ingested dates, fetch and
   load only the new versions. Pure reuse of the V1 fetcher/parser/loader.
   `--skip-fetch` supports offline re-checks after an engine upgrade.
2. **Monitored set**: the latest audit (by `created_at`, then `id`) per
   `(client_id, client_ref)` where `client_ref` is not null and the
   jurisdiction matches the CLI argument.
3. **Re-run**: `LeaseInput(**audit.input)` through `run_audit` at
   `sydney_today()`.
4. **Diff**: `diff_findings(old, new)` — a pure function comparing
   `{rule_id: verdict}` maps. Output holds only changed rules; a rule
   present on one side only (engine upgrade) uses `null` for the absent
   side. `skipped` participates: red → skipped is a real transition (e.g.
   the s42 repeal).
5. **Persist on difference only**: a new `Audit` row (same input and
   tenant keys, today's `as_at`, current `ENGINE_VERSION`) plus an
   `audit_changes` row linking old to new. An empty diff writes nothing,
   so a second run diffs against the just-written audit and finds nothing:
   the run is idempotent.
6. **Summary**: versions ingested, audits checked, changes found (per
   `client_ref` with rule deltas).

## API

- `POST /v1/audits` — `AuditCreate` gains `client_ref: str | None`;
  the server stamps `client_id` from auth; `AuditInfo` echoes
  `client_ref`.
- `GET /v1/audits/{id}` — 404 unless the audit belongs to the caller's
  tenant (no distinction between "missing" and "not yours").
- `GET /v1/audit-changes` (new) — query `since: datetime | None`,
  `client_ref: str | None`, `limit: int = 100`; always tenant-scoped;
  ordered by `created_at` ascending so `since` + order is the polling
  cursor. Items: `{id, client_ref, old_audit_id, new_audit_id, changes,
  created_at}`.

## Targeted refactors (V1 leftovers this work needs)

- `NSW_ACT` and `ensure_act` move from `app/ingest/__main__.py` (which
  runs `main()` on import, so nothing can import from it) to
  `app/ingest/registry.py`; `__main__.py` gains an
  `if __name__ == "__main__"` guard.
- The inline Sydney-today expression in the audits router becomes
  `app/core/dates.py: sydney_today()`, shared with the monitor.

## Error handling

Linear code; real errors surface. Stated assumption: stored audit inputs
remain valid `LeaseInput` payloads because the schema only evolves by
adding optional fields. The corpus-refresh step runs before any audit
writes, so a fetch failure aborts the run without touching audit data.

## Testing and eval

Deterministic capability, so the eval is exact-assert pytest:

1. `diff_findings` unit tests: flip, added rule, removed rule,
   skipped transitions, no-change gives `{}`.
2. Runner scenario tests on the synthetic store: seed act v1, store an
   audit with tenant keys, load v2 closing a section, run the monitor,
   assert the delta and the new audit's tenant keys; run again and assert
   zero new rows. Audits without `client_ref` stay unmonitored; the same
   `client_ref` under two tenants groups separately.
3. Corpus-gated temporal eval (reuses the skip-guard fixture): an audit
   at `as_at` 2024-06-01 with a fixed-term lease re-run today must show
   `nsw.fixed_term_increase_disclosure: red → skipped` (the s42 repeal).
4. API tests: create with `client_ref`, poll with `since`, tenant
   isolation both for `GET /v1/audits/{id}` and the changes list, 401.
5. Tests never fetch: they exercise the `--skip-fetch` path, plus a pure
   test for the new-version set arithmetic.
6. Migration verified up → down → up locally.

## Out of scope

Webhooks, email, VIC, Regulation ingestion, re-writing history on
retrospective site corrections, pagination beyond `limit`, and any
account-management UI. The tenant model stays "a labelled API key" until a
real need outgrows it.
