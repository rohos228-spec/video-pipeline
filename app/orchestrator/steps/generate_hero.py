"""Шаг 4 (Hero): генерация референса главного героя — с поддержкой
вариаций и обязательным «стилем персонажа» (мастер-промт из
prompts/04_hero_style/).

Контракт работы шага (per-variation HITL):

  Один вызов `run(...)` обрабатывает РОВНО ОДНУ пару
  (hero_index, variation_index) — следующую неодобренную в порядке
  обхода (h=1..N, v=1..K_h). Каждая вариация попадает в TG отдельным
  фото с HITL-кнопками (✅/🔁/❌). После ✅ воркер вернётся в
  generating_hero и сделает следующую вариацию (или следующего героя,
  или поставит hero_ready). После 🔁 перегенерит ТУ ЖЕ вариацию.

  В hero_descriptions[i-1] лежит человекописанное описание героя i.
  В hero_variations[i-1]   лежит кол-во вариаций (1..5) для героя i.
  В hero_variation_modifiers[i-1][j-2] — текст «отличий» для вариации
  j∈[2..K_h] героя i (что менять относительно вариации 1).
  В overrides['hero_style'] лежит имя пресета стиля
  (prompts/04_hero_style/<name>.md).

  Промт от ChatGPT генерится ОДИН РАЗ на героя — на v=1 — и кешится в
  meta артефакта. Вариации 2..K_h берут его из meta v=1 и приклеивают
  к нему «текст отличий» + reference_image=v=1.png.
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
from app.services import gpt_text_builder as gtb
from app.services.hitl import send_hitl_photo
from app.services.outsee_retry import generate_image_with_retries
from app.services.prompt_library import (
    prompt_path,
    resolve_project_prompt_name,
)
from app.settings import settings
from app.storage import for_project as _sheet_for_project

# Hero turnaround sheet всегда генерится в ландшафтном 16:9 и с Relax=ON,
# НЕЗАВИСИМО от настроек проекта (которые под shorts — 9:16 и
# обычно Relax=OFF). Причина: референсный лист «стороны/выражения»
# влезает в 16:9, а Relax при этом дёшевле и идёт без очереди.
HERO_ASPECT_RATIO = "16:9"
HERO_RELAX = True


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


def _hero_target_pairs(
    n_total: int, variations_cfg: list[int]
) -> list[tuple[int, int]]:
    """Список всех пар (hero_idx, var_idx) в порядке обхода: сначала
    все вариации героя 1, потом героя 2, и т.д. Каждая вариация —
    отдельная HITL-карточка."""
    pairs: list[tuple[int, int]] = []
    for hi in range(1, n_total + 1):
        n_var = 1
        if hi - 1 < len(variations_cfg):
            try:
                n_var = int(variations_cfg[hi - 1] or 1)
            except (TypeError, ValueError):
                n_var = 1
        n_var = max(1, min(5, n_var))
        for vi in range(1, n_var + 1):
            pairs.append((hi, vi))
    return pairs


async def _approved_pairs(
    session: AsyncSession, project: Project
) -> set[tuple[int, int]]:
    """Множество (hero_idx, var_idx), для которых уже есть одобренная
    HITL-карточка. variation_index по умолчанию 1 (legacy)."""
    rows = (
        await session.execute(
            select(HITLRequest)
            .where(
                HITLRequest.project_id == project.id,
                HITLRequest.kind == HITLKind.approve_hero,
                HITLRequest.decision == HITLDecision.approved,
            )
        )
    ).scalars().all()
    out: set[tuple[int, int]] = set()
    for r in rows:
        p = r.payload or {}
        hi = p.get("hero_index")
        vi = p.get("variation_index", 1)
        if isinstance(hi, int) and isinstance(vi, int):
            out.add((hi, vi))
    return out


async def _is_regen_for_pair(
    session: AsyncSession,
    project: Project,
    hero_idx: int,
    var_idx: int,
) -> bool:
    """True если ПОСЛЕДНЯЯ HITL-карточка для (hero_idx, var_idx) —
    regenerate. Используется чтобы понять «эту вариацию надо передать
    через кнопку Повторить» (для v=1) вместо fresh generate."""
    rows = (
        await session.execute(
            select(HITLRequest)
            .where(
                HITLRequest.project_id == project.id,
                HITLRequest.kind == HITLKind.approve_hero,
            )
            .order_by(desc(HITLRequest.id))
        )
    ).scalars().all()
    for r in rows:
        p = r.payload or {}
        if p.get("hero_index") == hero_idx and p.get(
            "variation_index", 1
        ) == var_idx:
            return r.decision is HITLDecision.regenerate
    return False


async def _v1_artifact_for_hero(
    session: AsyncSession, project: Project, hero_idx: int
) -> Artifact | None:
    """Возвращает САМЫЙ СВЕЖИЙ артефакт v=1 для данного героя (то, что
    мы будем использовать как reference для v>=2 и брать оттуда
    закешированный hero_prompt)."""
    rows = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.kind == ArtifactKind.hero_reference,
            )
            .order_by(desc(Artifact.id))
        )
    ).scalars().all()
    for a in rows:
        m = a.meta or {}
        if (
            m.get("hero_index") == hero_idx
            and m.get("variation_index") == 1
        ):
            return a
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

    # Конфиг героев из проекта.
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

    # Определяем СЛЕДУЮЩУЮ пару (hero_idx, var_idx), которую надо сделать.
    target_pairs = _hero_target_pairs(n_total, variations_cfg)
    approved = await _approved_pairs(session, project)
    target: tuple[int, int] | None = None
    for pair in target_pairs:
        if pair not in approved:
            target = pair
            break
    if target is None:
        logger.info(
            "[#{}] hero: все {} пар (hero, variation) одобрены — "
            "перехожу к hero_ready",
            project.id, len(target_pairs),
        )
        project.status = ProjectStatus.hero_ready
        return
    hero_idx, v_idx = target

    user_brief = (
        descriptions[hero_idx - 1]
        if hero_idx - 1 < len(descriptions) else ""
    ).strip()
    if len(user_brief) < 5:
        raise RuntimeError(
            f"hero_descriptions[{hero_idx - 1}] пустой — нечем описать "
            f"героя {hero_idx}/{n_total}. Тыкни «4. Hero» в меню заново."
        )

    # Кол-во вариаций героя (нужно для подписи в HITL и для индексации
    # модификаторов).
    n_variations = 1
    if hero_idx - 1 < len(variations_cfg):
        try:
            n_variations = int(variations_cfg[hero_idx - 1] or 1)
        except (TypeError, ValueError):
            n_variations = 1
    n_variations = max(1, min(5, n_variations))

    # Текстовые «отличия» для вариаций 2..N этого героя.
    modifiers_all = list(
        getattr(project, "hero_variation_modifiers", None) or []
    )
    variation_mods_for_hero: list[str] = []
    if hero_idx - 1 < len(modifiers_all):
        raw = modifiers_all[hero_idx - 1] or []
        if isinstance(raw, list):
            variation_mods_for_hero = [str(x or "").strip() for x in raw]

    is_regen = await _is_regen_for_pair(session, project, hero_idx, v_idx)

    logger.info(
        "[#{}] generate_hero pair=({}/{}, v{}/{}) starting "
        "(brief: {} симв, regen={})",
        project.id, hero_idx, n_total, v_idx, n_variations,
        len(user_brief), is_regen,
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

    # Загружаем уже готовый hero_prompt из meta артефакта v=1 (если есть и
    # это не его собственная регенерация).
    hero_prompt: str = ""
    if v_idx >= 2:
        v1_art = await _v1_artifact_for_hero(session, project, hero_idx)
        if v1_art is not None:
            cached = (v1_art.meta or {}).get("hero_prompt")
            if isinstance(cached, str) and len(cached) >= 100:
                hero_prompt = cached
                logger.info(
                    "[#{}] hero pair=({}, v{}): использую закешированный "
                    "hero_prompt из meta v=1 артефакта (id={}, {} симв)",
                    project.id, hero_idx, v_idx, v1_art.id, len(hero_prompt),
                )

    async with browser_session() as bs:
        # `gpt` нужен ОБОИМ путям:
        #   - v=1 / cache miss — для генерации hero_prompt из ChatGPT;
        #   - любой v — для GPT-rewrite внутри generate_image_with_retries
        #     (после 3 неудачных попыток в outsee он попросит ChatGPT
        #     переписать промт без триггеров модерации).
        gpt = ChatGPTBot(bs)

        # 1) ChatGPT — нужен только для v=1 (или если кеш не нашёлся).
        if not hero_prompt:
            # Текст «сопр. сообщения» берём из gpt_text_builder — там же
            # лежит выбор между юзерским override и дефолтом. Обязательно
            # прогоняем через `render_hero_text` — в шаблоне живут литеральные
            # плейсхолдеры `{{BRIEF}}` и `{{HERO_STYLE}}`, заполняем их
            # отдельно для этого героя.
            hero_template = gtb.get_effective_text(project, "hero")
            hero_ask = gtb.render_hero_text(
                hero_template, brief=user_brief, hero_style=hero_style_content,
            )
            last_reply = ""
            OUTSEE_PROMPT_MAX = 5000
            for attempt in range(1, 4):
                ask = hero_ask
                if (
                    attempt > 1
                    and last_reply
                    and len(last_reply) > OUTSEE_PROMPT_MAX
                ):
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
                    f"ChatGPT не вернул заполненный hero-промт после 3 "
                    f"попыток. Последний ответ ({len(last_reply)} симв): "
                    f"{last_reply[:200]!r}"
                )
            if len(hero_prompt) > OUTSEE_PROMPT_MAX:
                logger.warning(
                    "[#{}] hero prompt всё ещё длиннее лимита: {} > {} — "
                    "отправляю как есть, outsee может не принять",
                    project.id, len(hero_prompt), OUTSEE_PROMPT_MAX,
                )

        # 2) Сборка финального prompt_text.
        # v=1: полный hero_prompt от ChatGPT (без референса).
        # v>=2: ТОЛЬКО текст-модификатор от пользователя + reference-картинка
        #       (которая прикрепляется отдельно через `reference_image=`).
        #       Полный hero_prompt НЕ повторяем — outsee получит «keep this
        #       character + change <X>», где character закодирован в самом
        #       reference image. Этот короткий промт сильно реже триггерит
        #       модерацию, чем длинный hero_prompt с описанием анатомии.
        if v_idx == 1:
            prompt_text = hero_prompt
        else:
            modifier_text = ""
            idx_in_mods = v_idx - 2
            if 0 <= idx_in_mods < len(variation_mods_for_hero):
                modifier_text = variation_mods_for_hero[idx_in_mods]
            if modifier_text.strip():
                prompt_text = modifier_text.strip()
            else:
                # Модификатор не задан — отправляем минимально-короткую
                # инструкцию: «другая поза/ракурс/наряд для того же
                # персонажа на референсе». Без длинного hero_prompt.
                prompt_text = (
                    "Different pose, camera angle, or outfit for the same "
                    "character shown in the reference image."
                )
        logger.info(
            "[#{}] hero pair=({}, v{}): prompt {} симв "
            "(style='{}', regen={})",
            project.id, hero_idx, v_idx, len(prompt_text),
            style_chosen, is_regen,
        )

        # 3) Reference-картинка для v>=2: путь к v=1 артефакту героя.
        ref_path: Path | None = None
        if v_idx >= 2:
            v1_art = await _v1_artifact_for_hero(session, project, hero_idx)
            if v1_art is not None and v1_art.path:
                cand = Path(v1_art.path)
                if cand.exists():
                    ref_path = cand
                else:
                    logger.warning(
                        "[#{}] hero v{}: v=1 файл {} не найден на диске — "
                        "пойду без референса",
                        project.id, v_idx, cand,
                    )
            else:
                logger.warning(
                    "[#{}] hero v{}: v=1 артефакт героя {} не найден — "
                    "пойду без референса",
                    project.id, v_idx, hero_idx,
                )

        # 4) Генерация в outsee.
        outsee = OutseeBot(bs)
        out_dir = Path(settings.data_dir) / "videos" / project.slug / "characters"
        img_gen = IMAGE_GENERATORS_BY_ID.get(
            project.image_generator or DEFAULTS["image_generator"]
        )
        # Aspect ratio и Relax для hero жёстко захардкожены: 16:9 + Relax=ON.
        # См. HERO_ASPECT_RATIO / HERO_RELAX в верху файла.
        ir = IMAGE_RESOLUTIONS_BY_ID.get(
            project.image_resolution or DEFAULTS["image_resolution"]
        )

        short_uuid = uuid.uuid4().hex[:8]
        file_name = f"hero_{hero_idx}_v{v_idx}_{short_uuid}.png"
        out_path = out_dir / file_name
        prompt_id_prefix = (
            f"[ID: P{project.id}-HERO{hero_idx}-V{v_idx}-{short_uuid}]"
        )

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
            # Внутри generate_image_with_retries:
            #   - до 3 попыток с исходным prompt_text;
            #   - если все 3 провалились (включая «Контент отклонён») —
            #     просим ChatGPT переписать промт без триггеров и
            #     ещё 3 попытки уже с переписанным.
            # Дамп страницы в TG БОЛЬШЕ НЕ ШЛЁМ — пользователь попросил
            # вообще не отправлять html/png-снимки на ошибки.
            result = await generate_image_with_retries(
                outsee, gpt,
                prompt=prompt_text,
                out_path=out_path,
                max_attempts_per_prompt=3,
                gpt_rewrite=True,
                aspect_ratio=HERO_ASPECT_RATIO,
                model_slug=img_gen.outsee_slug if img_gen else None,
                resolution=ir.outsee_slug if ir else None,
                relax=HERO_RELAX,
                prompt_id_prefix=prompt_id_prefix,
                reference_image=ref_path,
                timeout=600,
            )

    # 5) Сохраняем артефакт ОДНОЙ вариации.
    file_path = Path(result.file_path)
    art_meta: dict = {
        "hero_index": hero_idx,
        "variation_index": v_idx,
        "variations_total": n_variations,
        "hero_total": n_total,
    }
    if v_idx == 1:
        # Кешируем hero_prompt именно в meta v=1 артефакта — вариации
        # 2..N будут читать его отсюда без повторного запроса в ChatGPT.
        art_meta["hero_prompt"] = hero_prompt
    art = Artifact(
        project_id=project.id,
        kind=ArtifactKind.hero_reference,
        uuid=uuid.uuid4().hex,
        path=str(file_path),
        meta=art_meta,
    )
    session.add(art)
    project.status = ProjectStatus.hero_ready
    await session.flush()

    # 6) xlsx — пишем «текущий» референс героя.
    try:
        _sheet_for_project(project).write_general(
            status=project.status.value,
            hero_description=user_brief,
            hero_image_path=str(file_path),
            hero_image_url=getattr(result, "raw_url", None),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet hero write failed: {}", project.id, e)

    # 7) HITL-карточка на ЭТУ вариацию (юзер одобряет каждую отдельно).
    final_prompt_id_prefix = prompt_id_prefix
    if v_idx < n_variations:
        approve_hint = (
            f"✅ — принять и перейти к v{v_idx + 1}/{n_variations}; "
            f"🔁 — перегенерить эту вариацию."
        )
    elif hero_idx < n_total:
        approve_hint = (
            f"✅ — принять и перейти к герою {hero_idx + 1}/{n_total}; "
            f"🔁 — перегенерить эту вариацию."
        )
    else:
        approve_hint = (
            "✅ — принять (это последняя вариация последнего героя); "
            "🔁 — перегенерить эту вариацию."
        )
    await send_hitl_photo(
        bot, session, project,
        kind=HITLKind.approve_hero,
        photo_path=str(file_path),
        caption=(
            f"{final_prompt_id_prefix}\n"
            f"Герой {hero_idx}/{n_total}, "
            f"вариация {v_idx}/{n_variations}.\n"
            f"Стиль: {style_chosen}\n"
            f"{approve_hint}"
        ),
        payload={
            "step": "hero",
            "artifact_id": art.id,
            "prompt_id_prefix": final_prompt_id_prefix,
            "photo_path": str(file_path),
            "hero_index": hero_idx,
            "hero_total": n_total,
            "variation_index": v_idx,
            "variations_total": n_variations,
            "hero_style": style_chosen,
        },
    )
