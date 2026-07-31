# Developer portal design

Sub-project 2 of the external-tenant commercialisation track. A
self-serve portal where invited developers sign up, accept the terms,
get a tenant with an API key, manage keys, and watch their usage.
Signup is invite-code gated until billing (sub-project 3) ships; removing
the invite check is the only change needed to open it.

Decisions taken during brainstorming: invite-code gated signup; an
independent full-stack portal app; a new repo (`lease-portal`, mirroring
rental_management_app's backend/ + frontend/ layout); the portal reaches
tenant data only through a new admin API on lease-compliance-service —
it never touches the service's tables.

## Architecture

Two repos:

- **lease-compliance-service** gains an `/admin/*` router. Auth is an
  `X-Admin-Key` header compared with `secrets.compare_digest` against a
  new `admin_api_key` setting; when the setting is empty every `/admin/*`
  path returns 404. Admin routes do not use the tenant rate limiter.
  Endpoints wrap the existing `app.tenants` functions - no duplicated
  logic.
- **lease-portal** (new repo): `backend/` FastAPI + `frontend/` Next.js.
  Its own database `lease_portal` (same Postgres instance, own alembic
  history). The frontend builds to static files baked into the backend
  image (multi-stage Dockerfile: node build, then FastAPI serves the
  export via StaticFiles) - one container, no Node on the droplet.
  Caddy adds a `portal.leasekoala.com` site proxying to `portal:8001`;
  same-origin means cookie sessions without CORS.

Portal-to-service calls travel the compose-internal network
(`http://api:8000`); the admin key never crosses the public internet.

## Admin API contract

All requests carry `X-Admin-Key`. Wrong key: 401. Setting unset: 404 for
every `/admin/*` path.

| Endpoint | Request | Response |
|---|---|---|
| `POST /admin/tenants` | `{client_id, name, rpm?, clause_per_day?}` | `201 {client_id, api_key}` (the only time plaintext appears); duplicate client_id 409 |
| `POST /admin/tenants/{client_id}/keys` | - | `201 {api_key}`; unknown tenant 404 |
| `DELETE /admin/keys/{prefix}` | - | 204; unknown prefix 404; ambiguous prefix 409 with tenant names |
| `PATCH /admin/tenants/{client_id}` | `{rpm?, clause_per_day?, status?}` | 200; invalid values 422 (via `_check_limits`); unknown tenant 404 |
| `GET /admin/tenants/{client_id}` | - | `{client_id, name, status, rpm_limit, clause_audits_per_day, keys: [{prefix, status, created_at, last_used_at}], today: {audit, clause_audit, legislation}}` |
| `GET /admin/tenants/{client_id}/usage?days=30` | - | `[{day, endpoint_class, count}]` |

`ValueError` from `app.tenants` maps to 404 (not found), 409 (already
exists / ambiguous prefix), or 422 (limit validation).

## Portal data model

Database `lease_portal`, separate alembic version history.

- `portal_users`: `id` uuid PK, `email` unique, `password_hash` (bcrypt,
  same convention as the rental SaaS backend), `email_verified_at`
  nullable, `verify_token` nullable (single use), `tenant_client_id`
  nullable (filled after provisioning), `created_at`.
- `invite_codes`: `code` text PK (`inv_` + 12 random url-safe chars),
  `created_at`, `used_by` nullable FK to portal_users, `used_at`
  nullable. One code, one use.
- `tos_acceptances`: `user_id` FK, `tos_version` (e.g. `2026-07-30`),
  `accepted_at`. Append-only; a new terms version forces re-acceptance
  at login.

Sessions: httpOnly + Secure + SameSite=Lax cookie holding a signed token
(the SaaS backend's JWT conventions), 7-day expiry.

client_id generation: slug of the email local part plus the first six
hex chars of the user id (e.g. `jane-doe-dev-3f9a1c`). Users never choose
it. The suffix makes the id globally unique across the service's shared
tenant namespace, so crash recovery is unambiguous: a create conflict can
only be our own half-created tenant, never someone else's, and recovery
is simply issuing a new key.

Invite codes are managed by a CLI in the portal backend:
`python -m app.invites new [--count N]` and `list`, run on the server.

## Pages and flows

1. `/signup` - invite code, email, password, ToS checkbox linking to
   `/terms` (submit disabled until checked). Creates the unverified
   user, records the ToS acceptance, marks the invite code used, sends
   the verification email. Invalid or used code: form error.
2. `/verify?token=...` - validates the single-use token, sets
   `email_verified_at`, then calls the admin API to provision the
   tenant, shows the API key once (copy button plus a "you will not see
   this again" warning), continues to the dashboard. If provisioning
   fails the user stays verified but tenantless; the dashboard shows a
   retry button. Retry rule: `tenant_client_id` filled = provisioned; a
   create conflict on our unique id means our own earlier attempt
   half-created it, so issue a new key for it.
3. `/login`, `/logout` - standard; unverified users are prompted to
   re-send the verification email.
4. `/dashboard` - key list (prefix, status, created, last used), new-key
   button (modal shows plaintext once), revoke with confirmation;
   read-only limits display (rpm, clause/day - "contact us to change");
   usage for the last 30 days as a daily table plus a simple bar chart
   drawn without a heavy chart library.
5. `/docs` - quickstart with three curl examples (deterministic audit,
   clause-audit upload, result polling), an error-code table
   (401/403/413/422/429 including Retry-After semantics).
6. `/terms` - full terms text with version.

"General information, not legal advice." appears in the footer of every
page.

## Terms of service

Drafted in English by us, reviewed by you (a lawyer review before public
launch is recommended - flagged as a user-performed step). Contents:

- Nature of service: compliance information tooling; general
  information, not legal advice; no solicitor-client relationship.
- AI data-use disclosure: uploaded documents are sent to the Anthropic
  API for analysis; deleted from our database when processing completes
  (matching the implemented document wipe); per Anthropic's API terms,
  API data is not used for model training; do not upload unnecessary
  personal information.
- Acceptable use; quotas and future paid plans; disclaimer and liability
  cap; termination; governing law (NSW).

## Email

Resend, from `noreply@leasekoala.com` (domain verification in Resend is
a user-performed rollout step). One template: the verification email,
plain text plus a button.

## Deployment

- lease-portal CI: backend pytest + frontend lint and build; on green
  main, publish `ghcr.io/keith-hoka/lease-portal` (`latest` +
  `sha-<short>`).
- The stack compose file (owned by lease-compliance-service's `deploy/`)
  gains a `portal` service: image
  `ghcr.io/keith-hoka/lease-portal:${PORTAL_TAG:-latest}`, env
  `PORTAL_DATABASE_URL` (the `lease_portal` database on the existing db
  container), `ADMIN_API_URL=http://api:8000`, `ADMIN_API_KEY`,
  `RESEND_API_KEY`, `SESSION_SECRET`,
  `PORTAL_BASE_URL=https://portal.leasekoala.com`.
- Caddyfile gains the `portal.leasekoala.com` site block. DNS `A portal
  -> 168.144.169.66` (user-performed, DNS only).
- `ADMIN_API_KEY` is generated at rollout (`openssl rand -hex 24`) and
  written to both the service and portal env.
- The portal repo ships `deploy/deploy-portal.sh` mirroring the service
  deploy script (pull by tag, `up -d portal`, health check); rollback =
  pin a `sha-*` tag.
- Rollout creates the `lease_portal` database once (`createdb`) and runs
  the portal's alembic.

## Testing

- Service: admin API tests - 404 when the key is unset, 401 on a wrong
  key, create returns plaintext once, duplicate 409, invalid limits 422,
  revoke 404/409 mapping, tenant info and usage shapes.
- Portal backend: signup (invalid/used/valid invite, unchecked ToS 422,
  rows written), verification token (single use, invalid after use),
  provisioning (admin API mocked with respx: success fills
  `tenant_client_id`, failure leaves it null, retry is idempotent per
  the retry rule), login/session/unverified gating, key and usage
  endpoints (admin API mocked).
- Frontend: lint and build in CI; a live walkthrough at acceptance, as
  with the rental SaaS.
- No LLM stage, so no eval set.

## Out of scope

- Stripe billing (sub-project 3)
- Open signup (invite-gated until sub-project 3)
- Password reset flow (invite-only population; manual reset via CLI)
- Self-serve limit changes
- Multi-user / organisation accounts
- Portal metrics beyond the existing UptimeRobot pattern
