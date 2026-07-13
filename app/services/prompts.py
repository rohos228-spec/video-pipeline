"""Загрузка мастер-промтов из БД по ключу (последняя активная версия)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MasterPrompt, PromptKey
from app.prompts_loader import _STEP_TO_KEY


async def get_active_prompt(session: AsyncSession, key: PromptKey) -> str:
    row = (
        await session.execute(
            select(MasterPrompt)
            .where(MasterPrompt.key == key, MasterPrompt.active.is_(True))
            .order_by(MasterPrompt.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise RuntimeError(f"master prompt {key.value} not found in DB — did sync_prompts_from_files run?")
    return row.text


async def sync_step_prompt_to_db(
    session: AsyncSession,
    step_code: str,
    content: str,
) -> bool:
    """Обновить MasterPrompt в БД после сохранения файла в студии."""
    key = _STEP_TO_KEY.get(step_code)
    if key is None:
        return False
    existing = (
        await session.execute(
            select(MasterPrompt).where(
                MasterPrompt.key == key, MasterPrompt.version == 1
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(MasterPrompt(key=key, version=1, text=content, active=True))
    else:
        existing.text = content
        existing.active = True
    await session.flush()
    return True


def render_prompt(template: str, **context: object) -> str:
    """Минимальный шаблонизатор вида {{name}}. Если шаблон не содержит
    плейсхолдеров — возвращаем как есть; пользователь укладывает контент в конец.
    """
    out = template
    for k, v in context.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out
