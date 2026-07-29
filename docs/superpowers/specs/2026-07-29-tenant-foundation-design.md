# Tenant foundation design

Sub-project 1 of the external-tenant commercialisation track (1 tenant
foundation -> 2 developer portal -> 3 Stripe billing). This stage makes it
safe to hand API keys to third parties: keys move from the `API_KEYS`
environment variable into Postgres, every tenant gets rate limits and a
daily clause-audit quota, and usage is recorded in a shape billing can
consume later. No new infrastructure: Postgres only, no Redis, single
instance.

## Data model

Three tables, one Alembic migration.

`tenants`
- `id` uuid PK
- `client_id` text unique (existing string, e.g. `rentalapp`)
- `name` text
- `status` text: `active` | `suspended`
- `rpm_limit` int, default 60
- `clause_audits_per_day` int, default 10
- `created_at` timestamptz

`api_keys`
- `id` uuid PK
- `tenant_id` uuid FK -> tenants
- `key_hash` text unique (SHA-256 hex of the full key; plaintext never stored)
- `prefix` text (first 8 chars of the key, e.g. `lk_a1b2c`, for humans)
- `status` text: `active` | `revoked`
- `created_at` timestamptz
- `last_used_at` timestamptz nullable

`usage_counters`
- composite PK `(tenant_id, day, endpoint_class)`
- `count` int, upsert-incremented
- `endpoint_class` text: `audit` | `clause_audit` | `legislation`

Key format: `lk_` + 32 url-safe random chars. The plaintext is shown once
at creation and never again. Per-audit detail for billing already lives in
`clause_audit_jobs`; `usage_counters` is the daily rollup.

## Authentication path

`require_api_key` becomes: SHA-256 the presented key -> process-local TTL
cache (60 s) -> on miss, one query joining `api_keys` to `tenants` ->
cache `(tenant_id, client_id, status, rpm_limit, clause_audits_per_day)`.
Routers keep receiving `client_id`; no handler signatures change.

- Unknown or revoked key -> 401.
- Tenant `suspended` -> 403.
- Revocation and suspension take effect within the 60 s cache TTL.
- `last_used_at` is updated only on cache miss (at most one write per key
  per minute, off the hot path).

## Rate limiting

Per-minute limit: an in-process token bucket per tenant (capacity
`rpm_limit`, refill `rpm_limit/60` per second), enforced as a dependency
after auth on every `/v1` route. Over limit -> 429 with a `Retry-After`
header. Single instance; buckets reset on restart, which is acceptable.

Clause-audit daily quota: before creating a job, `POST /v1/clause-audits`
counts the tenant's `clause_audit_jobs` created today (UTC). At or over
`clause_audits_per_day` -> 429 whose detail states the quota and that it
resets at midnight UTC. The existing per-tenant in-flight cap of 10 stays:
it guards concurrency, the quota guards volume.

## Usage recording

Only billable events are counted, by an explicit helper call on the
success path of three handlers:

- `POST /v1/audits` -> `audit`
- `POST /v1/clause-audits` -> `clause_audit`
- `GET /v1/legislation/*` -> `legislation`

Status polling (`GET /v1/clause-audits*`) and `/v1/changes` are not
counted but remain rate-limited. The helper upserts
`usage_counters (tenant_id, utc-day, class) += 1`.

## Admin CLI

`uv run python -m app.tenants <command>`, matching the `app.ingest` CLI
convention. On the server: `docker compose exec api uv run python -m
app.tenants ...` (documented in deploy/README.md).

- `create <client_id> --name NAME [--rpm N] [--clause-per-day N]` -
  creates tenant plus first key; prints the plaintext key once
- `new-key <client_id>` - issues an additional key, prints once
- `revoke-key <prefix>` - revokes the key matching the prefix
- `suspend <client_id>` / `activate <client_id>`
- `set-limits <client_id> [--rpm N] [--clause-per-day N]`
- `list` - tenants with status, limits, today's usage
- `usage <client_id> [--days 30]` - per-day counters
- `import-env-keys` - idempotent import of `API_KEYS` env pairs

## Migration and rollout

Lifespan runs the idempotent env import on startup: for each
`key:client_id` pair in `API_KEYS`, if the key hash is absent from the
database, create the tenant (if needed) and key. One deploy therefore
switches auth to the database with the rentalapp key seeded on boot; the
SaaS never notices. Afterwards: remove `API_KEYS` from the server `.env`
(the import becomes a no-op), then raise rentalapp limits via CLI
(`set-limits rentalapp --rpm 300 --clause-per-day 200`).

## Default limits and cost exposure

One sonnet-5 clause audit is ~3 LLM calls, ~15k input + ~2k output tokens,
about US$0.07. Defaults for new tenants:

| Limit | Default | Daily worst case |
|---|---|---|
| rpm_limit | 60 | cheap reads only |
| clause_audits_per_day | 10 | ~US$0.70 LLM cost |

A fully saturated default tenant costs ~US$21/month; paid tiers
(sub-project 3) raise limits per tenant via the same columns.

## Testing

- Token bucket: injected clock; refill and exhaustion cases.
- Auth: 401 unknown key, 401 revoked key, 403 suspended tenant, cache TTL
  expiry honours revocation.
- Quota: 429 at rpm limit with Retry-After; 429 at daily clause quota with
  reset message; in-flight cap unchanged.
- Usage: counters increment only on success paths; polling does not count.
- Startup import: idempotent, seeds from env once, no-op when env empty.
- CLI: each subcommand against a test database.
- Existing suite: conftest seeds a test tenant + key in the database
  (replacing the `API_KEYS` env fixture); everything else unchanged.

## Out of scope

- Prometheus-style `/metrics` (UptimeRobot + usage counters + logs
  suffice for a single instance)
- Redis or any shared rate-limit store
- Portal/self-serve endpoints (sub-project 2)
- Stripe billing (sub-project 3)
