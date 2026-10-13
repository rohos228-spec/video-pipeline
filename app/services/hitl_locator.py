"""Где лежит запрос подтверждения (HITL): в project.db проекта или в master.

Запросы проектов создаются в project.db, а старые (до разделения баз) остались в master. У каждой
базы свой счётчик id, поэтому номер запроса сам по себе не однозначен. Правила:

* project.db проекта главнее master: если запрос есть там, решение принимается там (вместе с побочными
  эффектами: статусы кадров и проекта пишутся в ту же базу);
* master используется только для запросов без проектной копии;
* один и тот же номер у разных проектов - явная неоднозначность (``HitlAmbiguous``): пользователь
  должен указать ``project_id``, чужой запрос молча не берётся.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HITLRequest, Project

AMBIGUOUS_RU = ("Номер запроса подтверждения совпал у нескольких проектов: "
                "укажите project_id в запросе")


class HitlAmbiguous(RuntimeError):
    """Один номер запроса найден в разных проектах (или проектная копия не того проекта)."""

    def __init__(self, hitl_id: int, project_ids: list[int | None]):
        self.hitl_id = hitl_id
        self.project_ids = project_ids
        super().__init__(f"{AMBIGUOUS_RU} (запрос {hitl_id}, проекты {project_ids})")


@dataclass(frozen=True)
class HitlLocation:
    hitl_id: int
    project_id: int
    store: str  # "project" | "master"
    data_dir: Path | None


def _project_db_file(data_dir: Path) -> Path | None:
    from app.project_db import resolve_project_db_path
    from app.settings import settings
    try:
        root = Path(settings.data_dir).resolve()
        db = resolve_project_db_path(data_dir)
        if not db.is_file() or not db.resolve().is_relative_to(root):
            return None
        return db
    except (OSError, RuntimeError, ValueError):
        return None


async def locate_hitl(hitl_id: int, project_id: int | None = None) -> HitlLocation | None:
    """Найти, где лежит запрос. Ничего не создаёт и не мигрирует (только чтение)."""
    from app.db import SessionLocal
    from app.project_db import get_project_sessionmaker

    project_hits: list[HitlLocation] = []
    async with SessionLocal() as master:
        query = select(Project)
        if project_id is not None:
            query = query.where(Project.id == project_id)
        projects = (await master.execute(query)).scalars().all()
        master_row = await master.get(HITLRequest, hitl_id)
        master_hit = None
        if master_row is not None and (project_id is None or master_row.project_id == project_id):
            master_hit = HitlLocation(hitl_id, master_row.project_id, "master", None)
        for project in projects:
            try:
                data_dir = Path(project.data_dir)
            except (TypeError, ValueError):
                continue
            if _project_db_file(data_dir) is None:
                continue
            try:
                sessions = await get_project_sessionmaker(data_dir)
                async with sessions() as local:
                    row = await local.get(HITLRequest, hitl_id)
                    if row is not None and row.project_id == project.id:
                        project_hits.append(HitlLocation(hitl_id, project.id, "project", data_dir))
            except Exception as exc:  # noqa: BLE001 - одна повреждённая база не должна скрывать запрос
                logger.warning("hitl locate: project #{} db unreadable: {}", project.id, exc)

    owners = {hit.project_id for hit in project_hits}
    if master_hit is not None:
        owners.add(master_hit.project_id)
    if len(owners) > 1:
        raise HitlAmbiguous(hitl_id, sorted(o for o in owners if o is not None))
    if project_hits:
        return project_hits[0]
    return master_hit


@asynccontextmanager
async def hitl_session_scope(hitl_id: int, project_id: int | None = None
                             ) -> AsyncIterator[tuple[AsyncSession, HitlLocation | None]]:
    """Сессия той базы, где лежит запрос, с коммитом при выходе. Если запроса нет - сессия master."""
    from app.db import session_scope
    from app.project_db import project_session_scope

    location = await locate_hitl(hitl_id, project_id)
    if location is not None and location.store == "project" and location.data_dir is not None:
        async with project_session_scope(location.data_dir) as session:
            yield session, location
        return
    async with session_scope() as session:
        yield session, location


async def mirror_decision_to_master(req: HITLRequest) -> None:
    """Старая копия запроса в master (если есть) получает то же решение, чтобы не висела «ждёт решения»."""
    from sqlalchemy import update

    from app.db import session_scope
    from app.models import HITLDecision
    try:
        async with session_scope() as master:
            await master.execute(
                update(HITLRequest)
                .where(HITLRequest.id == req.id, HITLRequest.project_id == req.project_id,
                       HITLRequest.decision == HITLDecision.pending)
                .values(decision=req.decision, decided_at=req.decided_at, payload=dict(req.payload or {})))
    except Exception as exc:  # noqa: BLE001 - зеркало не должно ломать принятое решение
        logger.warning("hitl #{}: master mirror failed: {}", req.id, exc)
