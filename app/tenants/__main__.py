"""Admin CLI: uv run python -m app.tenants <command> ..."""

import argparse
import asyncio
import sys

from app.core.db import async_session_factory
from app.tenants import (
    create_tenant,
    import_env_keys,
    new_key,
    revoke_key,
    set_limits,
    set_status,
    tenant_lines,
    usage_lines,
)


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
        elif args.command == "list":
            for line in await tenant_lines(session):
                print(line)
        elif args.command == "usage":
            for line in await usage_lines(session, args.client_id, args.days):
                print(line)


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
    try:
        asyncio.run(_run(args))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
