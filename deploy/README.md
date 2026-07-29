# Server runbook

One droplet (DigitalOcean SYD1) runs the stack from `/opt/lease-compliance`:
`compose.yaml`, `Caddyfile` and `.env` (chmod 600). Images come from
`ghcr.io/keith-hoka/lease-compliance-service`, published by CI on green
main.

## Deploy / roll back

From the repo root on the Mac (`LEASE_DEPLOY_SERVER` and
`LEASE_DEPLOY_DOMAIN` exported, e.g. in `~/.zshrc`):

```bash
./deploy/deploy.sh              # latest
./deploy/deploy.sh sha-abc1234  # pin a version = rollback
```

Migrations run on every deploy (upgrade only). Rolling back a schema
change is a manual decision - restore a backup or write a down migration.

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
