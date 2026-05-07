"""Шаг 5: генерация референса главного героя (антропоморфного кота).

Только если project.hero_mode in {"hero", "auto+...}. Решение о необходимости
ГГ — в project.hero_needed (проставляется шагом make_plan на основе плана от GPT)
либо в режиме "auto" ориентируемся на флаг hero_mode.

Сейчас: проверяем hero_mode. Если "no_hero" — шаг пропускается, проект сразу
движется к images_ready. В следующей итерации добавим поддержку hero_needed из
plan-вывода, когда вручную доведём парсер.
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
from app.bots.outsee import OutseeBot
from app.generation_options import (
    ASPECT_RATIOS_BY_ID,
    DEFAULTS,
    IMAGE_GENERATORS_BY_ID,
    IMAGE_RESOLUTIONS_BY_ID,
    build_gen_id_prefix,
)
from app.models import (
    Artifact,
    ArtifactKind,
    HITLDecision,
    HITLKind,
    HITLRequest,
    Project,
    ProjectStatus,
)
from app.services.hitl import send_hitl_photo
from app.services.prompt_library import get_project_prompt
from app.settings import settings
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_hero:
        return

    if project.hero_mode == "no_hero" or project.hero_count == 0:
        logger.info(
            "[#{}] hero skipped (hero_mode={}, hero_count={})",
            project.id, project.hero_mode, project.hero_count,
        )
        project.status = ProjectStatus.hero_ready
        return

    # Сколько героев нужно всего и сколько уже одобрено пользователем.
    # Описания лежат в project.hero_descriptions (по списку); генерируем
    # того, чей индекс = approved_count (нумерация с 0).
    descriptions: list[str] = list(project.hero_descriptions or [])
    n_total = project.hero_count or (1 if project.hero_description else 0)
    if n_total == 0:
        # legacy fallback: hero_description есть, hero_count не задан → 1.
        if project.hero_description:
            descriptions = [project.hero_description]
            n_total = 1
        else:
            raise RuntimeError(
                "hero_count=None и hero_description пуст — "
                "нечем описать героя. Тыкни «4. Hero» в меню заново."
            )

    # Считаем одобренные ранее hero-карточки.
    approved_hero_count = (
        await session.execute(
            select(HITLRequest)
            .where(
                HITLRequest.project_id == project.id,
                HITLRequest.kind == HITLKind.approve_hero,
                HITLRequest.decision == HITLDecision.approved,
            )
        )
    ).scalars().all()
    approved_count = len(approved_hero_count)
    if approved_count >= n_total:
        logger.info(
            "[#{}] hero: все {} героев уже одобрены, перехожу к hero_ready",
            project.id, n_total,
        )
        project.status = ProjectStatus.hero_ready
        return

    hero_idx = approved_count + 1  # 1..N: какого героя сейчас делаем
    user_brief = (descriptions[approved_count] if approved_count < len(descriptions) else "").strip()
    if len(user_brief) < 5:
        raise RuntimeError(
            f"hero_descriptions[{approved_count}] пустой — нечем описать "
            f"героя {hero_idx}/{n_total}. Тыкни «4. Hero» в меню заново."
        )

    logger.info(
        "[#{}] generate_hero {}/{} starting (brief: {} симв)",
        project.id, hero_idx, n_total, len(user_brief),
    )

    # Перегенерация (🔁 на последней карточке текущего героя)?
    last_hitl = (
        await session.execute(
            select(HITLRequest)
            .where(
                HITLRequest.project_id == project.id,
                HITLRequest.kind == HITLKind.approve_hero,
            )
            .order_by(desc(HITLRequest.id))
            .limit(1)
        )
    ).scalar_one_or_none()
    is_regen = (
        last_hitl is not None
        and last_hitl.decision is HITLDecision.regenerate
        and (last_hitl.payload or {}).get("hero_index") == hero_idx
    )

    async with browser_session() as bs:
        # Шаблон HERO_SHORTS (turnaround sheet) держим как структурный гайд.
        hero_master = get_project_prompt(project, "hero")
        # Префикс — фиксированная инструкция от пользователя:
        #   "сделай промт для генерации персонажа который описан ниже,
        #    ты должен интегрировать персонажа в промт и прислать готовый
        #    промт для генерации персонажа"
        hero_ask = (
            "Сделай промт для генерации персонажа, который описан ниже. "
            "Ты должен интегрировать персонажа в промт и прислать готовый "
            "промт для генерации персонажа.\n\n"
            "Структура промта (turnaround sheet) — ниже шаблоном. "
            "Подставь в него характеристики персонажа из описания ниже, "
            "верни ТОЛЬКО готовый текст промта (на английском, без кавычек, "
            "без markdown-обрамления, без пояснений).\n\n"
            "Шаблон:\n\n"
            + hero_master
            + "\n\n---\n\nОписание персонажа:\n"
            + user_brief
        )
        gpt = ChatGPTBot(bs)
        hero_prompt = ""
        last_reply = ""
        for attempt in range(1, 3):  # 2 попытки максимум
            reply = await gpt.ask_fresh(hero_ask, timeout=600)
            last_reply = reply or ""
            logger.info(
                "[#{}] hero ChatGPT attempt {}: {} симв",
                project.id,
                attempt,
                len(last_reply),
            )
            logger.info(
                "[#{}] hero ChatGPT preview:\n{}",
                project.id,
                last_reply[:600],
            )
            if last_reply and len(last_reply) >= 100:
                hero_prompt = last_reply.strip()
                break
            logger.warning(
                "[#{}] hero ChatGPT вернул слишком короткий ответ ({} симв), "
                "пробую ещё раз",
                project.id,
                len(last_reply),
            )
        if not hero_prompt:
            raise RuntimeError(
                f"ChatGPT не вернул заполненный hero-промт после 2 попыток. "
                f"Последний ответ ({len(last_reply)} симв): "
                f"{last_reply[:200]!r}"
            )
        logger.info(
            "[#{}] hero final prompt: {} симв (из {} симв описания)",
            project.id,
            len(hero_prompt),
            len(user_brief),
        )

        # 2) генерация референса в outsee — по выбранной юзером модели
        outsee = OutseeBot(bs)
        out_dir = Path(settings.data_dir) / "videos" / project.slug / "characters"
        short_uuid = uuid.uuid4().hex[:8]
        file_name = f"hero_{hero_idx}_{short_uuid}.png"
        out_path = out_dir / file_name
        # Префикс ID для outsee/HITL: P<pid>-HERO<idx>-<hex8>.
        prompt_id_prefix = (
            f"[ID: P{project.id}-HERO{hero_idx}-{short_uuid}]"
        )

        # Настройки из проекта — с дефолтами на случай отсутствия.
        img_gen = IMAGE_GENERATORS_BY_ID.get(
            project.image_generator or DEFAULTS["image_generator"]
        )
        ar = ASPECT_RATIOS_BY_ID.get(
            project.aspect_ratio or DEFAULTS["aspect_ratio"]
        )
        ir = IMAGE_RESOLUTIONS_BY_ID.get(
            project.image_resolution or DEFAULTS["image_resolution"]
        )

        result = None
        if is_regen:
            logger.info(
                "[#{}] regenerate hero: пробую кнопку «Повторить»",
                project.id,
            )
            try:
                result = await outsee.regenerate_image(out_path)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[#{}] «Повторить» не сработала ({}), делаю fresh generate",
                    project.id,
                    e,
                )
                result = None
        if result is None:
            result = await outsee.generate_image(
                hero_prompt,
                out_path,
                aspect_ratio=ar.outsee_slug if ar else "9:16",
                model_slug=img_gen.outsee_slug if img_gen else None,
                resolution=ir.outsee_slug if ir else None,
                relax=bool(project.image_relax),
                prompt_id_prefix=prompt_id_prefix,
            )

    # 3) сохраняем в БД + HITL
    art = Artifact(
        project_id=project.id,
        kind=ArtifactKind.hero_reference,
        uuid=uuid.uuid4().hex,
        path=str(result.file_path),
        meta={"hero_index": hero_idx},
    )
    session.add(art)
    # Статус остаёмся generating_hero — ждём одобрения юзера.
    # Но чтобы воркер не подхватил шаг снова (иначе будет жарить бесконечно),
    # переводим в hero_ready. После approve бот решит: если есть
    # ещё несделанные герои — вернёт в generating_hero.
    project.status = ProjectStatus.hero_ready
    await session.flush()

    try:
        _sheet_for_project(project).write_general(
            status=project.status.value,
            hero_description=project.hero_description,
            hero_image_path=str(result.file_path),
            hero_image_url=result.raw_url,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet hero write failed: {}", project.id, e)

    await send_hitl_photo(
        bot, session, project,
        kind=HITLKind.approve_hero,
        photo_path=str(result.file_path),
        caption=(
            f"{prompt_id_prefix}\n"
            f"Герой {hero_idx}/{n_total} для P{project.id}. Одобрить?"
        ),
        payload={
            "step": "hero",
            "artifact_id": art.id,
            "prompt_id_prefix": prompt_id_prefix,
            "photo_path": str(result.file_path),
            "hero_index": hero_idx,
            "hero_total": n_total,
        },
    )
