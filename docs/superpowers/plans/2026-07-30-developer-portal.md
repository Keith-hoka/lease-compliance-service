# Developer Portal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An invite-gated developer portal (signup, email verification, tenant provisioning, key management, usage dashboard, docs, ToS) as a new `lease-portal` app, talking to lease-compliance-service through a new `/admin/*` API.

**Architecture:** Service gains six thin `/admin/*` endpoints (X-Admin-Key auth) wrapping `app.tenants`. New repo `lease-portal`: FastAPI backend (own `lease_portal` database, own alembic) + Next.js frontend statically exported and baked into the backend image; one extra container on the droplet behind Caddy at `portal.leasekoala.com`, calling the service over the compose network.

**Tech Stack:** FastAPI, async SQLAlchemy 2.0, Alembic, pwdlib, PyJWT, httpx, respx (tests), Next.js 16 + React 19 + Tailwind 4, Docker multi-stage, GHCR.

**Spec:** `docs/superpowers/specs/2026-07-30-developer-portal-design.md`

## Global Constraints

- Python 3.12+, `uv` only. TDD: failing test first, watch it fail, implement.
- Both repos end every task: full suite -> ruff sequence (`uv run ruff format .` -> `uv run ruff check --fix .` -> `uv run ruff check .` -> `uv run ruff format --check .`; frontend tasks use `npm run lint` and `npm run build`) -> commit -> push -> CI green.
- No emojis anywhere. Docstrings over comments. Short modules.
- Admin auth: header `X-Admin-Key`, `secrets.compare_digest` against `settings.admin_api_key`; when the setting is empty every `/admin/*` path returns 404.
- Error mapping (admin API): wrong key 401; unknown tenant/prefix 404; duplicate client_id or ambiguous prefix 409; invalid limits 422.
- Invite codes: `inv_` + 12 url-safe chars, single use.
- Verify tokens: `secrets.token_urlsafe(32)`, single use, rotated on re-send.
- Session: cookie `portal_session`, JWT HS256 (`session_secret`), 7-day expiry, httpOnly + Secure + SameSite=Lax.
- `TOS_VERSION = "2026-07-30"`.
- Portal backend port 8001. Image `ghcr.io/keith-hoka/lease-portal`, tags `latest` + `sha-<short>`.
- Every portal page footer: "General information, not legal advice."
- The plaintext API key is never stored or logged by the portal; it passes through to the browser once.

---

### Task 1: Service admin API - auth gate, create tenant, keys

**Repo:** lease-compliance-service

**Files:**
- Create: `app/routers/admin.py`
- Modify: `app/core/config.py` (add `admin_api_key: str = ""`)
- Modify: `app/main.py` (include router)
- Test: `tests/test_admin_api.py`

**Interfaces:**
- Consumes: `app.tenants.create_tenant`, `new_key`, `revoke_key`.
- Produces: `require_admin` dependency; `POST /admin/tenants`, `POST /admin/tenants/{client_id}/keys`, `DELETE /admin/keys/{prefix}`. Task 2 adds to the same router.

- [ ] **Step 1: Write the failing tests**

`tests/test_admin_api.py`:

```python
import pytest

from app.core.config import settings

ADMIN = {"X-Admin-Key": "test-admin-key"}


@pytest.fixture(autouse=True)
def admin_key(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "test-admin-key")


async def test_admin_404_when_key_unset(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "")
    response = await client.post("/admin/tenants", json={"client_id": "a"}, headers=ADMIN)
    assert response.status_code == 404


async def test_admin_401_on_wrong_key(client):
    response = await client.post(
        "/admin/tenants", json={"client_id": "a"}, headers={"X-Admin-Key": "wrong"}
    )
    assert response.status_code == 401


async def test_create_tenant_returns_key_once(client):
    response = await client.post(
        "/admin/tenants", json={"client_id": "acme", "name": "Acme"}, headers=ADMIN
    )
    assert response.status_code == 201
    body = response.json()
    assert body["client_id"] == "acme"
    assert body["api_key"].startswith("lk_")


async def test_duplicate_client_id_is_409(client):
    await client.post("/admin/tenants", json={"client_id": "acme"}, headers=ADMIN)
    response = await client.post("/admin/tenants", json={"client_id": "acme"}, headers=ADMIN)
    assert response.status_code == 409


async def test_new_key_and_revoke(client):
    await client.post("/admin/tenants", json={"client_id": "acme"}, headers=ADMIN)
    created = await client.post("/admin/tenants/acme/keys", headers=ADMIN)
    assert created.status_code == 201
    key = created.json()["api_key"]

    revoked = await client.delete(f"/admin/keys/{key[:8]}", headers=ADMIN)
    assert revoked.status_code == 204

    missing = await client.delete("/admin/keys/lk_nope0", headers=ADMIN)
    assert missing.status_code == 404


async def test_new_key_unknown_tenant_is_404(client):
    response = await client.post("/admin/tenants/ghost/keys", headers=ADMIN)
    assert response.status_code == 404
```

- [ ] **Step 2: Run to watch them fail**

Run: `uv run pytest tests/test_admin_api.py -v`
Expected: FAIL - every request 404 (router does not exist yet, and FastAPI returns 404 for unknown paths), with the 401 test failing on 404 != 401.

- [ ] **Step 3: Implement the router**

Add to `app/core/config.py` inside `Settings`:

```python
    admin_api_key: str = ""
```

`app/routers/admin.py`:

```python
"""Admin API for the developer portal. Shared-secret auth, no tenant rate limit."""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.db import get_session
from app.tenants import create_tenant, new_key, revoke_key

router = APIRouter(prefix="/admin")


def require_admin(x_admin_key: str = Header(default="")) -> None:
    if not settings.admin_api_key:
        raise HTTPException(status_code=404, detail="Not Found")
    if not secrets.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=401, detail="Invalid admin key")


AdminDep = Depends(require_admin)
SessionDep = Depends(get_session)


class TenantCreate(BaseModel):
    client_id: str
    name: str = ""
    rpm: int = 60
    clause_per_day: int = 10


@router.post("/tenants", status_code=201, dependencies=[AdminDep])
async def admin_create_tenant(body: TenantCreate, session=SessionDep) -> dict:
    try:
        key = await create_tenant(session, body.client_id, body.name, body.rpm, body.clause_per_day)
    except ValueError as exc:
        status = 409 if "already exists" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"client_id": body.client_id, "api_key": key}


@router.post("/tenants/{client_id}/keys", status_code=201, dependencies=[AdminDep])
async def admin_new_key(client_id: str, session=SessionDep) -> dict:
    try:
        key = await new_key(session, client_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"api_key": key}


@router.delete("/keys/{prefix}", status_code=204, dependencies=[AdminDep])
async def admin_revoke_key(prefix: str, session=SessionDep) -> None:
    try:
        await revoke_key(session, prefix)
    except ValueError as exc:
        status = 409 if "multiple" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc
```

In `app/main.py`: `from app.routers.admin import router as admin_router` and `app.include_router(admin_router)` beside the other routers.

- [ ] **Step 4: Run the tests, then the full suite**

```bash
uv run pytest tests/test_admin_api.py -v
uv run pytest
```

Expected: 7 passed in the file; full suite 193 passed, 5 deselected.

- [ ] **Step 5: Ruff, commit, push, CI**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/routers/admin.py app/core/config.py app/main.py tests/test_admin_api.py
git commit -m "Add admin API: auth gate, tenant create, key issue and revoke"
git push origin main
```

---

### Task 2: Service admin API - limits patch, tenant info, usage

**Repo:** lease-compliance-service

**Files:**
- Modify: `app/routers/admin.py`
- Modify: `app/tenants/__init__.py` (add `tenant_info`, `usage_rows`)
- Test: `tests/test_admin_api.py` (append)

**Interfaces:**
- Consumes: `set_limits`, `set_status`, models.
- Produces: `PATCH /admin/tenants/{client_id}`, `GET /admin/tenants/{client_id}`, `GET /admin/tenants/{client_id}/usage`; `app.tenants.tenant_info(session, client_id) -> dict`, `usage_rows(session, client_id, days) -> list[dict]`. Tasks 6-7 (portal) consume these shapes verbatim.

- [ ] **Step 1: Append failing tests**

Append to `tests/test_admin_api.py`:

```python
async def test_patch_limits_and_status(client):
    await client.post("/admin/tenants", json={"client_id": "acme"}, headers=ADMIN)
    response = await client.patch(
        "/admin/tenants/acme",
        json={"rpm": 120, "clause_per_day": 50, "status": "suspended"},
        headers=ADMIN,
    )
    assert response.status_code == 200

    info = (await client.get("/admin/tenants/acme", headers=ADMIN)).json()
    assert info["rpm_limit"] == 120
    assert info["clause_audits_per_day"] == 50
    assert info["status"] == "suspended"


async def test_patch_invalid_rpm_is_422(client):
    await client.post("/admin/tenants", json={"client_id": "acme"}, headers=ADMIN)
    response = await client.patch("/admin/tenants/acme", json={"rpm": 0}, headers=ADMIN)
    assert response.status_code == 422


async def test_tenant_info_includes_keys_and_today(client):
    created = await client.post(
        "/admin/tenants", json={"client_id": "acme", "name": "Acme"}, headers=ADMIN
    )
    prefix = created.json()["api_key"][:8]
    info = (await client.get("/admin/tenants/acme", headers=ADMIN)).json()
    assert info["name"] == "Acme"
    assert info["keys"][0]["prefix"] == prefix
    assert info["keys"][0]["status"] == "active"
    assert info["today"] == {"audit": 0, "clause_audit": 0, "legislation": 0}


async def test_tenant_info_unknown_is_404(client):
    response = await client.get("/admin/tenants/ghost", headers=ADMIN)
    assert response.status_code == 404


async def test_usage_rows(client, db_session):
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.models import Tenant, UsageCounter

    await client.post("/admin/tenants", json={"client_id": "acme"}, headers=ADMIN)
    tenant = (
        await db_session.execute(select(Tenant).where(Tenant.client_id == "acme"))
    ).scalar_one()
    db_session.add(
        UsageCounter(
            tenant_id=tenant.id,
            day=datetime.now(UTC).date(),
            endpoint_class="audit",
            count=4,
        )
    )
    await db_session.commit()

    rows = (await client.get("/admin/tenants/acme/usage?days=7", headers=ADMIN)).json()
    assert rows == [
        {"day": datetime.now(UTC).date().isoformat(), "endpoint_class": "audit", "count": 4}
    ]
```

- [ ] **Step 2: Watch them fail** - `uv run pytest tests/test_admin_api.py -v`; the new tests 404/405.

- [ ] **Step 3: Implement**

Append to `app/tenants/__init__.py`:

```python
async def tenant_info(session: AsyncSession, client_id: str) -> dict:
    """Tenant row, its keys, and today's usage as a JSON-ready dict."""
    tenant = await _tenant_by_client_id(session, client_id)
    keys = (
        (await session.execute(select(ApiKey).where(ApiKey.tenant_id == tenant.id))).scalars().all()
    )
    today = datetime.now(UTC).date()
    counters = (
        (
            await session.execute(
                select(UsageCounter).where(
                    UsageCounter.tenant_id == tenant.id, UsageCounter.day == today
                )
            )
        )
        .scalars()
        .all()
    )
    by_class = {c.endpoint_class: c.count for c in counters}
    return {
        "client_id": tenant.client_id,
        "name": tenant.name,
        "status": tenant.status,
        "rpm_limit": tenant.rpm_limit,
        "clause_audits_per_day": tenant.clause_audits_per_day,
        "keys": [
            {
                "prefix": k.prefix,
                "status": k.status,
                "created_at": k.created_at.isoformat(),
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            }
            for k in keys
        ],
        "today": {
            "audit": by_class.get("audit", 0),
            "clause_audit": by_class.get("clause_audit", 0),
            "legislation": by_class.get("legislation", 0),
        },
    }


async def usage_rows(session: AsyncSession, client_id: str, days: int) -> list[dict]:
    """Daily usage counters for the last N days as JSON-ready dicts."""
    tenant = await _tenant_by_client_id(session, client_id)
    since = datetime.now(UTC).date() - timedelta(days=days)
    rows = (
        (
            await session.execute(
                select(UsageCounter)
                .where(UsageCounter.tenant_id == tenant.id, UsageCounter.day >= since)
                .order_by(UsageCounter.day, UsageCounter.endpoint_class)
            )
        )
        .scalars()
        .all()
    )
    return [
        {"day": r.day.isoformat(), "endpoint_class": r.endpoint_class, "count": r.count}
        for r in rows
    ]
```

Append to `app/routers/admin.py`:

```python
from app.tenants import set_limits, set_status, tenant_info, usage_rows


class TenantPatch(BaseModel):
    rpm: int | None = None
    clause_per_day: int | None = None
    status: str | None = None


@router.patch("/tenants/{client_id}", dependencies=[AdminDep])
async def admin_patch_tenant(client_id: str, body: TenantPatch, session=SessionDep) -> dict:
    try:
        if body.rpm is not None or body.clause_per_day is not None:
            await set_limits(session, client_id, body.rpm, body.clause_per_day)
        if body.status is not None:
            await set_status(session, client_id, body.status)
    except ValueError as exc:
        status = 404 if "not found" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"client_id": client_id}


@router.get("/tenants/{client_id}", dependencies=[AdminDep])
async def admin_tenant_info(client_id: str, session=SessionDep) -> dict:
    try:
        return await tenant_info(session, client_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tenants/{client_id}/usage", dependencies=[AdminDep])
async def admin_usage(client_id: str, days: int = 30, session=SessionDep) -> list[dict]:
    try:
        return await usage_rows(session, client_id, days)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
```

Note: `set_limits` validation raises before the tenant lookup, so a patch
with `rpm=0` on an unknown tenant returns 422; acceptable.

- [ ] **Step 4: Full suite + ruff + commit**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/routers/admin.py app/tenants/__init__.py tests/test_admin_api.py
git commit -m "Add admin API: tenant patch, info and usage"
git push origin main
```

Expected: 198 passed, 5 deselected; CI green. The service side is complete and deployable.

---

### Task 3: Portal repo scaffold - backend core, models, migration, CI

**Repo:** lease-portal (new)

**Files (all under `/Users/keithho/LLMProjects/lease-portal`):**
- Create: repo via `gh repo create Keith-hoka/lease-portal --private --clone` (run in `~/LLMProjects`)
- Create: `backend/pyproject.toml`, `backend/.python-version` (3.12)
- Create: `backend/app/core/config.py`, `backend/app/core/db.py`, `backend/app/core/security.py`
- Create: `backend/app/models/__init__.py`, `backend/app/models/portal.py`
- Create: `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/c7a1d94e02b5_portal_tables.py`
- Create: `backend/app/main.py` (health only for now)
- Create: `backend/tests/conftest.py`, `backend/tests/test_models.py`
- Create: `.github/workflows/ci.yml` (backend test + lint jobs)
- Create: `.gitignore`

**Interfaces:**
- Produces: `PortalUser`, `InviteCode`, `TosAcceptance` models; `Base`, `get_session`, `async_session_factory`; `settings` with `database_url`, `test_database_url`, `session_secret`, `admin_api_url`, `admin_api_key`, `resend_api_key`, `email_from`, `portal_base_url`; `hash_password`/`verify_password`/`create_session_token`/`decode_session_token` in `app.core.security`. Everything later tasks import.

- [ ] **Step 1: Create the repo and backend skeleton**

```bash
cd ~/LLMProjects
gh repo create Keith-hoka/lease-portal --private --clone
cd lease-portal && mkdir -p backend/app/core backend/app/models backend/tests
cd backend && uv init --no-readme --python 3.12 && rm -f main.py
uv add fastapi "uvicorn[standard]" "sqlalchemy[asyncio]" asyncpg alembic pydantic-settings "pwdlib[argon2]" pyjwt httpx
uv add --dev pytest pytest-asyncio ruff respx
```

`.gitignore` at repo root: `.venv/`, `__pycache__/`, `.env`, `node_modules/`, `.next/`, `out/`.

`backend/pyproject.toml` gains (matching the service's conventions):

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "DTZ"]
```

- [ ] **Step 2: Core modules**

`backend/app/core/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

TOS_VERSION = "2026-07-30"


class Settings(BaseSettings):
    """Portal configuration, overridable via environment or .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://rental:rental@localhost:5433/lease_portal"
    test_database_url: str = "postgresql+asyncpg://rental:rental@localhost:5433/lease_portal_test"
    session_secret: str = "dev-secret-change-in-production"
    admin_api_url: str = "http://localhost:8000"
    admin_api_key: str = ""
    resend_api_key: str = ""
    email_from: str = "noreply@leasekoala.com"
    portal_base_url: str = "http://localhost:8001"


settings = Settings()
```

`backend/app/core/db.py` - identical shape to the service's `db.py`
(Base, engine from `settings.database_url`, `async_session_factory`,
`get_session`).

`backend/app/core/security.py`:

```python
"""Password hashing and session tokens."""

from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

password_hash = PasswordHash.recommended()
SESSION_DAYS = 7


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_session_token(user_id: str) -> str:
    payload = {"sub": user_id, "exp": datetime.now(UTC) + timedelta(days=SESSION_DAYS)}
    return jwt.encode(payload, settings.session_secret, algorithm="HS256")


def decode_session_token(token: str) -> str:
    """Return the user id. Raises jwt.PyJWTError on invalid or expired tokens."""
    return jwt.decode(token, settings.session_secret, algorithms=["HS256"])["sub"]
```

`backend/app/models/portal.py`:

```python
"""Portal users, invite codes, and terms acceptances."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PortalUser(Base):
    __tablename__ = "portal_users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verify_token: Mapped[str | None] = mapped_column(Text, unique=True)
    tenant_client_id: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InviteCode(Base):
    __tablename__ = "invite_codes"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    used_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("portal_users.id"))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TosAcceptance(Base):
    __tablename__ = "tos_acceptances"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("portal_users.id"))
    tos_version: Mapped[str] = mapped_column(Text)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

`backend/app/models/__init__.py` re-exports the three models.

`backend/app/main.py` (grows in later tasks):

```python
from fastapi import FastAPI

app = FastAPI(title="Lease Koala Developer Portal")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
```

- [ ] **Step 3: Alembic**

`uv run alembic init alembic`, then make `alembic/env.py` async (copy the
service repo's `alembic/env.py` wholesale - it reads `settings.database_url`
and `Base.metadata`; adjust the two imports to `app.core.config` /
`app.core.db` and import `app.models` for metadata). Write
`alembic/versions/c7a1d94e02b5_portal_tables.py` creating the three tables
exactly as the models define them (`revision = "c7a1d94e02b5"`,
`down_revision = None`; `op.create_table` per model as in the tenant
migration pattern; downgrade drops in reverse order).

Verify locally:

```bash
docker exec rental_management_app-db-1 createdb -U rental lease_portal
docker exec rental_management_app-db-1 createdb -U rental lease_portal_test
uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head
```

- [ ] **Step 4: conftest and first test**

`backend/tests/conftest.py` - copy the service conftest's `db_engine` /
`db_session` / `client` fixture pattern, with
`TEST_DATABASE_URL = settings.test_database_url` and importing `app.main`.

`backend/tests/test_models.py`:

```python
from app.models import InviteCode, PortalUser, TosAcceptance


async def test_models_roundtrip(db_session):
    user = PortalUser(email="dev@example.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    db_session.add_all(
        [
            InviteCode(code="inv_abc123def456", used_by=user.id),
            TosAcceptance(user_id=user.id, tos_version="2026-07-30"),
        ]
    )
    await db_session.commit()
    assert user.email_verified_at is None
    assert user.tenant_client_id is None
```

Run: `uv run pytest` -> 1 passed.

- [ ] **Step 5: CI workflow**

`.github/workflows/ci.yml`: copy the service repo's test+lint job shape -
postgres:16 service container (create `lease_portal_test`), working
directory `backend/`, `uv sync`, `uv run pytest`,
`uv run ruff check . && uv run ruff format --check .`. No publish job yet
(Task 10 adds it).

- [ ] **Step 6: Ruff, commit, push, CI**

```bash
cd ~/LLMProjects/lease-portal/backend
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
cd .. && git add -A && git commit -m "Scaffold portal backend: models, migration, CI" && git push -u origin main
gh run watch --exit-status
```

---

### Task 4: Signup, invites, ToS record, verification tokens, email

**Repo:** lease-portal

**Files:**
- Create: `backend/app/core/email.py` (copy the SaaS `send_email` verbatim: Resend when `resend_api_key` set, logged stub otherwise)
- Create: `backend/app/routers/auth.py` (signup + verify + resend endpoints; login comes in Task 5)
- Create: `backend/app/invites/__init__.py`, `backend/app/invites/__main__.py`
- Modify: `backend/app/main.py` (include router)
- Test: `backend/tests/test_signup.py`, `backend/tests/test_invites_cli.py`

**Interfaces:**
- Produces: `POST /api/signup {invite_code, email, password, accept_tos: bool}` -> 201; `POST /api/verify {token}` -> `{provisioned: bool, api_key?: str, client_id?: str}` (provisioning wired in Task 6 - until then it returns `{provisioned: false}`); `POST /api/resend {email}` -> 204 always. `app.invites.new_codes(session, count) -> list[str]`, `mark_used(session, code, user_id)`, plus the CLI.

- [ ] **Step 1: Failing tests**

`backend/tests/test_signup.py`:

```python
import pytest
from sqlalchemy import select

from app.invites import new_codes
from app.models import InviteCode, PortalUser, TosAcceptance

BODY = {"email": "dev@example.com", "password": "hunter2secure", "accept_tos": True}


@pytest.fixture
async def invite(db_session):
    return (await new_codes(db_session, 1))[0]


async def test_signup_creates_user_tos_and_burns_invite(client, db_session, invite):
    response = await client.post("/api/signup", json={"invite_code": invite, **BODY})
    assert response.status_code == 201

    user = (await db_session.execute(select(PortalUser))).scalar_one()
    assert user.email == "dev@example.com"
    assert user.password_hash != BODY["password"]
    assert user.verify_token is not None
    assert user.email_verified_at is None

    tos = (await db_session.execute(select(TosAcceptance))).scalar_one()
    assert tos.tos_version == "2026-07-30"

    code = (await db_session.execute(select(InviteCode))).scalar_one()
    assert code.used_by == user.id and code.used_at is not None


async def test_signup_rejects_bad_or_used_invite(client, db_session, invite):
    bad = await client.post("/api/signup", json={"invite_code": "inv_nope", **BODY})
    assert bad.status_code == 422

    await client.post("/api/signup", json={"invite_code": invite, **BODY})
    reused = await client.post(
        "/api/signup",
        json={
            "invite_code": invite,
            "email": "two@example.com",
            "password": "hunter2secure",
            "accept_tos": True,
        },
    )
    assert reused.status_code == 422


async def test_signup_requires_tos_and_unique_email(client, db_session):
    codes = await new_codes(db_session, 2)
    no_tos = await client.post(
        "/api/signup", json={"invite_code": codes[0], **{**BODY, "accept_tos": False}}
    )
    assert no_tos.status_code == 422

    await client.post("/api/signup", json={"invite_code": codes[0], **BODY})
    dup = await client.post("/api/signup", json={"invite_code": codes[1], **BODY})
    assert dup.status_code == 422


async def test_verify_token_is_single_use(client, db_session, invite):
    await client.post("/api/signup", json={"invite_code": invite, **BODY})
    user = (await db_session.execute(select(PortalUser))).scalar_one()

    ok = await client.post("/api/verify", json={"token": user.verify_token})
    assert ok.status_code == 200
    assert ok.json()["provisioned"] is False

    again = await client.post("/api/verify", json={"token": user.verify_token})
    assert again.status_code == 422

    await db_session.refresh(user)
    assert user.email_verified_at is not None
    assert user.verify_token is None


async def test_resend_rotates_token(client, db_session, invite):
    await client.post("/api/signup", json={"invite_code": invite, **BODY})
    user = (await db_session.execute(select(PortalUser))).scalar_one()
    old = user.verify_token

    response = await client.post("/api/resend", json={"email": "dev@example.com"})
    assert response.status_code == 204
    await db_session.refresh(user)
    assert user.verify_token != old

    ghost = await client.post("/api/resend", json={"email": "ghost@example.com"})
    assert ghost.status_code == 204
```

`backend/tests/test_invites_cli.py`:

```python
from sqlalchemy import select

from app.invites import new_codes
from app.models import InviteCode


async def test_new_codes_have_format_and_persist(db_session):
    codes = await new_codes(db_session, 3)
    assert len(codes) == 3
    assert all(c.startswith("inv_") and len(c) == 16 for c in codes)
    rows = (await db_session.execute(select(InviteCode))).scalars().all()
    assert {r.code for r in rows} == set(codes)
```

- [ ] **Step 2: Watch them fail** - `uv run pytest` -> import errors for `app.invites`, 404s for routes.

- [ ] **Step 3: Implement**

`backend/app/invites/__init__.py`:

```python
"""Invite code management."""

import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InviteCode


async def new_codes(session: AsyncSession, count: int) -> list[str]:
    codes = ["inv_" + secrets.token_urlsafe(9)[:12] for _ in range(count)]
    session.add_all(InviteCode(code=c) for c in codes)
    await session.commit()
    return codes


async def claim(session: AsyncSession, code: str, user_id: UUID) -> bool:
    """Mark an unused invite as used. False when missing or already used."""
    invite = (
        await session.execute(select(InviteCode).where(InviteCode.code == code))
    ).scalar_one_or_none()
    if invite is None or invite.used_by is not None:
        return False
    invite.used_by = user_id
    invite.used_at = datetime.now(UTC)
    return True
```

`backend/app/invites/__main__.py`: argparse with `new [--count N]`
(prints codes) and `list` (prints code, created, used_by or "-"), same
CLI shape as the service's `app.tenants.__main__`, with the
`if __name__ == "__main__":` guard.

`backend/app/routers/auth.py`:

```python
"""Signup, verification and session endpoints."""

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import TOS_VERSION, settings
from app.core.db import get_session
from app.core.email import send_email
from app.core.security import hash_password
from app.invites import claim
from app.models import PortalUser, TosAcceptance

router = APIRouter(prefix="/api")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class SignupBody(BaseModel):
    invite_code: str
    email: EmailStr
    password: str
    accept_tos: bool


async def _send_verification(user: PortalUser) -> None:
    link = f"{settings.portal_base_url}/verify?token={user.verify_token}"
    await send_email(
        user.email,
        "Verify your Lease Koala developer account",
        f'<p>Confirm your email to finish signing up:</p><p><a href="{link}">{link}</a></p>',
    )


@router.post("/signup", status_code=201)
async def signup(body: SignupBody, session: SessionDep) -> dict:
    if not body.accept_tos:
        raise HTTPException(status_code=422, detail="You must accept the terms of service")
    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
    existing = (
        await session.execute(select(PortalUser).where(PortalUser.email == body.email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=422, detail="An account with this email already exists")
    user = PortalUser(
        email=body.email,
        password_hash=hash_password(body.password),
        verify_token=secrets.token_urlsafe(32),
    )
    session.add(user)
    await session.flush()
    if not await claim(session, body.invite_code, user.id):
        await session.rollback()
        raise HTTPException(status_code=422, detail="Invalid or already used invite code")
    session.add(TosAcceptance(user_id=user.id, tos_version=TOS_VERSION))
    await session.commit()
    await _send_verification(user)
    return {"email": user.email}


class VerifyBody(BaseModel):
    token: str


@router.post("/verify")
async def verify(body: VerifyBody, session: SessionDep) -> dict:
    user = (
        await session.execute(select(PortalUser).where(PortalUser.verify_token == body.token))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=422, detail="Invalid or already used verification link")
    user.email_verified_at = datetime.now(UTC)
    user.verify_token = None
    await session.commit()
    return {"provisioned": False}


class ResendBody(BaseModel):
    email: EmailStr


@router.post("/resend", status_code=204)
async def resend(body: ResendBody, session: SessionDep) -> None:
    user = (
        await session.execute(select(PortalUser).where(PortalUser.email == body.email))
    ).scalar_one_or_none()
    if user is None or user.email_verified_at is not None:
        return
    user.verify_token = secrets.token_urlsafe(32)
    await session.commit()
    await _send_verification(user)
```

Wire into `app/main.py`. (Task 6 replaces `verify`'s return with real
provisioning.)

- [ ] **Step 4: Run, ruff, commit** - `uv run pytest` all green; ruff sequence; commit "Add signup, verification and invite codes"; push; CI green.

---

### Task 5: Login, session cookie, auth gating

**Repo:** lease-portal

**Files:**
- Modify: `backend/app/routers/auth.py`
- Create: `backend/app/core/auth.py` (current-user dependency)
- Test: `backend/tests/test_login.py`

**Interfaces:**
- Produces: `POST /api/login {email, password}` sets the `portal_session` cookie, 200 `{email, verified: bool}`; `POST /api/logout` clears it; `GET /api/me` -> `{email, verified, client_id}` or 401. `CurrentUser` dependency (`app.core.auth.current_user`) returning a verified-or-not `PortalUser`; a `VerifiedUser` variant that 403s when unverified. Tasks 6-7 consume `VerifiedUser`.

- [ ] **Step 1: Failing tests**

`backend/tests/test_login.py`:

```python
import pytest
from sqlalchemy import select

from app.invites import new_codes
from app.models import PortalUser

BODY = {"email": "dev@example.com", "password": "hunter2secure", "accept_tos": True}


@pytest.fixture
async def signed_up(client, db_session):
    invite = (await new_codes(db_session, 1))[0]
    await client.post("/api/signup", json={"invite_code": invite, **BODY})
    return (await db_session.execute(select(PortalUser))).scalar_one()


async def test_login_sets_cookie_and_me_works(client, signed_up):
    response = await client.post(
        "/api/login", json={"email": BODY["email"], "password": BODY["password"]}
    )
    assert response.status_code == 200
    assert "portal_session" in response.cookies

    me = await client.get("/api/me")
    assert me.status_code == 200
    assert me.json() == {"email": "dev@example.com", "verified": False, "client_id": None}


async def test_wrong_password_is_401(client, signed_up):
    response = await client.post(
        "/api/login", json={"email": BODY["email"], "password": "wrong-password"}
    )
    assert response.status_code == 401


async def test_me_without_cookie_is_401(client):
    assert (await client.get("/api/me")).status_code == 401


async def test_logout_clears_session(client, signed_up):
    await client.post("/api/login", json={"email": BODY["email"], "password": BODY["password"]})
    await client.post("/api/logout")
    assert (await client.get("/api/me")).status_code == 401


async def test_verified_gate(client, db_session, signed_up):
    await client.post("/api/login", json={"email": BODY["email"], "password": BODY["password"]})
    keys = await client.get("/api/keys")
    assert keys.status_code == 403
```

(`/api/keys` arrives in Task 7; for this task add a placeholder route
`GET /api/keys` returning `{"keys": []}` behind `VerifiedUser` so the gate
is testable end-to-end.)

- [ ] **Step 2: Watch fail.**

- [ ] **Step 3: Implement**

`backend/app/core/auth.py`:

```python
"""Session-cookie authentication for portal routes."""

import uuid
from typing import Annotated

import jwt
from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import decode_session_token
from app.models import PortalUser

SESSION_COOKIE = "portal_session"


async def current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    portal_session: str = Cookie(default=""),
) -> PortalUser:
    if not portal_session:
        raise HTTPException(status_code=401, detail="Not signed in")
    try:
        user_id = decode_session_token(portal_session)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Not signed in") from exc
    user = await session.get(PortalUser, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


CurrentUser = Annotated[PortalUser, Depends(current_user)]


async def verified_user(user: CurrentUser) -> PortalUser:
    if user.email_verified_at is None:
        raise HTTPException(status_code=403, detail="Verify your email first")
    return user


VerifiedUser = Annotated[PortalUser, Depends(verified_user)]
```

Login/logout/me in `auth.py` router: login verifies password
(`verify_password`), sets the cookie via
`response.set_cookie(SESSION_COOKIE, token, max_age=7*24*3600, httponly=True, secure=True, samesite="lax")`,
logout `response.delete_cookie(SESSION_COOKIE)`, `/api/me` returns
`{"email": user.email, "verified": user.email_verified_at is not None, "client_id": user.tenant_client_id}`.

- [ ] **Step 4: Run, ruff, commit** - "Add login, session cookies and verified gating".

---

### Task 6: Admin client, provisioning on verify, retry rule

**Repo:** lease-portal

**Files:**
- Create: `backend/app/core/admin_client.py`
- Create: `backend/app/provisioning.py`
- Modify: `backend/app/routers/auth.py` (verify calls provisioning; add `POST /api/provision/retry`)
- Test: `backend/tests/test_provisioning.py`

**Interfaces:**
- Produces: `AdminClient` with `create_tenant(client_id, name) -> dict` (raises `AdminConflict` on 409, `AdminError` otherwise), `get_tenant(client_id) -> dict | None`, `new_key(client_id) -> str`, `revoke_key(prefix) -> None` (raises `AdminConflict` on 409), `usage(client_id, days) -> list[dict]`; `provision(session, user) -> str` returning the plaintext key and filling `tenant_client_id`; `client_id_candidates(email)`.

- [ ] **Step 1: Failing tests** (respx mocks `settings.admin_api_url`)

`backend/tests/test_provisioning.py`:

```python
import pytest
import respx
from httpx import Response
from sqlalchemy import select

from app.core.config import settings
from app.invites import new_codes
from app.models import PortalUser
from app.provisioning import client_id_candidates, provision

BODY = {"email": "jane.doe+dev@example.com", "password": "hunter2secure", "accept_tos": True}


def test_client_id_candidates_slugify():
    assert client_id_candidates("jane.doe+dev@example.com")[:3] == [
        "jane-doe-dev",
        "jane-doe-dev-2",
        "jane-doe-dev-3",
    ]


@pytest.fixture
async def verified_user(client, db_session):
    invite = (await new_codes(db_session, 1))[0]
    await client.post("/api/signup", json={"invite_code": invite, **BODY})
    user = (await db_session.execute(select(PortalUser))).scalar_one()
    token = user.verify_token
    return user, token


@respx.mock
async def test_verify_provisions_and_returns_key(client, db_session, verified_user):
    user, token = verified_user
    respx.post(f"{settings.admin_api_url}/admin/tenants").mock(
        return_value=Response(201, json={"client_id": "jane-doe-dev", "api_key": "lk_secret1"})
    )
    response = await client.post("/api/verify", json={"token": token})
    assert response.status_code == 200
    assert response.json() == {
        "provisioned": True,
        "client_id": "jane-doe-dev",
        "api_key": "lk_secret1",
    }
    await db_session.refresh(user)
    assert user.tenant_client_id == "jane-doe-dev"


@respx.mock
async def test_verify_survives_admin_outage(client, db_session, verified_user):
    user, token = verified_user
    respx.post(f"{settings.admin_api_url}/admin/tenants").mock(
        return_value=Response(500, json={"detail": "boom"})
    )
    response = await client.post("/api/verify", json={"token": token})
    assert response.status_code == 200
    assert response.json() == {"provisioned": False}
    await db_session.refresh(user)
    assert user.email_verified_at is not None
    assert user.tenant_client_id is None


@respx.mock
async def test_provision_walks_collisions(db_session, verified_user):
    user, _ = verified_user
    route = respx.post(f"{settings.admin_api_url}/admin/tenants")
    route.side_effect = [
        Response(409, json={"detail": "exists"}),
        Response(201, json={"client_id": "jane-doe-dev-2", "api_key": "lk_secret2"}),
    ]
    respx.get(f"{settings.admin_api_url}/admin/tenants/jane-doe-dev").mock(
        return_value=Response(200, json={"client_id": "jane-doe-dev"})
    )
    key = await provision(db_session, user)
    assert key == "lk_secret2"
    assert user.tenant_client_id == "jane-doe-dev-2"


@respx.mock
async def test_retry_reuses_half_created_tenant(client, db_session, verified_user):
    user, token = verified_user
    respx.post(f"{settings.admin_api_url}/admin/tenants").mock(
        return_value=Response(500, json={"detail": "boom"})
    )
    await client.post("/api/verify", json={"token": token})

    respx.get(f"{settings.admin_api_url}/admin/tenants/jane-doe-dev").mock(
        return_value=Response(200, json={"client_id": "jane-doe-dev"})
    )
    respx.post(f"{settings.admin_api_url}/admin/tenants/jane-doe-dev/keys").mock(
        return_value=Response(201, json={"api_key": "lk_recovered"})
    )
    await client.post("/api/login", json={"email": BODY["email"], "password": BODY["password"]})
    response = await client.post("/api/provision/retry")
    assert response.status_code == 200
    assert response.json()["api_key"] == "lk_recovered"
    await db_session.refresh(user)
    assert user.tenant_client_id == "jane-doe-dev"
```

Provisioning semantics under test: on create-409 for a candidate, check
GET - if the tenant exists but no portal user owns it (checked via the
unique `tenant_client_id` column), it is our half-created tenant from a
crashed earlier attempt only when a previous 500 happened AFTER create
succeeded server-side; the retry path issues a new key for it. When GET
shows it exists and it is NOT ours (owned by another user row), move to
the next candidate.

- [ ] **Step 2: Watch fail.**

- [ ] **Step 3: Implement**

`backend/app/core/admin_client.py`:

```python
"""HTTP client for the lease-compliance-service admin API."""

import httpx

from app.core.config import settings


class AdminError(Exception):
    pass


class AdminConflict(AdminError):
    pass


class AdminClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.admin_api_url,
            headers={"X-Admin-Key": settings.admin_api_key},
            timeout=15,
        )

    async def create_tenant(self, client_id: str, name: str) -> dict:
        response = await self._client.post(
            "/admin/tenants", json={"client_id": client_id, "name": name}
        )
        if response.status_code == 409:
            raise AdminConflict(client_id)
        if response.status_code != 201:
            raise AdminError(f"create_tenant -> {response.status_code}")
        return response.json()

    async def get_tenant(self, client_id: str) -> dict | None:
        response = await self._client.get(f"/admin/tenants/{client_id}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise AdminError(f"get_tenant -> {response.status_code}")
        return response.json()

    async def new_key(self, client_id: str) -> str:
        response = await self._client.post(f"/admin/tenants/{client_id}/keys")
        if response.status_code != 201:
            raise AdminError(f"new_key -> {response.status_code}")
        return response.json()["api_key"]

    async def revoke_key(self, prefix: str) -> None:
        response = await self._client.delete(f"/admin/keys/{prefix}")
        if response.status_code == 409:
            raise AdminConflict(prefix)
        if response.status_code not in (204, 404):
            raise AdminError(f"revoke_key -> {response.status_code}")

    async def usage(self, client_id: str, days: int) -> list[dict]:
        response = await self._client.get(f"/admin/tenants/{client_id}/usage?days={days}")
        if response.status_code != 200:
            raise AdminError(f"usage -> {response.status_code}")
        return response.json()
```

`backend/app/provisioning.py`:

```python
"""Turn a verified portal user into a service tenant with one API key."""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_client import AdminClient, AdminConflict
from app.models import PortalUser

MAX_CANDIDATES = 10


def client_id_candidates(email: str) -> list[str]:
    base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0].lower()).strip("-") or "tenant"
    return [base] + [f"{base}-{n}" for n in range(2, MAX_CANDIDATES + 1)]


async def _owned_by_someone(session: AsyncSession, client_id: str) -> bool:
    row = (
        await session.execute(select(PortalUser.id).where(PortalUser.tenant_client_id == client_id))
    ).first()
    return row is not None


async def provision(session: AsyncSession, user: PortalUser) -> str:
    """Create (or recover) the user's tenant; returns the plaintext key once."""
    admin = AdminClient()
    for candidate in client_id_candidates(user.email):
        try:
            created = await admin.create_tenant(candidate, user.email)
        except AdminConflict:
            existing = await admin.get_tenant(candidate)
            if existing is not None and not await _owned_by_someone(session, candidate):
                key = await admin.new_key(candidate)
                user.tenant_client_id = candidate
                await session.commit()
                return key
            continue
        user.tenant_client_id = created["client_id"]
        await session.commit()
        return created["api_key"]
    raise AdminConflict("no free client_id candidate")
```

In `auth.py`, `verify` becomes: mark verified + commit (as now), then

```python
    try:
        key = await provision(session, user)
    except AdminError:
        return {"provisioned": False}
    return {"provisioned": True, "client_id": user.tenant_client_id, "api_key": key}
```

(`AdminError` import covers `AdminConflict` exhaustion too.) Add:

```python
@router.post("/provision/retry")
async def provision_retry(user: VerifiedUser, session: SessionDep) -> dict:
    if user.tenant_client_id is not None:
        raise HTTPException(status_code=409, detail="Already provisioned")
    try:
        key = await provision(session, user)
    except AdminError as exc:
        raise HTTPException(status_code=502, detail="Provisioning failed; try again") from exc
    return {"provisioned": True, "client_id": user.tenant_client_id, "api_key": key}
```

The user object passed to `provision` must belong to `session` (fetch it
via `session.get(PortalUser, user.id)` inside the endpoints before
calling, since the dependency's instance came from the same request
session - it does, so passing it directly is fine).

- [ ] **Step 4: Run, ruff, commit** - "Provision tenants through the admin API".

---

### Task 7: Dashboard backend - keys and usage endpoints

**Repo:** lease-portal

**Files:**
- Create: `backend/app/routers/dashboard.py`
- Modify: `backend/app/main.py`; remove the Task-5 placeholder `/api/keys`
- Test: `backend/tests/test_dashboard.py`

**Interfaces:**
- Produces: `GET /api/tenant` (admin tenant_info passthrough), `POST /api/keys` -> `{api_key}`, `DELETE /api/keys/{prefix}` -> 204, `GET /api/usage?days=30` (passthrough). All behind `VerifiedUser` + provisioned (403 `Not provisioned yet` when `tenant_client_id` is null).

- [ ] **Step 1: Failing tests** - `backend/tests/test_dashboard.py` with respx: a `provisioned_user` fixture (signup + verify with mocked create 201 + login); `GET /api/tenant` proxies the admin info JSON verbatim; `POST /api/keys` returns the new key; `DELETE /api/keys/{prefix}` 204; `GET /api/usage?days=7` proxies rows; unprovisioned user gets 403 on all four; unauthenticated 401.

```python
import pytest
import respx
from httpx import Response
from sqlalchemy import select

from app.core.config import settings
from app.invites import new_codes
from app.models import PortalUser

BODY = {"email": "dev@example.com", "password": "hunter2secure", "accept_tos": True}
INFO = {
    "client_id": "dev",
    "name": "dev@example.com",
    "status": "active",
    "rpm_limit": 60,
    "clause_audits_per_day": 10,
    "keys": [
        {
            "prefix": "lk_abc12",
            "status": "active",
            "created_at": "2026-07-30T00:00:00",
            "last_used_at": None,
        }
    ],
    "today": {"audit": 0, "clause_audit": 0, "legislation": 0},
}


@pytest.fixture
async def provisioned(client, db_session):
    invite = (await new_codes(db_session, 1))[0]
    await client.post("/api/signup", json={"invite_code": invite, **BODY})
    user = (await db_session.execute(select(PortalUser))).scalar_one()
    with respx.mock:
        respx.post(f"{settings.admin_api_url}/admin/tenants").mock(
            return_value=Response(201, json={"client_id": "dev", "api_key": "lk_first00"})
        )
        await client.post("/api/verify", json={"token": user.verify_token})
    await client.post("/api/login", json={"email": BODY["email"], "password": BODY["password"]})
    return user


@respx.mock
async def test_tenant_info_passthrough(client, provisioned):
    respx.get(f"{settings.admin_api_url}/admin/tenants/dev").mock(
        return_value=Response(200, json=INFO)
    )
    response = await client.get("/api/tenant")
    assert response.status_code == 200
    assert response.json() == INFO


@respx.mock
async def test_new_key_and_revoke(client, provisioned):
    respx.post(f"{settings.admin_api_url}/admin/tenants/dev/keys").mock(
        return_value=Response(201, json={"api_key": "lk_second1"})
    )
    respx.delete(f"{settings.admin_api_url}/admin/keys/lk_abc12").mock(return_value=Response(204))
    created = await client.post("/api/keys")
    assert created.status_code == 200
    assert created.json() == {"api_key": "lk_second1"}
    assert (await client.delete("/api/keys/lk_abc12")).status_code == 204


@respx.mock
async def test_usage_passthrough(client, provisioned):
    rows = [{"day": "2026-07-30", "endpoint_class": "audit", "count": 2}]
    respx.get(f"{settings.admin_api_url}/admin/tenants/dev/usage?days=7").mock(
        return_value=Response(200, json=rows)
    )
    response = await client.get("/api/usage?days=7")
    assert response.json() == rows


async def test_unprovisioned_is_403(client, db_session):
    invite = (await new_codes(db_session, 1))[0]
    await client.post("/api/signup", json={"invite_code": invite, **BODY})
    user = (await db_session.execute(select(PortalUser))).scalar_one()
    with respx.mock:
        respx.post(f"{settings.admin_api_url}/admin/tenants").mock(
            return_value=Response(500, json={"detail": "boom"})
        )
        await client.post("/api/verify", json={"token": user.verify_token})
    await client.post("/api/login", json={"email": BODY["email"], "password": BODY["password"]})
    assert (await client.get("/api/tenant")).status_code == 403
```

- [ ] **Step 2: Watch fail. Step 3: Implement**

`backend/app/routers/dashboard.py`:

```python
"""Key management and usage, proxied through the admin API."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.core.admin_client import AdminClient, AdminError
from app.core.auth import VerifiedUser
from app.models import PortalUser

router = APIRouter(prefix="/api")


def provisioned_user(user: VerifiedUser) -> PortalUser:
    if user.tenant_client_id is None:
        raise HTTPException(status_code=403, detail="Not provisioned yet")
    return user


Provisioned = Annotated[PortalUser, Depends(provisioned_user)]


@router.get("/tenant")
async def tenant(user: Provisioned) -> dict:
    try:
        info = await AdminClient().get_tenant(user.tenant_client_id)
    except AdminError as exc:
        raise HTTPException(status_code=502, detail="Service unavailable") from exc
    if info is None:
        raise HTTPException(status_code=502, detail="Tenant missing")
    return info


@router.post("/keys")
async def create_key(user: Provisioned) -> dict:
    try:
        key = await AdminClient().new_key(user.tenant_client_id)
    except AdminError as exc:
        raise HTTPException(status_code=502, detail="Service unavailable") from exc
    return {"api_key": key}


@router.delete("/keys/{prefix}", status_code=204)
async def revoke(prefix: str, user: Provisioned) -> None:
    try:
        await AdminClient().revoke_key(prefix)
    except AdminError as exc:
        raise HTTPException(status_code=502, detail="Service unavailable") from exc


@router.get("/usage")
async def usage(user: Provisioned, days: int = 30) -> list[dict]:
    try:
        return await AdminClient().usage(user.tenant_client_id, days)
    except AdminError as exc:
        raise HTTPException(status_code=502, detail="Service unavailable") from exc
```

Remove the Task-5 placeholder `/api/keys` GET; update the Task-5 gate
test to call `GET /api/tenant` instead.

- [ ] **Step 4: Run, ruff, commit** - "Add dashboard key and usage endpoints".

---

### Task 8: Frontend scaffold and auth pages

**Repo:** lease-portal

**Files:**
- Create: `frontend/` via `npx create-next-app@latest frontend --ts --tailwind --app --no-src-dir --import-alias "@/*"` (then move pages under `app/`)
- Create: `frontend/lib/api.ts`, `frontend/app/signup/page.tsx`, `frontend/app/verify/page.tsx`, `frontend/app/login/page.tsx`, `frontend/app/layout.tsx` (footer disclaimer), `frontend/components/KeyReveal.tsx`

**Interfaces:**
- Consumes: `/api/*` endpoints from Tasks 4-6, same origin.
- Produces: `apiFetch(path, init)` helper (`fetch(path, {credentials: "include", ...})` + JSON error unwrapping); `KeyReveal` (shows a plaintext key once with copy button and warning) reused by the dashboard in Task 9.

- [ ] **Step 1: Scaffold** - run create-next-app (versions will match the SaaS: Next 16 / React 19 / Tailwind 4); delete boilerplate; set `next.config.ts` to `{ output: "export" }` now so every page stays static-export-compatible from the start. All data loading is client-side (`"use client"` pages), matching the SaaS convention.

- [ ] **Step 2: `frontend/lib/api.ts`**

```typescript
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail ?? `Request failed (${response.status})`);
  return body as T;
}
```

- [ ] **Step 3: Pages**

`app/layout.tsx`: html/body wrapper, small header ("Lease Koala Developers"),
`<main>{children}</main>`, footer `General information, not legal advice.`
on every page.

`app/signup/page.tsx` (`"use client"`): controlled form - invite code,
email, password, ToS checkbox with `I accept the <a href="/terms">Terms of
Service</a>` label; submit disabled until checked; on success swap to a
"Check your email" panel with a re-send button calling `/api/resend`.
Errors from `apiFetch` render inline above the submit button.

`app/verify/page.tsx` (`"use client"`): reads `token` from
`useSearchParams()` inside a `<Suspense>` boundary (required for static
export); on mount POSTs `/api/verify`; renders three states - verifying
spinner; success with `<KeyReveal apiKey={...} clientId={...}/>` and a
"Go to dashboard" link; `provisioned: false` state with "Your account is
verified but provisioning is pending - retry from the dashboard." and a
dashboard link; invalid-token error with a re-send form.

`components/KeyReveal.tsx`: monospace box with the key, copy-to-clipboard
button, and the warning line "Store this key now - you will not see it
again."

`app/login/page.tsx`: email + password; on success route to `/dashboard`;
on 401 show the error; below the form a link to `/signup`.

- [ ] **Step 4: Verify** - `npm run lint && npm run build` (build must succeed with `output: "export"`, emitting `out/`). Commit "Scaffold frontend with signup, verify and login pages"; push; CI green (frontend job added to CI in this step: `working-directory: frontend`, `npm ci`, `npm run lint`, `npm run build`).

---

### Task 9: Frontend dashboard, docs, terms

**Repo:** lease-portal

**Files:**
- Create: `frontend/app/dashboard/page.tsx`, `frontend/app/docs/page.tsx`, `frontend/app/terms/page.tsx`, `frontend/components/UsageChart.tsx`

- [ ] **Step 1: `app/dashboard/page.tsx`** (`"use client"`): on mount `GET /api/me` (redirect to `/login` on 401); when `client_id` is null show the retry panel (`POST /api/provision/retry`, on success render `KeyReveal`); otherwise load `GET /api/tenant` and `GET /api/usage?days=30` in parallel. Render: limits card (rpm, clause/day, status, "Contact us to change limits"); keys table (prefix, status, created, last used) with per-row Revoke button (`confirm()` then `DELETE /api/keys/{prefix}`, reload info) and a "New key" button (`POST /api/keys` -> `KeyReveal` in a modal-style panel); usage section with `UsageChart` and a daily table.

- [ ] **Step 2: `components/UsageChart.tsx`**: no chart library - group rows by day summed across classes, render a flex row of divs whose heights scale to the max count, tooltip via `title`; beneath it a plain table (day, audit, clause_audit, legislation).

- [ ] **Step 3: `app/docs/page.tsx`**: static quickstart. Three fenced-style `<pre>` blocks with curl examples against `https://api.leasekoala.com`: deterministic audit POST (the JSON body from the service README/acceptance), clause audit multipart POST (`-F payload=... -F file=@lease.pdf`), and polling GET; a note that `X-API-Key` comes from the dashboard. Error table: 401 invalid key, 403 suspended, 413 too large, 422 validation, 429 with `Retry-After` (per-minute or daily quota). Rate-limit defaults stated (60 rpm, 10 clause audits/day).

- [ ] **Step 4: `app/terms/page.tsx`**: full ToS text (English), version heading `Terms of Service - version 2026-07-30`, sections: 1 Nature of the service (general information, not legal advice, no solicitor-client relationship); 2 AI processing disclosure (documents submitted for clause audit are transmitted to Anthropic's API for analysis; deleted from our systems when processing completes; per Anthropic's API terms, inputs are not used to train models; do not upload unnecessary personal information); 3 Accounts and acceptable use (one account per organisation, keys are secrets, no abuse/reverse engineering/resale); 4 Quotas and fees (free trial limits today, paid plans to come with notice); 5 Disclaimer and liability (as-is, output may be wrong, verify with a qualified professional; liability capped at fees paid in the last 12 months); 6 Termination; 7 Governing law (New South Wales, Australia). Write the full prose in the page - roughly 60-80 lines of JSX paragraphs.

- [ ] **Step 5: Verify + commit** - `npm run lint && npm run build`; commit "Add dashboard, docs and terms pages"; push; CI green.

---

### Task 10: Portal image - static export served by FastAPI, publish job

**Repo:** lease-portal

**Files:**
- Modify: `backend/app/main.py` (mount static export)
- Create: `Dockerfile` (repo root), `.dockerignore`
- Modify: `.github/workflows/ci.yml` (publish job)

- [ ] **Step 1: Static mount** - append to `backend/app/main.py` after the routers:

```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
```

(html=True serves `signup/index.html` style exports; the dir is absent in
dev/tests so the mount is skipped.)

- [ ] **Step 2: Dockerfile** (repo root):

```dockerfile
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend .
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY --from=web /web/out ./static
EXPOSE 8001
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

`.dockerignore`: `.git`, `**/.venv`, `**/__pycache__`, `frontend/node_modules`, `frontend/.next`, `.env`.

- [ ] **Step 3: Local smoke**

```bash
docker build -t lease-portal-smoke .
docker run --rm -d -p 8001:8001 -e DATABASE_URL=postgresql+asyncpg://x:x@host.docker.internal:5433/lease_portal --name portal-smoke lease-portal-smoke
sleep 3 && curl -fsS localhost:8001/healthz && curl -fsS localhost:8001/signup/ | head -c 200
docker rm -f portal-smoke
```

Expected: health JSON and the signup page HTML.

- [ ] **Step 4: Publish job** - copy the service CI publish job verbatim, image `ghcr.io/keith-hoka/lease-portal`, context `.`. Commit "Bake the static frontend into the portal image"; push; CI green including the first published image.

---

### Task 11: Stack integration - compose, Caddy, deploy script

**Repos:** lease-compliance-service (stack files) + lease-portal (deploy script)

**Files:**
- Modify: `deploy/compose.yaml`, `deploy/Caddyfile`, `deploy/env.example`, `deploy/README.md` (service repo)
- Create: `deploy/deploy-portal.sh` (portal repo)

- [ ] **Step 1: compose** - add to `deploy/compose.yaml` services:

```yaml
  portal:
    image: ghcr.io/keith-hoka/lease-portal:${PORTAL_TAG:-latest}
    env_file: .env.portal
    depends_on:
      db:
        condition: service_healthy
      api:
        condition: service_started
    restart: unless-stopped
```

- [ ] **Step 2: Caddyfile** becomes:

```
{$DOMAIN} {
	reverse_proxy api:8000
}

{$PORTAL_DOMAIN} {
	reverse_proxy portal:8001
}
```

and the caddy service in compose gains `PORTAL_DOMAIN: ${PORTAL_DOMAIN}`
under `environment`.

- [ ] **Step 3: env.example** - append `PORTAL_DOMAIN=portal.example.com` and a comment that `.env.portal` (chmod 600) holds: `DATABASE_URL` (lease_portal db on the db service), `SESSION_SECRET`, `ADMIN_API_URL=http://api:8000`, `ADMIN_API_KEY` (same value as the service `.env`), `RESEND_API_KEY`, `EMAIL_FROM=noreply@leasekoala.com`, `PORTAL_BASE_URL=https://portal.leasekoala.com`. The service `.env` gains `ADMIN_API_KEY=`.

- [ ] **Step 4: deploy-portal.sh** (portal repo, chmod +x), mirroring the service script:

```bash
#!/usr/bin/env bash
# Deploy (or roll back) the portal. Usage: deploy-portal.sh [image-tag]
set -euo pipefail

TAG="${1:-latest}"
SERVER="${LEASE_DEPLOY_SERVER:?set LEASE_DEPLOY_SERVER, e.g. deploy@1.2.3.4}"
DOMAIN="${PORTAL_DEPLOY_DOMAIN:?set PORTAL_DEPLOY_DOMAIN, e.g. portal.example.com}"

echo "deploying portal tag ${TAG} to ${SERVER}"
ssh "$SERVER" "cd /opt/lease-compliance \
  && PORTAL_TAG='${TAG}' docker compose pull portal \
  && PORTAL_TAG='${TAG}' docker compose run --rm portal uv run --no-sync alembic upgrade head \
  && PORTAL_TAG='${TAG}' docker compose up -d portal"

sleep 3
curl -fsS "https://${DOMAIN}/healthz"
echo ""
echo "deployed portal ${TAG}"
```

- [ ] **Step 5: README** - service `deploy/README.md` gains a Portal
section (deploy/rollback command, `.env.portal` note, invite CLI:
`docker compose exec portal uv run --no-sync python -m app.invites new`).
Commit both repos ("Add portal to the droplet stack" / "Add the portal
deploy script"); push; CI green on both.

---

### Task 12: Production rollout (interactive)

No repo changes except ledger. Steps marked **[you]** need your browser or accounts.

- [ ] **Step 1 [you]: DNS** - Cloudflare: `A portal -> 168.144.169.66`, DNS only, TTL 5 min.
- [ ] **Step 2 [you]: Resend** - verify the `leasekoala.com` domain in Resend (DNS records it asks for), so `noreply@leasekoala.com` can send. Hand me the API key (or confirm the SaaS `RESEND_API_KEY` may be reused).
- [ ] **Step 3: Secrets and database** (I run) - generate `ADMIN_API_KEY=$(openssl rand -hex 24)`; append it and `PORTAL_DOMAIN=portal.leasekoala.com` to `/opt/lease-compliance/.env`; write `/opt/lease-compliance/.env.portal` (chmod 600) with the variables from Task 11 Step 3 (`SESSION_SECRET=$(openssl rand -hex 32)`, `DATABASE_URL=postgresql+asyncpg://postgres:<pw>@db:5432/lease_portal`); `docker compose exec -T db createdb -U postgres lease_portal`.
- [ ] **Step 4: Deploy** - service first (`./deploy/deploy.sh`, picks up the admin API + ADMIN_API_KEY), then portal (`./deploy/deploy-portal.sh` after `git pull` of stack files: `scp deploy/compose.yaml deploy/Caddyfile` to the server). Checkpoints: `https://api.leasekoala.com/health` 200; `https://portal.leasekoala.com/healthz` 200 over valid TLS; `/admin/*` without the key returns 401 (and 404 from a box without the env, N/A here).
- [ ] **Step 5: Invite + live acceptance** - generate one invite code on the server; sign up with a real mailbox **[you]** (or keith.hoka@gmail.com), click the verification link, watch the key reveal; run one real curl audit with the new key; check the dashboard shows the usage counter and the key row; revoke the key from the UI and confirm the curl now 401s.
- [ ] **Step 6 [you]: ToS read-through** - read `/terms` once before any external invite goes out; lawyer review before public launch.
- [ ] **Step 7: Records** - append the rollout to `.superpowers/sdd/progress.md`; update the memory roadmap (sub-project 2 done).

---

## Self-review

- Spec coverage: admin API contract (Tasks 1-2 match the table incl. error
  mapping); portal data model + invite CLI (3-4); session semantics (5);
  provisioning + retry rule incl. half-created recovery (6); dashboard
  endpoints (7); all six pages, footer disclaimer, KeyReveal-once,
  chart-without-library, docs error table (8-9); static export in one
  image (10); compose/Caddy/env/deploy script (11); rollout incl. [you]
  DNS/Resend/ToS steps and end-to-end acceptance (12). Out-of-scope list
  honoured: no password reset, no billing, invite-gated.
- Placeholders: none - every code step carries the code; prose-described
  JSX (Tasks 8-9) names exact files, states, endpoints and copy.
- Type consistency: `AdminClient` method signatures match Tasks 6-7 usage;
  `VerifiedUser`/`Provisioned` dependency names consistent; admin JSON
  shapes in Task 2 match the respx fixtures in Tasks 6-7; `TOS_VERSION`
  constant matches the signup test and terms page heading.
