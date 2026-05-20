"""DB engine + session factory (SQLite + aiosqlite).

Включаем WAL + busy_timeout на каждом соединении, иначе при
параллельных писателях (воркер + TG-хендлер на callback'и /
GPT-ревью) ловим `OperationalError: database is locked`.

`journal_mode=WAL`     — читатели не блокируют писателя, писатели
                         не блокируют читателей. Стандарт для
                         SQLite-сервисов с конкурентным доступом.
`busy_timeout=30000`   — если писать всё-таки нельзя (другой писатель
                         держит лок), aiosqlite будет ждать до 30 сек
                         перед тем как кинуть `database is locked`.
                         15 сек оказалось мало на Windows: openpyxl
                         иногда коптит больше при создании topics.xlsx
                         с тяжёлыми data-validation формулами.
`synchronous=NORMAL`   — fsync только в WAL checkpoint'ах, не на
                         каждый commit. Безопасно для WAL, выигрыш
                         по производительности x2-3.
`foreign_keys=ON`      — SQLite по умолчанию выключает FK; включаем,
                         чтобы каскадные удаления / ON DELETE
                         работали как ожидается ORM-ом.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
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
    # 30-секундный таймаут на уровне DB-API. aiosqlite использует это
    # для своего sqlite3-коннекшна (BusyTimeout). Дублирует PRAGMA, но
    # подстраховывает на случай если PRAGMA не успел применится.
    connect_args={"timeout": 30.0},
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
    """Прокручиваем PRAGMA на КАЖДОМ новом соединении. SQLite
    хранит journal_mode и synchronous в файле БД, но `busy_timeout`
    и `foreign_keys` — per-connection, поэтому без этого хука
    параллельные коннекшны ловят `database is locked`."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


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
