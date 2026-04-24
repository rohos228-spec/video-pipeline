"""DB engine + session factory (SQLite + aiosqlite)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.settings import settings

# Гарантируем, что папка под БД существует — SQLite сам файл создаст, а вот
# родительскую директорию нужно создать заранее.
_db_path = settings.sqlite_path
if not _db_path.is_absolute():
    from pathlib import Path
    _db_path = Path.cwd() / _db_path
_db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(
    settings.db_url,
    echo=False,
    future=True,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
