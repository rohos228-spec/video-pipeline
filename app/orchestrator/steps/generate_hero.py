"""Шаг 4 (Hero): генерация референса главного героя — с поддержкой
вариаций и обязательным «стилем персонажа» (мастер-промт из
prompts/04_hero_style/).

Контракт работы шага:

  Один вызов `run(...)` обрабатывает РОВНО одного следующего
  неодобренного героя — от индекса 1..N. Внутри он генерит все его
  вариации (от 1 до 5) и присылает в TG все промежуточные кадры; HITL
  (✅/🔁/❌) вешается только на ПОСЛЕДНЮЮ вариацию — она содержит
  payload['hero_index'] = i. После 🔁 (regenerate) шаг перегенерит того
  же героя целиком (все вариации заново). После ✅ — переходит к
  следующему герою (если есть) или ставит status=hero_ready.

  В hero_descriptions[i-1] лежит человекописанное описание героя i.
  В hero_variations[i-1]   лежит кол-во вариаций (1..5) для героя i.
  В overrides['hero_style'] лежит имя пресета стиля
  (prompts/04_hero_style/<name>.md). Содержимое стиля приклеивается к
  промту при сборке hero_ask и идёт в ChatGPT, а потом в outsee.

  Переменная reference_image для outsee.generate_image —
  Path к первой сгенерированной вариации; передаётся для вариаций 2..N.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile
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
from app.services.prompt_library import (
    get_project_prompt,
    prompt_path,
    resolve_project_prompt_name,
)
from app.settings import settings
from app.storage import for_project as _sheet_for_project


async def _send_outsee_dumps(
    bot: Bot,
    chat_id: int,
    dumps: list[Path],
    *,
    caption_prefix: str,
) -> None:
    """Отправляет в TG dump-файлы (html/png) outsee-страницы. Используется
    для отладки селекторов: если на странице outsee.io не нашлась нужная
    кнопка (aspect/relax/Generate), хелпер `_dump_page` сохраняет HTML +
    скриншот, мы их сюда складываем — и юзер пересылает разработчику."""
    for path in dumps:
        try:
            if not path.exists():
                continue
            ext = path.suffix.lower()
            cap = f"{caption_prefix}\n<code>{path.name}</code>"
            if ext == ".png":
                await bot.send_photo(
                    chat_id, FSInputFile(str(path)),
                    caption=cap, parse_mode="HTML",
                )
            else:
                await bot.send_document(
                    chat_id, FSInputFile(str(path)),
                    caption=cap, parse_mode="HTML",
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("отправка dump {} в TG упала: {}", path, e)


def _read_hero_style(project: Project) -> str | None:
    """Возвращает содержимое выбранного для проекта пресета стиля
    из prompts/04_hero_style/. Если стиль не задан или файл отсутствует
    — возвращает None (вызывающий должен решить, как фоллбэчить)."""
    overrides = getattr(project, "prompt_overrides", None) or {}
    name = resolve_project_prompt_name(overrides, "hero_style")
    p = prompt_path("hero_style", name)
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] hero_style read failed ({}): {}", project.id, p, e
        )
        return None


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
    descriptions: list[str] = list(project.hero_descriptions or [])
    variations_cfg: list[int] = list(project.hero_variations or [])
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
    approved_hero = (
        await session.execute(
            select(HITLRequest)
            .where(
                HITLRequest.project_id == project.id,
                HITLRequest.kind == HITLKind.approve_hero,
                HITLRequest.decision == HITLDecision.approved,
            )
        )
    ).scalars().all()
    approved_count = len(approved_hero)
    if approved_count >= n_total:
        logger.info(
            "[#{}] hero: все {} героев уже одобрены, перехожу к hero_ready",
            project.id, n_total,
        )
        project.status = ProjectStatus.hero_ready
        return

    hero_idx = approved_count + 1  # 1..N: какого героя сейчас делаем
    user_brief = (
        descriptions[approved_count]
        if approved_count < len(descriptions) else ""
    ).strip()
    if len(user_brief) < 5:
        raise RuntimeError(
            f"hero_descriptions[{approved_count}] пустой — нечем описать "
            f"героя {hero_idx}/{n_total}. Тыкни «4. Hero» в меню заново."
        )

    # Сколько вариаций сделать для этого героя. Дефолт = 1 (одна
    # картинка без референса), но юзер мог выбрать 1..5 в TG.
    n_variations = 1
    if approved_count < len(variations_cfg):
        try:
            n_variations = int(variations_cfg[approved_count] or 1)
        except (TypeError, ValueError):
            n_variations = 1
    n_variations = max(1, min(5, n_variations))

    # Текстовые «отличия» для вариаций 2..N этого героя — приклеиваем
    # к промту вариации, чтобы outsee знал что менять (поза/ракурс/etc.).
    # variation_mods_for_hero[j-2] = текст отличия для вариации j (j∈2..N).
    modifiers_all = list(getattr(project, "hero_variation_modifiers", None) or [])
    variation_mods_for_hero: list[str] = []
    if approved_count < len(modifiers_all):
        raw = modifiers_all[approved_count] or []
        if isinstance(raw, list):
            variation_mods_for_hero = [str(x or "").strip() for x in raw]

    logger.info(
        "[#{}] generate_hero {}/{} starting "
        "(brief: {} симв, вариаций: {})",
        project.id, hero_idx, n_total, len(user_brief), n_variations,
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

    # Стиль персонажа (мастер-промт из prompts/04_hero_style/) —
    # обязательно подмешивается к ChatGPT-промту, чтобы итоговое
    # изображение было в нужном визуале (фото-реализм / аниме / 3D / etc).
    hero_style_content = _read_hero_style(project)
    style_chosen = (
        getattr(project, "prompt_overrides", None) or {}
    ).get("hero_style") or "default"
    if not hero_style_content:
        # Hard fallback: текст-плейсхолдер. Не падаем — но логируем.
        logger.warning(
            "[#{}] hero_style '{}' не найден на диске — продолжаю без стиля",
            project.id, style_chosen,
        )
        hero_style_content = ""

    async with browser_session() as bs:
        # Шаблон HERO_SHORTS (turnaround sheet) держим как структурный гайд.
        hero_master = get_project_prompt(project, "hero")
        hero_ask = (
            "Сделай промт для генерации персонажа, который описан ниже. "
            "Ты должен интегрировать персонажа в промт и прислать готовый "
            "промт для генерации персонажа.\n\n"
            "Структура промта (turnaround sheet) — ниже шаблоном. "
            "Подставь в него характеристики персонажа из описания ниже, "
            "верни ТОЛЬКО готовый текст промта (на английском, без кавычек, "
            "без markdown-обрамления, без пояснений).\n\n"
            "ВАЖНО: ОБЯЗАТЕЛЬНО учитывай блок «Visual style» ниже — он "
            "описывает визуальный стиль (рендер, освещение, lens, цвет). "
            "Эти инструкции должны быть отражены в финальном промте — "
            "никакого «default» style; используем именно этот блок.\n\n"
            "ЛИМИТ: финальный промт должен быть НЕ ДЛИННЕЕ 5000 "
            "символов (включая пробелы). Если получается длиннее — "
            "сожми описание, убери дубликаты, оставь только самое "
            "важное. Главное чтобы влезло в 5000.\n\n"
            "Шаблон:\n\n"
            + hero_master
            + "\n\n---\n\nVisual style (применять обязательно):\n"
            + (hero_style_content or "(не задан — используй кинематографический фото-реализм)")
            + "\n\n---\n\nОписание персонажа:\n"
            + user_brief
        )
        gpt = ChatGPTBot(bs)
        hero_prompt = ""
        last_reply = ""
        # Лимит длины промта в outsee.io. Если ChatGPT нагенерит больше —
        # просим его сжать ещё одной попыткой.
        OUTSEE_PROMPT_MAX = 5000
        for attempt in range(1, 4):  # до 3 попыток
            ask = hero_ask
            if attempt > 1 and last_reply and len(last_reply) > OUTSEE_PROMPT_MAX:
                # Целевая попытка сжатия — подаём прошлый ответ и просим уплотнить.
                ask = (
                    f"Прошлый ответ был {len(last_reply)} символов — это "
                    f"больше лимита {OUTSEE_PROMPT_MAX}. Сожми его до "
                    f"≤{OUTSEE_PROMPT_MAX} символов: убери повторы, "
                    "объедини похожие пункты, оставь самое важное. "
                    "Структуру (turnaround sheet) сохрани. Верни ТОЛЬКО "
                    "новый сокращённый промт, без пояснений.\n\n"
                    "Прошлый промт:\n\n" + last_reply
                )
            reply = await gpt.ask_fresh(ask, timeout=600)
            last_reply = reply or ""
            logger.info(
                "[#{}] hero ChatGPT attempt {}: {} симв",
                project.id, attempt, len(last_reply),
            )
            logger.info(
                "[#{}] hero ChatGPT preview:\n{}",
                project.id, last_reply[:600],
            )
            if not last_reply or len(last_reply) < 100:
                logger.warning(
                    "[#{}] hero ChatGPT вернул слишком короткий ответ "
                    "({} симв), пробую ещё раз",
                    project.id, len(last_reply),
                )
                continue
            hero_prompt = last_reply.strip()
            if len(hero_prompt) <= OUTSEE_PROMPT_MAX:
                break
            logger.warning(
                "[#{}] hero ChatGPT вернул {} симв (лимит {}), "
                "прошу сжать",
                project.id, len(hero_prompt), OUTSEE_PROMPT_MAX,
            )
        if not hero_prompt:
            raise RuntimeError(
                f"ChatGPT не вернул заполненный hero-промт после 3 попыток. "
                f"Последний ответ ({len(last_reply)} симв): "
                f"{last_reply[:200]!r}"
            )
        if len(hero_prompt) > OUTSEE_PROMPT_MAX:
            logger.warning(
                "[#{}] hero prompt всё ещё длиннее лимита: {} > {} — "
                "отправляю как есть, outsee может не принять",
                project.id, len(hero_prompt), OUTSEE_PROMPT_MAX,
            )
        # На вариациях 2..N подмешаем явный «keep same character» хинт —
        # outsee всё равно увидит референс, но текст в промте усилит сигнал.
        hero_prompt_main = hero_prompt
        hero_prompt_with_ref = (
            "[REFERENCE: keep the EXACT SAME character from the attached "
            "image — same face, hair, body. Different pose / angle / outfit "
            "is fine, but identity must match.]\n\n"
            + hero_prompt
        )
        logger.info(
            "[#{}] hero final prompt: {} симв (style='{}', "
            "вариаций будет {})",
            project.id, len(hero_prompt), style_chosen, n_variations,
        )

        # 2) Генерация вариаций в outsee.
        outsee = OutseeBot(bs)
        out_dir = Path(settings.data_dir) / "videos" / project.slug / "characters"

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

        # Будущая обработка: соберём все результаты, пошлём в TG, потом
        # на последний вариант повесим HITL.
        variation_results: list[tuple[int, Path, object]] = []
        first_variant_path: Path | None = None
        chat_id = settings.telegram_owner_chat_id

        for v_idx in range(1, n_variations + 1):
            short_uuid = uuid.uuid4().hex[:8]
            file_name = f"hero_{hero_idx}_v{v_idx}_{short_uuid}.png"
            out_path = out_dir / file_name
            prompt_id_prefix = (
                f"[ID: P{project.id}-HERO{hero_idx}-V{v_idx}-{short_uuid}]"
            )

            ref_for_this = (
                first_variant_path if v_idx > 1 and first_variant_path else None
            )
            base_prompt_for_v = (
                hero_prompt_with_ref if ref_for_this else hero_prompt_main
            )
            # Для вариаций 2..N приклеиваем юзерский «текст отличий».
            modifier_text = ""
            if v_idx >= 2:
                idx_in_mods = v_idx - 2
                if 0 <= idx_in_mods < len(variation_mods_for_hero):
                    modifier_text = variation_mods_for_hero[idx_in_mods]
            if modifier_text:
                prompt_text = (
                    f"{base_prompt_for_v}\n\n"
                    f"[VARIATION {v_idx} CHANGES — keep the SAME character "
                    f"from the reference, but change the following per "
                    f"user request:]\n{modifier_text}"
                )
            else:
                prompt_text = base_prompt_for_v

            result = None
            if v_idx == 1 and is_regen:
                logger.info(
                    "[#{}] regenerate hero {}/{} v1: пробую кнопку «Повторить»",
                    project.id, hero_idx, n_total,
                )
                try:
                    result = await outsee.regenerate_image(out_path)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "[#{}] «Повторить» не сработала ({}), делаю fresh "
                        "generate", project.id, e,
                    )
                    result = None

            if result is None:
                try:
                    result = await outsee.generate_image(
                        prompt_text,
                        out_path,
                        aspect_ratio=ar.outsee_slug if ar else "9:16",
                        model_slug=img_gen.outsee_slug if img_gen else None,
                        resolution=ir.outsee_slug if ir else None,
                        relax=bool(project.image_relax),
                        prompt_id_prefix=prompt_id_prefix,
                        reference_image=ref_for_this,
                        # Короткий таймаут (180c) — если outsee не запустил
                        # генерацию, лучше быстро упасть с дампом, чем
                        # висеть 10 минут.
                        timeout=180,
                    )
                except Exception as e:
                    # Если outsee упал и приложил dump-файлы (html/png страницы)
                    # — отправляем их в TG, чтобы можно было быстро поправить
                    # селекторы. Затем перебрасываем исключение дальше.
                    dumps = list(getattr(e, "dumps", None) or [])
                    if dumps:
                        await _send_outsee_dumps(
                            bot, chat_id, dumps,
                            caption_prefix=(
                                f"Герой {hero_idx}/{n_total} v{v_idx}: "
                                "outsee упал, дамп страницы для отладки"
                            ),
                        )
                    raise

            # Если генерация прошла, но по дороге что-то не нашлось
            # (aspect / relax / etc.) — отправляем dumps в TG для отладки.
            res_dumps = list(getattr(result, "dumps", None) or [])
            if res_dumps:
                await _send_outsee_dumps(
                    bot, chat_id, res_dumps,
                    caption_prefix=(
                        f"Герой {hero_idx}/{n_total} v{v_idx}: "
                        "выявлены проблемы с UI outsee, см. дамп"
                    ),
                )

            variation_results.append((v_idx, Path(result.file_path), result))
            if v_idx == 1:
                first_variant_path = Path(result.file_path)

            # Промежуточные вариации (не последняя) — отправляем как
            # обычное фото без HITL-кнопок, чтобы юзер видел прогресс.
            if v_idx < n_variations:
                try:
                    await bot.send_photo(
                        chat_id,
                        FSInputFile(str(result.file_path)),
                        caption=(
                            f"Герой {hero_idx}/{n_total}, "
                            f"вариация {v_idx}/{n_variations}\n"
                            f"{prompt_id_prefix}"
                        ),
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "[#{}] hero v{} preview send failed: {}",
                        project.id, v_idx, e,
                    )

    # 3) Сохраняем артефакты в БД (по одному на вариацию).
    last_artifact = None
    for v_idx, file_path, _res in variation_results:
        art = Artifact(
            project_id=project.id,
            kind=ArtifactKind.hero_reference,
            uuid=uuid.uuid4().hex,
            path=str(file_path),
            meta={
                "hero_index": hero_idx,
                "variation_index": v_idx,
                "variations_total": n_variations,
            },
        )
        session.add(art)
        last_artifact = art
    # После последней вариации статус временно переводим в hero_ready —
    # ждём решение HITL. После approve бот или вернёт generating_hero
    # (если есть ещё герои), или оставит hero_ready (если все готовы).
    project.status = ProjectStatus.hero_ready
    await session.flush()

    # 4) Записываем в xlsx последнюю вариацию (как «итоговый» референс
    # героя для дальнейших шагов).
    final_path = variation_results[-1][1]
    final_result = variation_results[-1][2]
    try:
        _sheet_for_project(project).write_general(
            status=project.status.value,
            hero_description=user_brief,
            hero_image_path=str(final_path),
            hero_image_url=getattr(final_result, "raw_url", None),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet hero write failed: {}", project.id, e)

    # 5) HITL-карточка на последнюю вариацию.
    last_v_idx = n_variations
    short_uuid_final = uuid.uuid4().hex[:8]
    final_prompt_id_prefix = (
        f"[ID: P{project.id}-HERO{hero_idx}-V{last_v_idx}-{short_uuid_final}]"
    )
    await send_hitl_photo(
        bot, session, project,
        kind=HITLKind.approve_hero,
        photo_path=str(final_path),
        caption=(
            f"{final_prompt_id_prefix}\n"
            f"Герой {hero_idx}/{n_total}, "
            f"финальная вариация {last_v_idx}/{n_variations}.\n"
            f"Стиль: {style_chosen}\n"
            f"Одобрить? (✅ — принять все вариации этого героя; "
            f"🔁 — перегенерить ВСЕ вариации заново.)"
        ),
        payload={
            "step": "hero",
            "artifact_id": last_artifact.id if last_artifact else None,
            "prompt_id_prefix": final_prompt_id_prefix,
            "photo_path": str(final_path),
            "hero_index": hero_idx,
            "hero_total": n_total,
            "variations_total": n_variations,
            "hero_style": style_chosen,
        },
    )
