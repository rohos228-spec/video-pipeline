"""Загрузка мастер-промтов из БД по ключу (последняя активная версия)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MasterPrompt, PromptKey


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


def render_prompt(template: str, **context: object) -> str:
    """Минимальный шаблонизатор вида {{name}}. Если шаблон не содержит
    плейсхолдеров — возвращаем как есть; пользователь укладывает контент в конец.
    """
    out = template
    for k, v in context.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out
