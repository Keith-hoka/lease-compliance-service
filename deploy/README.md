# Server runbook

One droplet (DigitalOcean SYD1) runs the stack from `/opt/lease-compliance`:
`compose.yaml`, `Caddyfile`, `.env` and `.env.portal` (all chmod 600).
Images come from `ghcr.io/keith-hoka/lease-compliance-service` and
`ghcr.io/keith-hoka/lease-portal`, published by CI on green main.

## Deploy / roll back

From the repo root on the Mac (`LEASE_DEPLOY_SERVER` and
`LEASE_DEPLOY_DOMAIN` exported, e.g. in `~/.zshrc`):

```bash
./deploy/deploy.sh              # latest
./deploy/deploy.sh sha-abc1234  # pin a version = rollback
```

Migrations run on every deploy (upgrade only). Rolling back a schema
change is a manual decision - restore a backup or write a down migration.

After editing the server `.env`, apply it by re-running `deploy.sh <tag>` -
never a bare `docker compose up` on the droplet: `compose.yaml` pins the
image as `${API_TAG:-latest}` and only deploy.sh exports `API_TAG`, so a
bare recreate silently falls back to the stale `latest` image (observed
2026-08-13).

## LLM provider failover

The judge runs on `CLAUSE_AUDIT_MODEL` (default `claude-sonnet-5`) with an
automatic circuit-breaker failover to `CLAUSE_AUDIT_FAILOVER_MODEL` when
the primary provider is down (3 consecutive infrastructure failures ->
backup; probe and self-recover after 300 s). Production `.env` carries:

- `OPENAI_API_KEY` - backup provider credential
- `CLAUSE_AUDIT_FAILOVER_MODEL=openai:gpt-5.6-terra` - eval-gated backup
  (docs/model-evals.md records the gate); empty disables failover

`GET /health` exposes `llm_failover: {"state", "active_model"}` -
`state=closed` with the Anthropic model is the normal reading; `open`
means traffic is on the backup (WARNING logged on every transition).
Jobs record the model that actually judged them.

Backup smoke (proves the OpenAI path end-to-end in production without
waiting for an outage): append `CLAUSE_AUDIT_MODEL=openai:gpt-5.6-terra`
to the server `.env`, `deploy.sh <current tag>`, submit one real audit and
verify it succeeds with the openai model recorded, then delete the line
and `deploy.sh <current tag>` again (performed 2026-08-13, 68 findings,
correct reds).

## Tenants

All tenant administration runs on the droplet:

```bash
ssh "$LEASE_DEPLOY_SERVER" "cd /opt/lease-compliance \
  && docker compose exec api uv run --no-sync python -m app.tenants list"
```

(`--no-sync` matters: without it uv installs dev dependencies into the
container.)

Commands: `create <client_id> --name NAME [--rpm N] [--clause-per-day N]`,
`new-key <client_id>`, `revoke-key <prefix>`, `suspend`/`activate
<client_id>`, `set-limits <client_id> --rpm N --clause-per-day N`,
`usage <client_id> --days 30`, `import-env-keys`. `create` and `new-key`
print the plaintext key once; it is never stored or shown again.

## Portal

The developer portal (self-service signup, API keys, usage) runs as a
second image alongside the API, deployed from the `lease-portal` repo.
One-time setup on the droplet, before the first portal deploy: create its
database on the shared `db` service.

```bash
ssh "$LEASE_DEPLOY_SERVER" "cd /opt/lease-compliance \
  && docker compose exec db createdb -U postgres lease_portal"
```

Deploy / roll back (from the `lease-portal` repo root on the Mac;
`LEASE_DEPLOY_SERVER` and `PORTAL_DEPLOY_DOMAIN` exported, e.g. in
`~/.zshrc`):

```bash
./deploy/deploy-portal.sh              # latest
./deploy/deploy-portal.sh sha-abc1234  # pin a version = rollback
```

Config lives in `.env.portal` next to `.env` on the droplet (chmod 600,
never commit) - see `env.example` for the full variable list.
`ADMIN_API_KEY` must match the value in the service `.env`.
`PORTAL_DOMAIN` must be set in the service `.env` before any `deploy.sh`
run once the portal service exists in the stack.

Signup is invite-gated; issue a code from the droplet:

```bash
ssh "$LEASE_DEPLOY_SERVER" "cd /opt/lease-compliance \
  && docker compose exec portal uv run --no-sync python -m app.invites new"
```

## Corpus (runs on the Mac, never the server)

Tunnel + full ingest (the initial load hits the `data/raw` cache):

```bash
ssh -f -N -o ExitOnForwardFailure=yes -L 15433:127.0.0.1:5432 "$LEASE_DEPLOY_SERVER"
DATABASE_URL="postgresql+asyncpg://postgres:<db password>@localhost:15433/lease_compliance" \
  uv run python -m app.ingest nsw
```

Port ownership: manual tunnels use 15433; the daily launchd monitor
owns 15434 exclusively (split 2026-08-11 - before the split they shared
15433 and a held manual tunnel silently killed the daily run, which is
why the historical incident note below existed). A manual `ssh -f`
tunnel survives closing the terminal; still close it when done:

```bash
pkill -f "15433:127.0.0.1:5432"
```

The daily monitor does the same through
`deploy/launchd/monitor-remote.sh` (see `deploy/launchd/README.md`).

## Rent statistics (runs on the Mac, never the server)

`GET /v1/rent-statistics` serves official bond-derived rent data:

- **NSW** - Fair Trading rental bond lodgements (per-lodgement detail;
  monthly files by URL pattern from 2026-01, annual 2021-2025 files
  pinned in `app/rent_stats/fetcher.py` because their paths are
  irregular). Aggregated per postcode/dwelling/bedrooms with
  `percentile_cont` into `rent_statistics`. Invariant: the pinned annual
  years and the monthly coverage never overlap - `nsw_annual_targets()`
  drops any year from `NSW_MONTHLY_SINCE.year` onward, so an annual file
  can never double-count months the monthly loader also covers. The
  source page states no licence text - confirm the terms of use before
  external tenants rely on this endpoint.
- **VIC** - Homes Victoria Rental Report, moving annual median rents by
  suburb (CC BY 4.0). One workbook carries the whole series; the fetcher
  probes newest-completed quarter backwards until a published one is
  found. Any UI built on this data must surface the CC BY 4.0
  attribution.

Dwelling-type/bedrooms coverage differs by jurisdiction. NSW serves
every `(dwelling_type, bedrooms)` combination present in the lodgements,
plus the `(dwelling_type, NULL)` rollup per type and the `('all', NULL)`
rollup across all types. VIC serves only the cells the published
workbook carries: `unit` at 1/2/3 bedrooms, `house` at 2/3/4 bedrooms,
and `all` at `NULL` bedrooms - there is no VIC `(type, NULL)` rollup, so
a query for a specific VIC dwelling type with `bedrooms` omitted (which
filters on `bedrooms IS NULL`) returns an empty series by design, not an
error.

Wire format: `median`/`p25`/`p75` serialise as JSON strings with two
decimals (e.g. `"760.00"`) - the `Numeric(10, 2)` column scale, no
custom serializer involved.

Same tunnel as the corpus (port 15433):

```bash
DATABASE_URL="postgresql+asyncpg://postgres:<db password>@localhost:15433/lease_compliance" \
  uv run python -m app.rent_stats backfill   # all history, idempotent by file hash
DATABASE_URL=... uv run python -m app.rent_stats update  # last 3 NSW months + current VIC quarter
```

The daily monitor runs `update` after the legislation monitors (port
15434). Initial production backfill 2026-08-16: NSW 13 files, 1,776,225
lodgements, 67 months 2021-01..2026-07 -> 406,109 statistics rows; VIC
101,399 rows 2000-Q1..2025-Q3. Calls are recorded under the usage class
`rent_statistics` (counted, no daily quota).

## Backups

Nightly cron on the droplet (installed per the deployment plan Task 8):
`pg_dump -Fc` into `/var/backups/lease-compliance/`, 14 kept. Pull an
off-site copy to the Mac:

```bash
scp "$LEASE_DEPLOY_SERVER":/var/backups/lease-compliance/latest.dump .
```

Restore drill (against a scratch database; the dump streams over stdin -
the db container mounts no host paths):

```bash
ssh "$LEASE_DEPLOY_SERVER" "cd /opt/lease-compliance \
  && docker compose exec -T db createdb -U postgres restore_test \
  && docker compose exec -T db pg_restore -U postgres -d restore_test \
       < /var/backups/lease-compliance/latest.dump \
  && docker compose exec -T db dropdb -U postgres restore_test"
```

## Logs and health

```bash
ssh "$LEASE_DEPLOY_SERVER" "cd /opt/lease-compliance && docker compose logs -f api"
curl -s https://<domain>/health   # pending count + oldest_pending_seconds
```

UptimeRobot polls `/health` every 5 minutes and emails on failure. A
climbing `oldest_pending_seconds` is the dead-worker signal.
