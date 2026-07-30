"""Admin CLI: uv run python -m app.tenants <command> ..."""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.db import async_session_factory
from app.models import Tenant, UsageCounter
from app.tenants import (
    create_tenant,
    import_env_keys,
    new_key,
    revoke_key,
    set_limits,
    set_status,
)


async def _list_tenants() -> None:
    async with async_session_factory() as session:
        tenants = (await session.execute(select(Tenant))).scalars().all()
        today = datetime.now(UTC).date()
        counters = (
            (await session.execute(select(UsageCounter).where(UsageCounter.day == today)))
            .scalars()
            .all()
        )
        by_tenant: dict = {}
        for c in counters:
            by_tenant.setdefault(c.tenant_id, {})[c.endpoint_class] = c.count
        for t in tenants:
            usage = by_tenant.get(t.id, {})
            print(
                f"{t.client_id:20} {t.status:10} rpm={t.rpm_limit:<5} "
                f"clause/day={t.clause_audits_per_day:<5} today={usage or '-'}"
            )


async def _usage(client_id: str, days: int) -> None:
    async with async_session_factory() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.client_id == client_id))
        ).scalar_one()
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
        for r in rows:
            print(f"{r.day} {r.endpoint_class:14} {r.count}")


async def _run(args: argparse.Namespace) -> None:
    async with async_session_factory() as session:
        if args.command == "create":
            key = await create_tenant(
                session, args.client_id, args.name, args.rpm, args.clause_per_day
            )
            print(f"created {args.client_id}")
            print(f"api key (shown once): {key}")
        elif args.command == "new-key":
            key = await new_key(session, args.client_id)
            print(f"api key (shown once): {key}")
        elif args.command == "revoke-key":
            await revoke_key(session, args.prefix)
            print(f"revoked {args.prefix}")
        elif args.command == "suspend":
            await set_status(session, args.client_id, "suspended")
            print(f"suspended {args.client_id}")
        elif args.command == "activate":
            await set_status(session, args.client_id, "active")
            print(f"activated {args.client_id}")
        elif args.command == "set-limits":
            await set_limits(session, args.client_id, args.rpm, args.clause_per_day)
            print(f"updated {args.client_id}")
        elif args.command == "import-env-keys":
            count = await import_env_keys(session)
            print(f"imported {count} keys")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.tenants")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("client_id")
    create.add_argument("--name", default="")
    create.add_argument("--rpm", type=int, default=60)
    create.add_argument("--clause-per-day", type=int, default=10, dest="clause_per_day")

    newkey = sub.add_parser("new-key")
    newkey.add_argument("client_id")

    revoke = sub.add_parser("revoke-key")
    revoke.add_argument("prefix")

    for name in ("suspend", "activate"):
        p = sub.add_parser(name)
        p.add_argument("client_id")

    limits = sub.add_parser("set-limits")
    limits.add_argument("client_id")
    limits.add_argument("--rpm", type=int, default=None)
    limits.add_argument("--clause-per-day", type=int, default=None, dest="clause_per_day")

    sub.add_parser("import-env-keys")
    sub.add_parser("list")

    usage = sub.add_parser("usage")
    usage.add_argument("client_id")
    usage.add_argument("--days", type=int, default=30)

    args = parser.parse_args()
    if args.command == "list":
        asyncio.run(_list_tenants())
    elif args.command == "usage":
        asyncio.run(_usage(args.client_id, args.days))
    else:
        asyncio.run(_run(args))


main()
