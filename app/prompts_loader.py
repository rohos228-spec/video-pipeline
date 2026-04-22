"""Подгрузка мастер-промтов из файлов + апсерт в БД."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.db import session_scope
from app.models import MasterPrompt, PromptKey

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


_FILE_TO_KEY: dict[str, PromptKey] = {
    "PLAN_SHORTS.v1.md": PromptKey.PLAN_SHORTS,
    "SCRIPT_SHORTS.v1.md": PromptKey.SCRIPT_SHORTS,
    "IMAGE_SHORTS.v1.md": PromptKey.IMAGE_SHORTS,
    "VIDEO_SHORTS.v1.md": PromptKey.VIDEO_SHORTS,
}


async def sync_prompts_from_files() -> None:
    """Апсерт всех мастер-промтов из `prompts/` как версии 1.

    Если одноимённый ключ уже есть в БД с той же версией — не трогаем.
    При обновлении файла нужно увеличивать версию (PLAN_SHORTS.v2.md и т.д.).
    """
    async with session_scope() as s:
        for fname, key in _FILE_TO_KEY.items():
            p = PROMPTS_DIR / fname
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8")
            # парсим версию из имени: NAME.v{N}.md
            version = 1
            stem = p.stem
            if ".v" in stem:
                try:
                    version = int(stem.rsplit(".v", 1)[1])
                except ValueError:
                    version = 1
            existing = (
                await s.execute(
                    select(MasterPrompt).where(
                        MasterPrompt.key == key, MasterPrompt.version == version
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                s.add(MasterPrompt(key=key, version=version, text=text, active=True))
            else:
                existing.text = text
                existing.active = True
