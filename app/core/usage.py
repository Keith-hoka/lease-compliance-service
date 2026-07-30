"""Daily billable-event counters. Caller commits the session."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageCounter


async def record_usage(session: AsyncSession, tenant_id: uuid.UUID, endpoint_class: str) -> None:
    stmt = insert(UsageCounter).values(
        tenant_id=tenant_id,
        day=datetime.now(UTC).date(),
        endpoint_class=endpoint_class,
        count=1,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "day", "endpoint_class"],
        set_={"count": UsageCounter.count + 1},
    )
    await session.execute(stmt)
