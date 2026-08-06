"""In CI the corpus store must be present - a broken restore fails loudly.

Locally the store may legitimately be absent, so the guard skips when
the CI environment variable (set by GitHub Actions) is missing.
"""

import os

import pytest
from sqlalchemy import select


async def test_corpus_store_available_in_ci():
    if not os.environ.get("CI"):
        pytest.skip("guard applies only in CI")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.models import Act

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        slugs = {act.slug for act in (await session.execute(select(Act))).scalars()}
    await engine.dispose()
    assert {
        "act-2010-042",
        "sl-2019-0629",
        "residential-tenancies-act-1997",
        "residential-tenancies-regulations-2021",
    } <= slugs
