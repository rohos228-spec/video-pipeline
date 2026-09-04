"""Зависимости FastAPI: открытие сессии БД."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.project_db import (
    get_project_db_session,
    resolve_edge_project_id,
    resolve_entity_project_id,
    resolve_frame_project_id,
    resolve_prompt_project_id,
    resolve_scene_project_id,
    resolve_text_project_id,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yields AsyncSession к общей базе (data/state.db); коммит в эндпоинтах.

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


async def get_project_session(
    request: Request, project_id: int
) -> AsyncIterator[AsyncSession]:
    """Yields AsyncSession к изолированной БД конкретного проекта (`project.db`).

    При тестировании с моками в `dependency_overrides` прозрачно перенаправляет
    на мок-генератор.
    """
    if get_project_session in request.app.dependency_overrides:
        override = request.app.dependency_overrides[get_project_session]
        if hasattr(override, "__aiter__") or callable(override):
            res = override()
            if hasattr(res, "__aiter__"):
                async for s in res:
                    yield s
                return
            yield res
            return

    if get_session in request.app.dependency_overrides:
        override = request.app.dependency_overrides[get_session]
        if hasattr(override, "__aiter__") or callable(override):
            res = override()
            if hasattr(res, "__aiter__"):
                async for s in res:
                    yield s
                return
            yield res
            return

    async for session in get_project_db_session(project_id):
        yield session


async def get_frame_session(
    request: Request, frame_id: int
) -> AsyncIterator[AsyncSession]:
    """Определяет project_id кадра и открывает соответствующую project.db."""
    if get_session in request.app.dependency_overrides:
        async for s in get_project_session(request, 0):
            yield s
        return

    pid = await resolve_frame_project_id(frame_id)
    if pid is not None:
        async for s in get_project_session(request, pid):
            yield s
        return

    async for s in get_session():
        yield s


async def get_entity_session(
    request: Request, entity_id: int
) -> AsyncIterator[AsyncSession]:
    """Определяет project_id сущности и открывает соответствующую project.db."""
    if get_session in request.app.dependency_overrides:
        async for s in get_project_session(request, 0):
            yield s
        return

    pid = await resolve_entity_project_id(entity_id)
    if pid is not None:
        async for s in get_project_session(request, pid):
            yield s
        return

    async for s in get_session():
        yield s


async def get_scene_session(
    request: Request, scene_id: int
) -> AsyncIterator[AsyncSession]:
    """Определяет project_id сцены и открывает соответствующую project.db."""
    if get_session in request.app.dependency_overrides:
        async for s in get_project_session(request, 0):
            yield s
        return

    pid = await resolve_scene_project_id(scene_id)
    if pid is not None:
        async for s in get_project_session(request, pid):
            yield s
        return

    async for s in get_session():
        yield s


async def get_edge_session(
    request: Request, edge_id: int
) -> AsyncIterator[AsyncSession]:
    """Определяет project_id связи и открывает соответствующую project.db."""
    if get_session in request.app.dependency_overrides:
        async for s in get_project_session(request, 0):
            yield s
        return

    pid = await resolve_edge_project_id(edge_id)
    if pid is not None:
        async for s in get_project_session(request, pid):
            yield s
        return

    async for s in get_session():
        yield s


async def get_prompt_session(
    request: Request, prompt_id: int
) -> AsyncIterator[AsyncSession]:
    """Определяет project_id версии промта и открывает соответствующую project.db."""
    if get_session in request.app.dependency_overrides:
        async for s in get_project_session(request, 0):
            yield s
        return

    pid = await resolve_prompt_project_id(prompt_id)
    if pid is not None:
        async for s in get_project_session(request, pid):
            yield s
        return

    async for s in get_session():
        yield s


async def get_text_session(
    request: Request, text_id: int
) -> AsyncIterator[AsyncSession]:
    """Определяет project_id текста и открывает соответствующую project.db."""
    if get_session in request.app.dependency_overrides:
        async for s in get_project_session(request, 0):
            yield s
        return

    pid = await resolve_text_project_id(text_id)
    if pid is not None:
        async for s in get_project_session(request, pid):
            yield s
        return

    async for s in get_session():
        yield s
