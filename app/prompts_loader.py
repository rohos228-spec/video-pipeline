"""Подгрузка дефолтных мастер-промтов из файлов + апсерт в БД.

Новая структура `prompts/`:
  prompts/01_plan/default.md, prompts/02_script/default.md, …

Этот загрузчик берёт `default.md` из каждой папки и кладёт в таблицу
`MasterPrompt` по соответствующему `PromptKey`. Это нужно только для
обратной совместимости со старыми вызовами `get_active_prompt(...)`.
Реальные шаги пайплайна сейчас читают мастер-промты с диска через
`app.services.prompt_library.get_project_prompt(...)`.

Если файл не найден — пропускаем (логируем warning), не падаем."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import select

from app.db import session_scope
from app.models import MasterPrompt, PromptKey
from app.services.prompt_library import PROMPTS_ROOT, STEP_FOLDERS

# Карта step_code → PromptKey (для апсерта в DB).
_STEP_TO_KEY: dict[str, PromptKey] = {
    "plan":    PromptKey.PLAN_SHORTS,
    "script":  PromptKey.SCRIPT_SHORTS,
    "split":   PromptKey.RAZBIVKA_SLOV,
    "hero":    PromptKey.HERO_SHORTS,
    "img_pr":  PromptKey.IMAGE_SHORTS,
    "anim_pr": PromptKey.VIDEO_SHORTS,
}


def _default_path(step_code: str) -> Path:
    folder = STEP_FOLDERS[step_code]
    return PROMPTS_ROOT / folder / "default.md"


async def sync_prompts_from_files() -> None:
    """Апсерт `prompts/<этап>/default.md` в БД как версии 1 для каждого ключа.

    Если файла нет — пропускаем. Текст уже в БД с такой же версией —
    обновляем содержимое (так удобно править default.md и видеть текст
    в DB). Это legacy-путь; новые шаги читают с диска напрямую.
    """
    async with session_scope() as s:
        for step_code, key in _STEP_TO_KEY.items():
            p = _default_path(step_code)
            if not p.exists():
                logger.warning(
                    "prompts_loader: файл не найден, пропускаю: {}", p
                )
                continue
            text = p.read_text(encoding="utf-8")
            existing = (
                await s.execute(
                    select(MasterPrompt).where(
                        MasterPrompt.key == key, MasterPrompt.version == 1
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                s.add(MasterPrompt(key=key, version=1, text=text, active=True))
            else:
                existing.text = text
                existing.active = True
