"""Зависимости FastAPI: открытие сессии БД."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yields AsyncSession; коммит происходит в каждом эндпоинте при необходимости.

    Откатываем только при исключении.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
