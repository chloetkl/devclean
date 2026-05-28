import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import application_settings

database_engine = create_async_engine(
    application_settings.database_url,
    echo=False,
)

async_database_session_factory = async_sessionmaker(
    database_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def initialize_database() -> None:
    from app.models import DatabaseBaseModel

    db_url = application_settings.database_url
    if "sqlite" in db_url:
        db_path = db_url.split("///")[-1]
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    async with database_engine.begin() as connection:
        await connection.run_sync(DatabaseBaseModel.metadata.create_all)


async def get_database_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_database_session_factory() as session:
        yield session
