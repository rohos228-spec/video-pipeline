"""Хранилище кадров проекта для шагов, которые открывают свои сессии (D2).

Кадры, клипы и артефакты проекта, созданного через API, лежат в его ``project.db``; в master их нет. Шаги и фоновые
задачи, которые брали ``app.db.SessionLocal()``, не находили кадров, а результат платной генерации записывали бы в
чужую базу. ``session_factory_for`` отдаёт фабрику сессий нужной базы:

* ``project.db`` проекта - если файл есть и в нём есть строка проекта;
* master (``SessionLocal``) - для старых проектов без ``project.db``;
* ``IdCollisionError`` - если id кадров или клипов совпадают в master и ``project.db`` с разными проектами:
  платный шаг не стартует (строки не перенумеровываются).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from app.models import Project
from app.services.project_db_guard import IdCollisionError, check_project_id_collisions


async def session_factory_for(project_id: int, data_dir: Path | str | None = None) -> Any:
    from app import db as app_db
    from app.project_db import get_project_data_dir, get_project_sessionmaker, resolve_project_db_path

    master = app_db.SessionLocal
    if data_dir is None:
        data_dir = await get_project_data_dir(project_id)
    if not data_dir or not resolve_project_db_path(data_dir).is_file():
        return master
    found = await check_project_id_collisions(project_id, data_dir)
    if found:
        raise IdCollisionError(
            f"проект #{project_id}: id кадров или клипов совпадают в master и project.db "
            f"({len(found)} строк, первая {found[0]}); платный шаг не запускается"
        )
    sm = await get_project_sessionmaker(data_dir)
    async with sm() as ps:
        if await ps.get(Project, project_id) is None:
            return master
    logger.debug("[#{}] frame_store: кадры берутся из project.db проекта", project_id)
    return sm
