import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import DatabaseBaseModel


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def test_database_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(DatabaseBaseModel.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(DatabaseBaseModel.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def test_database_session(test_database_engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(
        test_database_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session
