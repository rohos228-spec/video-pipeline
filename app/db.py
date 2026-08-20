"""DB engine + session factory (SQLite + aiosqlite)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.settings import settings

# Гарантируем, что папка под БД существует — SQLite сам файл создаст, а вот
# родительскую директорию нужно создать заранее.
_db_path = settings.sqlite_path
if not _db_path.is_absolute():
    from pathlib import Path

    _db_path = Path.cwd() / _db_path
_db_path.parent.mkdir(parents=True, exist_ok=True)

# NullPool: один writer на файл SQLite — QueuePool давал database is locked
# при concurrent PATCH canvas + worker.
engine = create_async_engine(
    settings.db_url,
    echo=False,
    future=True,
    poolclass=NullPool,
    # Montage / canvas save: запас против busy writer (worker Outsee).
    connect_args={"timeout": 60},
)


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=60000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


event.listens_for(engine.sync_engine, "connect")(_configure_sqlite_connection)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def commit_with_retry(
    session: AsyncSession, *, max_retries: int = 5, base_delay: float = 0.2
) -> None:
    """Commit with retry on sqlite3.OperationalError: database is locked / busy."""
    import asyncio
    from loguru import logger
    from sqlalchemy.exc import OperationalError

    for attempt in range(1, max_retries + 1):
        try:
            await session.commit()
            return
        except OperationalError as e:
            err_msg = str(e).lower()
            if "locked" in err_msg or "busy" in err_msg:
                if attempt < max_retries:
                    delay = base_delay * (1.5 ** (attempt - 1))
                    logger.warning(
                        "db: database is locked on commit (attempt {}/{}) — retry in {:.2f}s",
                        attempt,
                        max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await commit_with_retry(session)
        except Exception:
            await session.rollback()
            raise
