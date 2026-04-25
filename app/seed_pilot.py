"""Создаёт пилотный проект в БД, чтобы быстро проверить весь конвейер:
  тема: «5 фактов о рачках в стиле киберпанк», hero_mode=auto.

Запуск:
    python -m app.seed_pilot

После этого запусти:
    python -m app.main
и воркер автоматически подхватит этот проект и поведёт его по этапам,
присылая тебе HITL-запросы в Telegram.
"""

from __future__ import annotations

import asyncio
import re

from loguru import logger
from sqlalchemy import select

from app.db import engine, session_scope
from app.models import Base, Project, ProjectStatus
from app.settings import settings
from app.storage import ProjectSheet

DEFAULT_TOPIC = "5 фактов о рачках в стиле киберпанк"
DEFAULT_HERO_MODE = "auto"


def _slugify(text: str) -> str:
    t = text.lower()
    t = re.sub(r"[^a-zа-я0-9]+", "-", t, flags=re.IGNORECASE)
    t = re.sub(r"-+", "-", t).strip("-")
    # простая транслитерация для кириллицы, чтобы путь был ASCII-только
    table = str.maketrans(
        {
            "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
            "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
            "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
            "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
            "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
        }
    )
    t = t.translate(table)
    t = re.sub(r"[^a-z0-9-]+", "-", t)
    t = re.sub(r"-+", "-", t).strip("-")
    return t[:60] or "pilot"


async def _init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def seed(topic: str = DEFAULT_TOPIC, hero_mode: str = DEFAULT_HERO_MODE) -> int:
    await _init_db()
    base_slug = _slugify(topic)
    async with session_scope() as s:
        # Уникализируем slug, если пилот уже запускался.
        slug = base_slug
        n = 1
        while True:
            exists = (
                await s.execute(select(Project).where(Project.slug == slug))
            ).scalar_one_or_none()
            if exists is None:
                break
            n += 1
            slug = f"{base_slug}-{n}"
        p = Project(
            slug=slug,
            topic=topic,
            hero_mode=hero_mode,
            # `new` — проект ждёт первого клика «Запустить шаг 1» в TG-меню.
            status=ProjectStatus.new,
        )
        s.add(p)
        await s.flush()
        logger.info("pilot project created: #{} slug={} topic={}", p.id, p.slug, p.topic)

        # xlsx-хранилище: копия шаблона + общий план
        sheet = ProjectSheet(
            file_path=settings.data_dir / "videos" / p.slug / "project.xlsx",
        )
        sheet.ensure_initialized(project_id=p.id, slug=p.slug)
        sheet.write_general(
            topic=p.topic,
            slug=p.slug,
            hero_mode=p.hero_mode,
            status=p.status.value,
        )
        return p.id


def main() -> None:
    import sys

    topic = DEFAULT_TOPIC
    hero_mode = DEFAULT_HERO_MODE
    if len(sys.argv) > 1:
        topic = " ".join(sys.argv[1:])
    pid = asyncio.run(seed(topic=topic, hero_mode=hero_mode))
    print(f"Pilot project #{pid} created. Запусти 'python -m app.main' — воркер начнёт его прогонять.")


if __name__ == "__main__":
    main()
