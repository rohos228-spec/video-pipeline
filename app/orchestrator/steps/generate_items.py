"""Шаг 4b «Предметы» — генерация реф-картинок предметов.

Параллельная ветвь шага 4 «Объекты»: если шаг 4a «Персонажи» делает
hero_reference, то 4b делает item_reference. Логика проще, чем у Hero:
без HITL, без вариаций (1 картинка на предмет).

Источник списка предметов: `project.item_descriptions: list[str]`.
По одному непустому описанию = один сгенерированный предмет.
Файлы кладутся в `data/videos/<slug>/items/predmet<N>_<uuid>.png`,
где N — 1-based индекс предмета.

Если шаг падает на каком-то предмете — статус откатывается на
hero_ready (предметы опциональны), юзер правит описание и жмёт
«Предметы» снова.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.bots.outsee import (
    OutseeBot,
    OutseeContentRejectedError,
    OutseeImageError,
)
from app.generation_options import (
    DEFAULTS,
    IMAGE_GENERATORS_BY_ID,
    IMAGE_RESOLUTIONS_BY_ID,
)
from app.models import Artifact, ArtifactKind, Project, ProjectStatus
from app.services.outsee_retry import generate_image_with_retries
from app.services.prompt_library import get_project_prompt
from app.settings import settings

# Aspect ratio и Relax для предметов — как у hero (16:9 + Relax), потому
# что предметы тоже идут как реф-листы.
ITEM_ASPECT_RATIO = "16:9"
ITEM_RELAX = True


async def _existing_item_indices(
    session: AsyncSession, project: Project
) -> set[int]:
    """Какие индексы предметов уже имеют артефакт kind=item_reference."""
    rows = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.kind == ArtifactKind.item_reference,
            )
            .order_by(desc(Artifact.id))
        )
    ).scalars().all()
    out: set[int] = set()
    for a in rows:
        m = a.meta or {}
        idx = m.get("item_index")
        if isinstance(idx, int):
            out.add(idx)
    return out


def _items_style_prompt(project: Project) -> str:
    """Мастер-промт для генерации предметов (prompts/04b_items/<name>.md).
    Если ничего не выбрано/нет файла — пустая строка (юзер должен
    положить хотя бы default.md)."""
    try:
        return get_project_prompt(project, "items").strip()
    except FileNotFoundError:
        logger.warning(
            "items: prompts/04b_items/default.md не найден — генерирую "
            "только из описаний без стиля"
        )
        return ""


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_items:
        return

    descriptions: list[str] = list(project.item_descriptions or [])
    descriptions = [d.strip() for d in descriptions if isinstance(d, str)]
    descriptions = [d for d in descriptions if d]
    if not descriptions:
        logger.info(
            "[#{}] items: item_descriptions пуст — items_ready без работы",
            project.id,
        )
        project.status = ProjectStatus.items_ready
        await session.flush()
        return

    style = _items_style_prompt(project)
    img_gen = IMAGE_GENERATORS_BY_ID.get(
        project.image_generator or DEFAULTS["image_generator"]
    )
    ir = IMAGE_RESOLUTIONS_BY_ID.get(
        project.image_resolution or DEFAULTS["image_resolution"]
    )

    already_done = await _existing_item_indices(session, project)
    out_dir = project.data_dir / "items"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Идём по предметам последовательно, пропускаем уже сгенерированные.
    for idx, desc_text in enumerate(descriptions, start=1):
        if idx in already_done:
            logger.info(
                "[#{}] items: predmet{} уже есть, пропускаю",
                project.id,
                idx,
            )
            continue
        logger.info(
            "[#{}] items: предмет {}/{} — '{}'",
            project.id,
            idx,
            len(descriptions),
            desc_text[:60],
        )

        full_prompt = (
            (style + "\n\n---\n\n" if style else "")
            + f"Описание предмета (predmet{idx}): {desc_text}"
        )

        short_uuid = uuid.uuid4().hex[:8]
        file_name = f"predmet{idx}_{short_uuid}.png"
        out_path = out_dir / file_name
        prompt_id_prefix = f"[ID: P{project.id}-ITEM{idx}-{short_uuid}]"

        try:
            async with browser_session() as bs:
                outsee = OutseeBot(bs)
                gpt = ChatGPTBot(bs)
                result = await generate_image_with_retries(
                    outsee, gpt,
                    prompt=full_prompt,
                    out_path=out_path,
                    max_attempts_per_prompt=3,
                    gpt_rewrite=True,
                    aspect_ratio=ITEM_ASPECT_RATIO,
                    model_slug=img_gen.outsee_slug if img_gen else None,
                    resolution=ir.outsee_slug if ir else None,
                    relax=ITEM_RELAX,
                    prompt_id_prefix=prompt_id_prefix,
                    reference_image=None,
                    timeout=600,
                )
        except OutseeImageError as e:
            is_moderation = isinstance(e, OutseeContentRejectedError)
            logger.error(
                "[#{}] items: predmet{} 6 попыток provalились "
                "(moderation={}): {}",
                project.id, idx, is_moderation,
                getattr(e, "reason", None) or str(e),
            )
            # Откат на hero_ready: предметы опциональны, юзер может
            # пропустить и идти дальше.
            project.status = ProjectStatus.hero_ready
            await session.flush()
            raise RuntimeError(
                f"items: predmet{idx} не удалось сгенерить "
                f"(см. логи). Статус откатил на hero_ready — поправь "
                f"описание предмета и жми «Предметы» снова."
            ) from e

        # Сохраняем артефакт.
        a = Artifact(
            project_id=project.id,
            frame_id=None,
            kind=ArtifactKind.item_reference,
            uuid=uuid.uuid4().hex,
            path=str(result.image_path),
            meta={
                "item_index": idx,
                "item_id": f"predmet{idx}",
                "description": desc_text,
                "prompt": full_prompt,
            },
        )
        session.add(a)
        await session.flush()
        logger.info(
            "[#{}] items: predmet{} → {}",
            project.id,
            idx,
            result.image_path,
        )

    # Все предметы готовы.
    project.status = ProjectStatus.items_ready
    await session.flush()
    logger.info(
        "[#{}] items: все {} предметов готовы → items_ready",
        project.id,
        len(descriptions),
    )
