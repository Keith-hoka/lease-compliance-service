# Deployment Design

Move lease-compliance-service off the Mac onto a single DigitalOcean
droplet in Sydney: containerised API + worker + Postgres behind Caddy TLS,
images built by CI, corpus updates written from the Mac over an ssh tunnel
(the headed-Chrome fetch cannot leave the Mac — Cloudflare blocks
headless). The SaaS stays local and just repoints two environment
variables. SaaS hosting is a later milestone.

## Decisions (brainstorm outcomes)

- **Scope: service only.** The SaaS keeps running locally against the
  deployed service.
- **VPS + Docker Compose**, DigitalOcean **SYD1** (Australian customers
  and personal data; Sydney wins on latency and data residency; Hetzner
  rejected for having no AU region). Basic droplet, ~US$6-12/month.
- **Corpus sync: the Mac writes the remote DB directly** through an ssh
  tunnel. The existing ingest/monitor CLIs change only their connection
  string; fetch -> load -> re-audit -> audit-changes feed all happen
  against the production DB. A sleeping Mac delays corpus updates only,
  never the service.
- **CI-built images**: GitHub Actions builds and pushes
  `ghcr.io/keith-hoka/lease-compliance-service` after tests pass on main
  (tags `latest` + `sha-<short>`); deploys are manual, by a script that
  pulls a chosen tag. Rollback = pin the previous `sha-*` tag.
- **Nothing exists yet**: no droplet, no domain — procurement is part of
  the milestone as user-performed steps.

## Topology

One droplet, Ubuntu 24.04 LTS, three Compose services in
`deploy/compose.yaml`:

- `api` — the GHCR image; `uvicorn app.main:app`; the lifespan starts the
  clause worker (key present) and the startup sweep. Configuration from a
  server-side `.env` (chmod 600, never in git): `DATABASE_URL`,
  `API_KEYS` (fresh production tenant keys — dev-key never ships),
  `ANTHROPIC_API_KEY`. `CLAUSE_AUDIT_MODEL` stays unset (sonnet-5
  default).
- `db` — Postgres 16, bound to `127.0.0.1:5432` only (never published to
  the public interface; the Mac reaches it through the tunnel), named
  volume for data.
- `caddy` — reverse proxy `api.<domain>` -> `api:8000`, automatic
  Let's Encrypt TLS, ports 80/443.

New repo files: `Dockerfile` (uv image pattern: `uv sync --frozen`
without the dev group — Playwright/Chrome never enter the image),
`deploy/compose.yaml`, `deploy/Caddyfile`, `deploy/deploy.sh`.

## CI image pipeline

`ci.yml` gains a `publish` job gated on the test job, running only on
pushes to `main`: buildx build + push to
`ghcr.io/keith-hoka/lease-compliance-service` with tags `latest` and
`sha-<short sha>`, authenticated by the workflow's `GITHUB_TOKEN`
(`packages: write`). The server logs in once with a fine-grained PAT
(read-only packages).

## Procurement and initialisation (user-performed checklist)

1. DigitalOcean account; droplet in SYD1, Ubuntu 24.04, Basic
   (1 vCPU/1 GB at ~US$6, or 2 GB at ~US$12), SSH-key login.
2. A domain (~US$10-15/year) with one A record: `api.<domain>` ->
   droplet IP. Caddy self-issues TLS once DNS resolves.
3. Server init (plan provides exact commands): non-root user + ssh key,
   ufw allowing 22/80/443 only, password ssh login disabled, Docker +
   compose plugin, `docker login ghcr.io` with the read-only PAT, `.env`
   in place.
4. First start: `docker compose up -d`, `alembic upgrade head` inside the
   api container, `/health` green.

## Corpus path

- **Tunnel**: `ssh -N -L 15433:127.0.0.1:5432 <server>`; Mac-side
  connection string
  `postgresql+asyncpg://...@localhost:15433/lease_compliance`.
- **Initial load without refetching**: `data/raw/nsw/` already caches
  every version's HTML for both instruments. Running
  `uv run python -m app.ingest nsw` on the Mac with the tunnel connection
  string loads all versions into the fresh production DB in minutes (the
  landing-page fetch still opens Chrome once per instrument for the
  timeline; version bodies hit the cache). Production starts from an
  empty DB — no dev audits carried over.
- **launchd rework**: the `com.lease-monitor` wrapper script becomes
  open tunnel (`ssh -f -o ExitOnForwardFailure=yes`) -> run
  `uv run python -m app.monitor nsw` with the tunnel `DATABASE_URL` ->
  close tunnel. Re-audits and the `audit_changes` feed therefore happen
  in production. The repo's launchd template gains the server parameter.

## Deploy and rollback

`deploy/deploy.sh` runs from the Mac: ssh -> `docker compose pull` ->
`docker compose run --rm api uv run alembic upgrade head` ->
`docker compose up -d` -> curl `/health`. Default tag `latest`;
`./deploy/deploy.sh sha-<short>` pins a version, which is also the
rollback path. Migrations are never auto-downgraded — schema rollback is
a deliberate manual decision.

## Backup, monitoring, security

- **Backup**: nightly `pg_dump -Fc` via cron on the droplet to
  `/var/backups/`, 14 retained; a documented one-line `scp` pulls the
  latest dump to the Mac for an off-site copy; restore procedure in the
  runbook.
- **Monitoring**: UptimeRobot (free) polls `https://api.<domain>/health`
  every 5 minutes and emails on failure; the response already carries
  `pending` and `oldest_pending_seconds` (a climbing age is the
  dead-worker signal). Logs via `docker compose logs -f api` (INFO
  already configured: job lifecycle + judge token usage).
- **Security**: ufw 22/80/443 only; Postgres invisible to the public
  interface; `.env` at 600; fresh production API keys. fail2ban and a
  metrics stack belong to the external-tenant hardening milestone.

## SaaS switchover and acceptance

- The local SaaS changes exactly two env values:
  `COMPLIANCE_API_URL=https://api.<domain>` and
  `COMPLIANCE_API_KEY=<production key>`. No code changes.
- Acceptance against production: `/health` green over valid TLS; a
  deterministic audit via curl (oversized bond -> red); a clause-audit
  job with a real PDF (succeeded, carpet red with quote and citation,
  `document` column NULL afterwards); one monitor run from the Mac
  through the tunnel (corpus sync + feed proven); the local SaaS pointed
  at production runs the button flow end to end.

## Out of scope

SaaS hosting, automatic deploy-on-push, fail2ban, a metrics/alerting
stack, multi-node or high availability, and any application code changes
beyond configuration and packaging.
