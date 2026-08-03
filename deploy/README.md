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

The daily monitor does the same through
`deploy/launchd/monitor-remote.sh` (see `deploy/launchd/README.md`).

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
