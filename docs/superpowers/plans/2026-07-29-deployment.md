# Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** lease-compliance-service running on a DigitalOcean Sydney droplet (API + worker + Postgres behind Caddy TLS), images built by CI, corpus written from the Mac over an ssh tunnel.

**Architecture:** Three Compose services on one droplet (`api` from GHCR, `db` bound to localhost only, `caddy` for TLS). CI publishes `latest` + `sha-*` images after tests; deploys are a manual script from the Mac; the launchd monitor opens a tunnel and writes production directly.

**Tech Stack:** Docker + Compose, Caddy 2, GitHub Actions (buildx + GHCR), DigitalOcean, ssh tunnels, launchd.

**Spec:** `docs/superpowers/specs/2026-07-29-deployment-design.md`

## Global Constraints

- Droplet: DigitalOcean **SYD1**, Ubuntu 24.04 LTS, SSH-key login only.
- Image: `ghcr.io/keith-hoka/lease-compliance-service`, tags `latest` and `sha-<short sha>`; built only after tests pass on `main`.
- The image never contains Playwright/Chrome (`uv sync --frozen --no-dev`).
- Postgres binds `127.0.0.1:5432` on the droplet — never the public interface. Mac reaches it via `ssh -L 15433:127.0.0.1:5432`.
- Server layout: `/opt/lease-compliance/` holding `compose.yaml`, `Caddyfile`, `.env` (chmod 600, never in git).
- Production `API_KEYS` are freshly generated — `dev-key` never ships. `CLAUSE_AUDIT_MODEL` stays unset (sonnet-5 default).
- ufw allows 22/80/443 only; password ssh login disabled.
- Backups: nightly `pg_dump -Fc`, 14 retained, restore procedure documented.
- Migrations are never auto-downgraded; rollback = pin a previous `sha-*` tag.
- Repo tasks end with the usual: full suite -> ruff sequence -> commit -> push -> CI green. Interactive tasks (6-8) have explicit user-performed steps; steps marked **[you]** need the DigitalOcean/registrar/UptimeRobot browser or secrets only you hold — everything else I run from the Mac.

---

### Task 1: Dockerfile and local image smoke

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

**Interfaces:**
- Produces: an image that runs `uvicorn app.main:app` on port 8000, contains alembic for migrations, and has no Playwright. Tasks 2-3 reference it.

- [ ] **Step 1: Write the files**

`Dockerfile`:

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`.dockerignore`:

```
.git
.venv
.env
data
docs
tests
deploy
.github
.superpowers
__pycache__
*.pyc
```

- [ ] **Step 2: Build and smoke against the local Postgres**

```bash
docker build -t lease-compliance:smoke .
docker run -d --rm --name lc-smoke -p 18000:8000 \
  -e DATABASE_URL="postgresql+asyncpg://rental:rental@host.docker.internal:5433/lease_compliance" \
  -e API_KEYS="smoke-key:smoke" \
  lease-compliance:smoke
sleep 3
curl -fsS http://localhost:18000/health
docker exec lc-smoke uv run --no-sync python -c "import importlib.util; print(importlib.util.find_spec('playwright'))"
docker stop lc-smoke
```

Expected: health returns `{"status": "ok", ...}` with the queue block; the playwright probe prints `None` (not in the image). No `ANTHROPIC_API_KEY` -> the worker stays off, which is the correct disabled behavior.

- [ ] **Step 3: Full suite, ruff sequence, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Add the service Dockerfile" && git push origin main
```

---

### Task 2: Compose, Caddyfile, env example

**Files:**
- Create: `deploy/compose.yaml`
- Create: `deploy/Caddyfile`
- Create: `deploy/env.example`

**Interfaces:**
- Produces: the server-side stack definition. `api` image tag is `${API_TAG:-latest}` — Task 4's deploy script sets it. `.env` keys: `DOMAIN`, `POSTGRES_PASSWORD`, `DATABASE_URL`, `API_KEYS`, `ANTHROPIC_API_KEY`.

- [ ] **Step 1: Write the files**

`deploy/compose.yaml`:

```yaml
services:
  api:
    image: ghcr.io/keith-hoka/lease-compliance-service:${API_TAG:-latest}
    env_file: .env
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: lease_compliance
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - dbdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped

  caddy:
    image: caddy:2
    ports:
      - "80:80"
      - "443:443"
    environment:
      DOMAIN: ${DOMAIN}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    restart: unless-stopped

volumes:
  dbdata:
  caddy_data:
  caddy_config:
```

`deploy/Caddyfile`:

```
{$DOMAIN} {
    reverse_proxy api:8000
}
```

`deploy/env.example`:

```
# Copy to /opt/lease-compliance/.env on the server, chmod 600. Never commit.
DOMAIN=api.example.com
POSTGRES_PASSWORD=change-me
# Password must match POSTGRES_PASSWORD; host "db" is the compose service.
DATABASE_URL=postgresql+asyncpg://postgres:change-me@db:5432/lease_compliance
# Fresh production tenant keys, comma-separated key:client_id pairs.
API_KEYS=change-me:rentalapp
ANTHROPIC_API_KEY=sk-ant-change-me
```

- [ ] **Step 2: Validate the compose file**

```bash
cd deploy && cp env.example .env && docker compose config >/dev/null && rm .env && cd ..
echo ok
```

Expected: `ok` (no interpolation or syntax errors). The local `.env` copy exists only for validation and is deleted; `deploy/.env` stays untracked either way (root `.gitignore` covers `.env`).

- [ ] **Step 3: Full suite, ruff sequence, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Add the server compose stack" && git push origin main
```

---

### Task 3: CI publish job

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `ghcr.io/keith-hoka/lease-compliance-service:{latest, sha-<short>}` on every green main push. Tasks 4/7 pull these.

- [ ] **Step 1: Append the publish job**

Add to `.github/workflows/ci.yml` (after the `lint` job, same indentation level):

```yaml
  publish:
    needs: [test, lint]
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/metadata-action@v5
        id: meta
        with:
          images: ghcr.io/keith-hoka/lease-compliance-service
          tags: |
            type=raw,value=latest
            type=sha,prefix=sha-
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
```

- [ ] **Step 2: Commit, push, watch the publish run**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Publish the service image from CI" && git push origin main
gh run watch --exit-status "$(gh run list --branch main --limit 1 --json databaseId --jq '.[0].databaseId')"
```

Expected: test, lint and publish all green.

- [ ] **Step 3: Verify the package exists**

```bash
gh api /user/packages/container/lease-compliance-service/versions --jq '.[0].metadata.container.tags'
```

Expected: a JSON array containing `latest` and one `sha-*` tag.

---

### Task 4: Deploy script and runbook

**Files:**
- Create: `deploy/deploy.sh` (chmod +x)
- Create: `deploy/README.md`

**Interfaces:**
- Consumes: env vars `LEASE_DEPLOY_SERVER` (ssh target, e.g. `deploy@203.0.113.7` or an ssh-config alias) and `LEASE_DEPLOY_DOMAIN` (e.g. `api.example.com`).
- Produces: `./deploy/deploy.sh [tag]` — pull, migrate, up, health-check. Default tag `latest`; a `sha-*` argument pins/rolls back.

- [ ] **Step 1: Write the script**

`deploy/deploy.sh`:

```bash
#!/usr/bin/env bash
# Deploy (or roll back) the service. Usage: deploy.sh [image-tag]
set -euo pipefail

TAG="${1:-latest}"
SERVER="${LEASE_DEPLOY_SERVER:?set LEASE_DEPLOY_SERVER, e.g. deploy@1.2.3.4}"
DOMAIN="${LEASE_DEPLOY_DOMAIN:?set LEASE_DEPLOY_DOMAIN, e.g. api.example.com}"

echo "deploying tag ${TAG} to ${SERVER}"
ssh "$SERVER" "cd /opt/lease-compliance \
  && API_TAG='${TAG}' docker compose pull api \
  && API_TAG='${TAG}' docker compose run --rm api uv run --no-sync alembic upgrade head \
  && API_TAG='${TAG}' docker compose up -d"

sleep 3
curl -fsS "https://${DOMAIN}/health"
echo ""
echo "deployed ${TAG}"
```

`deploy/README.md` — replace the file? No: `deploy/README.md` does not exist yet (`deploy/launchd/README.md` is separate). Create it with the full runbook:

```markdown
# Server runbook

One droplet (DigitalOcean SYD1) runs the stack from /opt/lease-compliance:
compose.yaml, Caddyfile and .env (chmod 600). Images come from
ghcr.io/keith-hoka/lease-compliance-service, published by CI on green main.

## Deploy / roll back

From the repo root on the Mac (LEASE_DEPLOY_SERVER and LEASE_DEPLOY_DOMAIN
exported, e.g. in ~/.zshrc):

    ./deploy/deploy.sh              # latest
    ./deploy/deploy.sh sha-abc1234  # pin a version = rollback

Migrations run on every deploy (upgrade only). Rolling back a schema
change is a manual decision - restore a backup or write a down migration.

## Corpus (runs on the Mac, never the server)

Tunnel + full ingest (initial load hits the data/raw cache):

    ssh -f -N -o ExitOnForwardFailure=yes -L 15433:127.0.0.1:5432 "$LEASE_DEPLOY_SERVER"
    DATABASE_URL="postgresql+asyncpg://postgres:<db password>@localhost:15433/lease_compliance" \
      uv run python -m app.ingest nsw

The daily monitor does the same through deploy/launchd/monitor-remote.sh.

## Backups

Nightly cron on the droplet (installed per the deployment plan Task 8):
pg_dump -Fc into /var/backups/lease-compliance/, 14 kept. Pull an
off-site copy to the Mac:

    scp "$LEASE_DEPLOY_SERVER":/var/backups/lease-compliance/latest.dump .

Restore drill (against a scratch database; the dump streams over stdin -
the db container mounts no host paths):

    ssh "$LEASE_DEPLOY_SERVER" "cd /opt/lease-compliance \
      && docker compose exec -T db createdb -U postgres restore_test \
      && docker compose exec -T db pg_restore -U postgres -d restore_test \
           < /var/backups/lease-compliance/latest.dump \
      && docker compose exec -T db dropdb -U postgres restore_test"

## Logs and health

    ssh "$LEASE_DEPLOY_SERVER" "cd /opt/lease-compliance && docker compose logs -f api"
    curl -s https://<domain>/health   # pending count + oldest_pending_seconds

UptimeRobot polls /health every 5 minutes and emails on failure.
```

- [ ] **Step 2: Syntax-check and mark executable**

```bash
bash -n deploy/deploy.sh && chmod +x deploy/deploy.sh && echo ok
```

Expected: `ok`.

- [ ] **Step 3: Full suite, ruff sequence, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Add the deploy script and server runbook" && git push origin main
```

---

### Task 5: launchd tunnel wrapper

**Files:**
- Create: `deploy/launchd/monitor-remote.sh`
- Modify: `deploy/launchd/com.lease-monitor.plist`
- Modify: `deploy/launchd/README.md`

**Interfaces:**
- Consumes: plist env `LEASE_DB_SERVER` (ssh target) and `DATABASE_URL` (tunnel form, port 15433).
- Produces: the daily monitor run against production.

- [ ] **Step 1: Write the wrapper**

`deploy/launchd/monitor-remote.sh`:

```bash
#!/usr/bin/env bash
# Open a tunnel to the production DB, run the monitor, close the tunnel.
set -euo pipefail

SERVER="${LEASE_DB_SERVER:?set LEASE_DB_SERVER in the plist}"
: "${DATABASE_URL:?set DATABASE_URL (tunnel form, port 15433) in the plist}"
SOCK="/tmp/lease-monitor-tunnel.sock"

ssh -M -S "$SOCK" -f -N -o ExitOnForwardFailure=yes \
    -L 15433:127.0.0.1:5432 "$SERVER"
trap 'ssh -S "$SOCK" -O exit "$SERVER" 2>/dev/null || true' EXIT

uv run python -m app.monitor nsw
```

- [ ] **Step 2: Update the plist template**

In `deploy/launchd/com.lease-monitor.plist`, replace the `ProgramArguments` array and extend `EnvironmentVariables`:

```xml
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>__REPO_DIR__/deploy/launchd/monitor-remote.sh</string>
    </array>
```

and inside the existing `EnvironmentVariables` dict, after the `PATH` entry:

```xml
        <key>LEASE_DB_SERVER</key>
        <string>__SERVER__</string>
        <key>DATABASE_URL</key>
        <string>__REMOTE_DATABASE_URL__</string>
```

- [ ] **Step 3: Update the launchd README**

In `deploy/launchd/README.md`, replace the install block with:

```bash
sed -e "s|__REPO_DIR__|$(pwd)|g" \
    -e "s|__HOME__|$HOME|g" \
    -e "s|__SERVER__|deploy@YOUR.SERVER.IP|g" \
    -e "s|__REMOTE_DATABASE_URL__|postgresql+asyncpg://postgres:YOUR-DB-PASSWORD@localhost:15433/lease_compliance|g" \
    deploy/launchd/com.lease-monitor.plist \
    > ~/Library/LaunchAgents/com.lease-monitor.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lease-monitor.plist
```

and add one sentence below it: "The monitor now writes the production
database through an ssh tunnel; the fetch still needs your GUI session
for headed Chrome." Remove the now-unused `__UV__` sed line (the wrapper
finds `uv` via `PATH`).

- [ ] **Step 4: Syntax-check, full suite, ruff, commit, push, CI**

```bash
bash -n deploy/launchd/monitor-remote.sh && chmod +x deploy/launchd/monitor-remote.sh
plutil -lint deploy/launchd/com.lease-monitor.plist
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A && git commit -m "Point the launchd monitor at production through a tunnel" && git push origin main
```

(The wrapper's live run happens in Task 7, after the server exists.)

---

### Task 6: Procurement and server initialisation (interactive)

No repo changes. Steps marked **[you]** need your browser/accounts; the rest I run from the Mac once ssh works.

- [ ] **Step 1 [you]: Droplet** — DigitalOcean -> Create Droplet -> Region **Sydney (SYD1)** -> Ubuntu 24.04 LTS -> Basic / Regular (1 GB ~US$6 or 2 GB ~US$12) -> Authentication: SSH key. If you have no key yet, I generate one first (`ssh-keygen -t ed25519 -f ~/.ssh/lease_deploy`) and hand you the `.pub` to paste. Note the droplet IP.

- [ ] **Step 2 [you]: Domain** — register one (Cloudflare Registrar / Namecheap, ~US$10-15/yr) and add one DNS record: `A  api  <droplet IP>` (proxy/CDN off for Caddy's TLS issuance; TTL 300).

- [ ] **Step 3 [you]: GHCR PAT** — GitHub -> Settings -> Developer settings -> Fine-grained tokens: name `lease-server-pull`, expiry 1 year, Repository access: `lease-compliance-service`, Permissions: read-only Packages. Hand me the token (it lands only in the server's docker credential store).

- [ ] **Step 4: Server init (I run, you watch)** — as root@IP first:
  create user `deploy` with the ssh key and docker+sudo groups; disable password ssh (`/etc/ssh/sshd_config.d/50-hardening.conf`: `PasswordAuthentication no`, `PermitRootLogin prohibit-password`; restart sshd); `ufw allow 22,80,443/tcp && ufw --force enable`; install Docker via `curl -fsSL https://get.docker.com | sh`; `mkdir -p /opt/lease-compliance /var/backups/lease-compliance` owned by `deploy`.

- [ ] **Step 5: Stack files and secrets** — `scp deploy/compose.yaml deploy/Caddyfile` to `/opt/lease-compliance/`; create `.env` there (chmod 600) with: your domain; `POSTGRES_PASSWORD=$(openssl rand -hex 24)`; matching `DATABASE_URL`; `API_KEYS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))'):rentalapp"`; **[you]** paste `ANTHROPIC_API_KEY`. Then `docker login ghcr.io` on the server with the PAT.

Checkpoint: `ssh deploy@<IP> docker ps` works; DNS resolves `api.<domain>` to the IP (`dig +short`).

---

### Task 7: First deploy and initial corpus (interactive)

- [ ] **Step 1: First deploy**

```bash
export LEASE_DEPLOY_SERVER=deploy@<IP> LEASE_DEPLOY_DOMAIN=api.<domain>
./deploy/deploy.sh
```

Expected: pull, `Running upgrade ... -> a1c47e92b5d3` (chain applies from empty), containers up, and the final curl prints the health JSON over valid TLS (Caddy needs DNS already resolving; first TLS issuance can take ~30 s — retry the curl once if needed).

- [ ] **Step 2: Initial corpus from the Mac cache**

```bash
ssh -f -N -o ExitOnForwardFailure=yes -L 15433:127.0.0.1:5432 "$LEASE_DEPLOY_SERVER"
DATABASE_URL="postgresql+asyncpg://postgres:<db password>@localhost:15433/lease_compliance" \
  uv run python -m app.ingest nsw
```

Expected: Chrome opens twice (landing timelines); every version line loads from `data/raw` cache; both instruments complete in minutes. Verify remotely:

```bash
curl -s "https://api.<domain>/v1/legislation/sections?act=act-2010-042&section_no=19&as_at=2026-07-29" \
  -H "X-API-Key: <production key>" | head -c 300
```

Expected: s 19 "Prohibited terms" JSON.

- [ ] **Step 3: Install the reworked launchd job and fire it once**

Run the README's new sed+bootstrap block with the real server and DB password, then:

```bash
launchctl kickstart gui/$(id -u)/com.lease-monitor
tail -20 ~/Library/Logs/lease-monitor.log
```

Expected: `corpus: ... no new versions` for both instruments and `monitor: checked=0 changed=0` (no monitored production leases yet).

---

### Task 8: Backups, monitoring, acceptance, SaaS switchover (interactive)

- [ ] **Step 1: Backup cron on the droplet**

Install as `deploy` via `crontab -e` (I do it over ssh):

```
15 16 * * * cd /opt/lease-compliance && docker compose exec -T db pg_dump -U postgres -Fc lease_compliance > /var/backups/lease-compliance/$(date +\%F).dump && cp /var/backups/lease-compliance/$(date +\%F).dump /var/backups/lease-compliance/latest.dump && find /var/backups/lease-compliance -name '20*.dump' -mtime +14 -delete
```

(16:15 UTC = 02:15 Sydney.) Run the command body once by hand; verify the dump exists and the runbook's restore drill passes.

- [ ] **Step 2 [you]: UptimeRobot** — free account, HTTP(s) monitor on `https://api.<domain>/health`, 5-minute interval, email alerts on.

- [ ] **Step 3: Acceptance against production**

```bash
# deterministic audit: oversized bond -> red
curl -s -X POST https://api.<domain>/v1/audits -H "X-API-Key: <key>" \
  -H "Content-Type: application/json" \
  -d '{"jurisdiction":"NSW","lease":{"rent_amount":"600","rent_frequency":"weekly","start_date":"2026-01-01","bond_amount":"3000"}}'
# clause audit: real PDF -> succeeded, carpet red, document wiped
# (reuse the acceptance PDF; poll GET until terminal, then check the
#  document column is NULL via the tunnel)
```

Expected: bond rule red; clause job succeeded with the carpet red + s 19 citation; `select document from clause_audit_jobs` over the tunnel returns NULL.

- [ ] **Step 4: SaaS switchover (local)**

Start the local SaaS backend with `COMPLIANCE_API_URL=https://api.<domain>` and `COMPLIANCE_API_KEY=<production key>`; press "Check now" and "Run clause audit" on a lease and watch both complete against production.

- [ ] **Step 5: Record completion**

Append the deployment record (domain, droplet, image tag deployed) to `.superpowers/sdd/progress.md` and commit any README touch-ups from the run.

---

## Acceptance summary

The milestone is done when: `https://api.<domain>/health` is green over
valid TLS and UptimeRobot watches it; CI publishes images and
`deploy/deploy.sh` ships or rolls back by tag; the corpus lookup answers
from production; the launchd monitor writes production through the
tunnel; nightly dumps exist with a proven restore; and the local SaaS
completes both audit flows against production.
