"""Авто-продвижение проектов с `auto_mode=True`.

Для одиночных проектов: если юзер выставил `auto_mode=True` и проект
дошёл до *_ready статуса — мы запускаем GPT-проверку артефакта (план /
сценарий) или сразу авто-апруфим (визуальные шаги), затем сами
триггерим следующий running-статус. Юзеру не нужно нажимать «Запустить
шаг N+1» в TG.

Для массовых проектов: тот же механизм + serial worker запускает по
одному подпроекту за раз (см. `is_serial_busy` ниже).

Принципы:
1. auto_advance вызывается из worker-loop'а ОТДЕЛЬНОЙ итерацией для
   `*_ready`-статусов, чтобы не мешать существующей логике запуска
   running-шагов.
2. Текстовые артефакты (plan, script) → review_plan / review_script
   через ChatGPTBot → решение approve/regen/rejected.
3. Визуальные артефакты (hero, images, videos, audio, final) → авто-
   апруф без vision-чека. Когда подтвердишь, что хочешь vision —
   `AUTO_REVIEW_VISUAL_KINDS` будет переключаться через env.
4. На `regen` мы откатываем проект в running-статус соответствующего
   шага (он сам перезапустится воркером, артефакт пересоздастся).
5. На 2 подряд regen на одном шаге → проект в `paused`, чтобы не
   крутиться вечно. Юзер увидит fix_hints в карточке.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    HITLDecision,
    HITLKind,
    HITLRequest,
    Project,
    ProjectStatus,
)
from app.services import auto_review
from app.services.auto_review import ReviewResult
from app.settings import settings
from app.telegram.menu import STEPS, enabled_enrich_slots, step_by_running_status

# (single-mass parity #4) Гранулярность визуального ревью.
#
# Раньше: одна булевая env AUTO_REVIEW_VISUAL=1 включала vision-чек ДЛЯ ВСЕХ
# визуальных шагов (hero/images/videos/final). Юзер просил настройку «какие
# именно шаги визуально проверять GPT'ом, а какие — авто-апруф».
#
# Теперь:
#   * AUTO_REVIEW_VISUAL=1 (legacy)   → vision-чек включён для ВСЕХ visual
#     kind'ов (полностью эквивалент старому поведению).
#   * AUTO_REVIEW_VISUAL_KINDS="approve_hero,approve_videos" → только эти
#     kind'ы будут уходить на GPT-vision. Остальные — auto-approve.
#   * Per-project / per-batch override (settings_snapshot["auto_review_kinds"])
#     читается из meta при каждом такте.
AUTO_REVIEW_VISUAL: bool = os.environ.get("AUTO_REVIEW_VISUAL", "0") == "1"


def _parse_kinds_env(raw: str | None) -> set[HITLKind]:
    if not raw:
        return set()
    out: set[HITLKind] = set()
    for tok in str(raw).split(","):
        t = tok.strip().lower()
        if not t:
            continue
        for k in HITLKind:
            if k.value == t or k.name.lower() == t:
                out.add(k)
                break
    return out


AUTO_REVIEW_VISUAL_KINDS: set[HITLKind] = _parse_kinds_env(
    os.environ.get("AUTO_REVIEW_VISUAL_KINDS")
)

# Максимум подряд `regen` на одном шаге → проект → paused.
MAX_AUTO_REGEN_PER_STEP = 2


@dataclass
class StepTransition:
    """Описание перехода между *_ready и следующим running-шагом."""

    ready_status: ProjectStatus
    next_running: ProjectStatus | None  # None = последний шаг (published)
    kind: HITLKind  # какой HITL мы пытаемся одобрить


# Карта: ready_status → (kind, next_running_status).
# Берётся из STEPS (главное меню), плюс ручные «дополнения» для
# редких/нестандартных переходов (объекты → enrich_1, audio → assembling).
def _build_transitions() -> dict[ProjectStatus, StepTransition]:
    transitions: dict[ProjectStatus, StepTransition] = {}

    # plan_ready → scripting
    transitions[ProjectStatus.plan_ready] = StepTransition(
        ProjectStatus.plan_ready, ProjectStatus.scripting, HITLKind.approve_plan
    )
    # script_ready → splitting
    transitions[ProjectStatus.script_ready] = StepTransition(
        ProjectStatus.script_ready, ProjectStatus.splitting, HITLKind.approve_script
    )
    # frames_ready → generating_hero (объекты → персонажи)
    transitions[ProjectStatus.frames_ready] = StepTransition(
        ProjectStatus.frames_ready, ProjectStatus.generating_hero, HITLKind.approve_hero
    )
    # hero_ready → generating_items (если предметы есть) или enriching_1
    # Простой default — generating_items; если предметов нет, шаг сам сразу
    # завершится и проект уйдёт в items_ready.
    transitions[ProjectStatus.hero_ready] = StepTransition(
        ProjectStatus.hero_ready, ProjectStatus.generating_items, HITLKind.approve_hero
    )
    # items_ready → enriching_1
    transitions[ProjectStatus.items_ready] = StepTransition(
        ProjectStatus.items_ready, ProjectStatus.enriching_1, HITLKind.approve_hero
    )
    # enrich_1..5_ready → enriching_<n+1> или generating_image_prompts
    enrich_chain = [
        (ProjectStatus.enrich_1_ready, ProjectStatus.enriching_2),
        (ProjectStatus.enrich_2_ready, ProjectStatus.enriching_3),
        (ProjectStatus.enrich_3_ready, ProjectStatus.enriching_4),
        (ProjectStatus.enrich_4_ready, ProjectStatus.enriching_5),
        (ProjectStatus.enrich_5_ready, ProjectStatus.generating_image_prompts),
    ]
    for ready, nxt in enrich_chain:
        transitions[ready] = StepTransition(ready, nxt, HITLKind.approve_hero)

    # image_prompts_ready → generating_images
    transitions[ProjectStatus.image_prompts_ready] = StepTransition(
        ProjectStatus.image_prompts_ready,
        ProjectStatus.generating_images,
        HITLKind.approve_images,
    )
    # images_ready → generating_animation_prompts
    transitions[ProjectStatus.images_ready] = StepTransition(
        ProjectStatus.images_ready,
        ProjectStatus.generating_animation_prompts,
        HITLKind.approve_images,
    )
    # animation_prompts_ready → generating_videos
    transitions[ProjectStatus.animation_prompts_ready] = StepTransition(
        ProjectStatus.animation_prompts_ready,
        ProjectStatus.generating_videos,
        HITLKind.approve_videos,
    )
    # videos_ready → generating_audio
    transitions[ProjectStatus.videos_ready] = StepTransition(
        ProjectStatus.videos_ready,
        ProjectStatus.generating_audio,
        HITLKind.approve_videos,
    )
    # audio_ready → assembling
    transitions[ProjectStatus.audio_ready] = StepTransition(
        ProjectStatus.audio_ready, ProjectStatus.assembling, HITLKind.approve_videos
    )
    # assembled → publishing (или конец)
    transitions[ProjectStatus.assembled] = StepTransition(
        ProjectStatus.assembled, ProjectStatus.publishing, HITLKind.approve_final
    )
    return transitions


TRANSITIONS = _build_transitions()


def expected_status_progression(project: Project | None) -> list[ProjectStatus]:
    """(single-mass parity #8) Ожидаемая последовательность
    `*_running` статусов, которые проект ПРОШСДЕТ по пипалайну.

    Используется:
    * как Single Source of Truth в тестах BLOCK D (проверка,
      что одиночный и массовый проходят одни и те же шаги в одном
      порядке);
    * как debug-туля в admin-меню (показывать юзеру «вот что будет
      дальше»).

    Учитывает enrich_slots_count (парити #3) — включает ровно
    N слотов, где N = enabled_enrich_slots(project).
    """
    n_slots = enabled_enrich_slots(project)
    progression: list[ProjectStatus] = [
        ProjectStatus.planning,
        ProjectStatus.scripting,
        ProjectStatus.splitting,
        ProjectStatus.generating_hero,
        ProjectStatus.generating_items,
    ]
    enrich_running = [
        ProjectStatus.enriching_1,
        ProjectStatus.enriching_2,
        ProjectStatus.enriching_3,
        ProjectStatus.enriching_4,
        ProjectStatus.enriching_5,
    ]
    progression.extend(enrich_running[: max(1, min(5, n_slots))])
    progression.extend([
        ProjectStatus.generating_image_prompts,
        ProjectStatus.generating_images,
        ProjectStatus.generating_animation_prompts,
        ProjectStatus.generating_videos,
        ProjectStatus.generating_audio,
        ProjectStatus.assembling,
        ProjectStatus.publishing,
    ])
    return progression


# Для каких kind мы запускаем GPT-чек (text or vision):
TEXT_REVIEW_KINDS = {HITLKind.approve_plan, HITLKind.approve_script}
VISUAL_REVIEW_KINDS = {
    HITLKind.approve_hero,
    HITLKind.approve_images,
    HITLKind.approve_videos,
    HITLKind.approve_final,
}


# ============================================================
# Поиск последнего HITL
# ============================================================


async def get_latest_hitl(
    session: AsyncSession, project_id: int, kind: HITLKind
) -> HITLRequest | None:
    rows = (
        await session.execute(
            select(HITLRequest)
            .where(
                HITLRequest.project_id == project_id,
                HITLRequest.kind == kind,
            )
            .order_by(HITLRequest.id.desc())
            .limit(1)
        )
    ).scalars().all()
    return rows[0] if rows else None


# ============================================================
# Применение решения auto_review
# ============================================================


def _running_for_ready(ready: ProjectStatus) -> ProjectStatus | None:
    """Возвращает running-статус того же шага. Используется при `regen` —
    откатываемся назад, чтобы воркер запустил шаг повторно."""
    step = step_by_running_status(ready)
    # step_by_running_status работает по running_status, нам же нужен
    # по ready_status. Найдём вручную в STEPS.
    for s in STEPS:
        if s.ready_status == ready:
            return s.running_status
    # Это может быть sub-step (hero / items / enrich_i) — STEPS их не
    # содержит. Маппинг руками:
    sub_map: dict[ProjectStatus, ProjectStatus] = {
        ProjectStatus.hero_ready: ProjectStatus.generating_hero,
        ProjectStatus.items_ready: ProjectStatus.generating_items,
        ProjectStatus.enrich_1_ready: ProjectStatus.enriching_1,
        ProjectStatus.enrich_2_ready: ProjectStatus.enriching_2,
        ProjectStatus.enrich_3_ready: ProjectStatus.enriching_3,
        ProjectStatus.enrich_4_ready: ProjectStatus.enriching_4,
        ProjectStatus.enrich_5_ready: ProjectStatus.enriching_5,
    }
    _ = step  # silence
    return sub_map.get(ready)


def _retry_counter_key(status_value: str) -> str:
    return f"auto_retry_{status_value}"


def _get_retry_count(project: Project, ready: ProjectStatus) -> int:
    meta = project.meta or {}
    return int(meta.get(_retry_counter_key(ready.value)) or 0)


def _bump_retry_count(project: Project, ready: ProjectStatus) -> int:
    meta = dict(project.meta or {})
    key = _retry_counter_key(ready.value)
    cur = int(meta.get(key) or 0) + 1
    meta[key] = cur
    project.meta = meta
    return cur


def _reset_retry_count(project: Project, ready: ProjectStatus) -> None:
    meta = dict(project.meta or {})
    key = _retry_counter_key(ready.value)
    if key in meta:
        del meta[key]
        project.meta = meta


_ENRICH_READY_TO_INDEX: dict[ProjectStatus, int] = {
    ProjectStatus.enrich_1_ready: 1,
    ProjectStatus.enrich_2_ready: 2,
    ProjectStatus.enrich_3_ready: 3,
    ProjectStatus.enrich_4_ready: 4,
    ProjectStatus.enrich_5_ready: 5,
}


def _next_running_with_enrich_cap(
    project: Project, transition: StepTransition
) -> ProjectStatus | None:
    """(single-mass parity #3) Наследует логику single-mode меню:
    если проект в enrich_N_ready и N ≥ enrich_slots_count — это был
    ПОСЛЕДНИЙ активный слот, прыгаем сразу на generating_image_prompts.
    Иначе — следующий enrich. Раньше auto-mode по фиксированной
    таблице всегда шёл до enrich_5, игнорируя настройку юзера."""
    cur_idx = _ENRICH_READY_TO_INDEX.get(transition.ready_status)
    if cur_idx is None:
        return transition.next_running
    slots = enabled_enrich_slots(project)
    if cur_idx >= slots:
        return ProjectStatus.generating_image_prompts
    return transition.next_running


async def _next_status_after_hero_approve(
    session: AsyncSession,
    project: Project,
    hitl: HITLRequest | None,
) -> ProjectStatus:
    """(single-mass parity #1, #2) Зеркалит логику single-mode callback'а
    `bot.py` (≈ строки 5757-5851) для HITL `approve_hero`:

    * Excel-режим (payload содержит `excel_id`): считаем сколько персонажей
      ещё не одобрено; если есть остаток — `generating_hero` (воркер сделает
      следующего), иначе — `generating_items`.
    * Обычный режим (payload `hero_index` / `variation_index`):
      следующая вариация / следующий герой → `generating_hero`,
      иначе → `generating_items` (=transition.next_running).
    """
    payload: dict = {}
    if hitl is not None and isinstance(hitl.payload, dict):
        payload = dict(hitl.payload)

    # --- Excel-hero (parity #2) ---
    excel_id = payload.get("excel_id")
    if isinstance(excel_id, str) and excel_id:
        meta = dict(project.meta or {})
        cfg = meta.get("excel_hero") or {}
        all_ids: list[str] = []
        for c in (cfg.get("characters") or []):
            if isinstance(c, dict):
                cid = str((c.get("id") or "")).strip()
                if cid:
                    all_ids.append(cid)
        approved_rows = (
            await session.execute(
                select(HITLRequest).where(
                    HITLRequest.project_id == project.id,
                    HITLRequest.kind == HITLKind.approve_hero,
                    HITLRequest.decision == HITLDecision.approved,
                )
            )
        ).scalars().all()
        approved_ids = {
            (r.payload or {}).get("excel_id")
            for r in approved_rows
            if (r.payload or {}).get("excel_id")
        }
        approved_ids.add(excel_id)
        remaining = [i for i in all_ids if i not in approved_ids]
        if remaining:
            return ProjectStatus.generating_hero
        return ProjectStatus.generating_items

    # --- Multi-character / multi-variation (parity #1) ---
    try:
        cur_hi = int(payload.get("hero_index") or 1)
        cur_vi = int(payload.get("variation_index") or 1)
    except (TypeError, ValueError):
        cur_hi, cur_vi = 1, 1
    n_total = int(project.hero_count or 1) or 1
    vars_cfg = list(project.hero_variations or [])
    n_var = 1
    if cur_hi - 1 < len(vars_cfg):
        try:
            n_var = int(vars_cfg[cur_hi - 1] or 1)
        except (TypeError, ValueError):
            n_var = 1
    n_var = max(1, min(5, n_var))

    if cur_vi < n_var:
        return ProjectStatus.generating_hero
    if cur_hi < n_total:
        return ProjectStatus.generating_hero
    # Последняя вариация последнего героя — шаг полностью завершён.
    return ProjectStatus.generating_items


async def _hide_hitl_buttons_with_badge(
    bot: Bot | None, hitl: HITLRequest | None, badge: str
) -> None:
    """(single-mass parity #5) Убирает inline-кнопки С HITL-карточки
    в TG после auto-решения и добавляет подпись-бейдж в текст/caption.

    Раньше auto_advance менял только HITLRequest.decision в БД, а
    карточка с кнопками ✅/🔁/❌ оставалась кликабельной, и юзер
    не понимал что решение уже принято (и клик приводил к
    «HITL уже обработан» алерту).
    """
    if bot is None or hitl is None or hitl.tg_message_id is None:
        return
    chat_id = settings.telegram_owner_chat_id
    msg_id = hitl.tg_message_id
    # Пробуем edit caption (визуальные HITL — photo/video); если
    # СЭТОДА приходит ApiError "there is no caption" — переходим
    # на edit_message_text.
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=msg_id, reply_markup=None
        )
    except Exception:  # noqa: BLE001
        pass
    if not badge:
        return
    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=msg_id,
            caption=badge[:1024],
            reply_markup=None,
        )
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        # Text HITL (plan/script) — добавляем бейдж внизу. Без исходного
        # текста можем только полностью перезаписать — это ок, все
        # решения уже в БД.
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=badge[:4096],
            reply_markup=None,
        )
    except Exception:  # noqa: BLE001
        pass


async def _apply_approve(
    session: AsyncSession,
    project: Project,
    hitl: HITLRequest | None,
    transition: StepTransition,
    *,
    bot: Bot | None = None,
    badge: str | None = None,
) -> None:
    """Эмулируем клик `approve` пользователем в TG."""
    if hitl is not None and hitl.decision is HITLDecision.pending:
        hitl.decision = HITLDecision.approved

    # Hero-ready: внутри шага 4 может быть несколько героев / вариаций,
    # одна HITL-карточка ≠ переход к следующему шагу. Зеркалим логику
    # из bot.py callback (parity #1 + #2 — multi-hero / excel-hero).
    if transition.ready_status is ProjectStatus.hero_ready:
        nxt = await _next_status_after_hero_approve(session, project, hitl)
        project.status = nxt
    else:
        nxt = _next_running_with_enrich_cap(project, transition)
        if nxt is not None:
            project.status = nxt
    _reset_retry_count(project, transition.ready_status)
    await session.flush()
    logger.info(
        "auto_advance: #{} {} → approved → {}",
        project.id, transition.ready_status.value,
        project.status.value,
    )
    # (single-mass parity #5) Гасим кнопки на карточке в TG.
    await _hide_hitl_buttons_with_badge(
        bot, hitl, badge or "✅ Авто-одобрено"
    )


async def _apply_regen(
    session: AsyncSession,
    project: Project,
    hitl: HITLRequest | None,
    transition: StepTransition,
    result: ReviewResult,
    *,
    bot: Bot | None = None,
) -> None:
    """Эмулируем клик `regen` + кладём fix_hints для следующей генерации."""
    if hitl is not None and hitl.decision is HITLDecision.pending:
        hitl.decision = HITLDecision.regenerate

    count = _bump_retry_count(project, transition.ready_status)

    if count > MAX_AUTO_REGEN_PER_STEP:
        # Превышен лимит — pause проекта.
        project.status = ProjectStatus.paused
        meta = dict(project.meta or {})
        meta["auto_paused_reason"] = (
            f"{transition.ready_status.value}: "
            f"{count - 1} раз подряд GPT просил regen"
        )
        meta["auto_paused_fix_hints"] = result.fix_hints
        project.meta = meta
        await session.flush()
        logger.warning(
            "auto_advance: #{} paused after {} regens on {}",
            project.id, count - 1, transition.ready_status.value,
        )
        return

    # Откатываемся к running-статусу шага.
    back_to = _running_for_ready(transition.ready_status)
    if back_to is None:
        logger.warning(
            "auto_advance: не знаю как откатить {} → running",
            transition.ready_status.value,
        )
        project.status = ProjectStatus.paused
    else:
        project.status = back_to

    # Передаём fix_hints в gpt_text_override, чтобы шаг увидел их и
    # передал в промт.
    meta = dict(project.meta or {})
    if result.fix_hints:
        meta["auto_fix_hints"] = result.fix_hints
    project.meta = meta
    await session.flush()
    logger.info(
        "auto_advance: #{} {} → regen #{} → {}",
        project.id, transition.ready_status.value, count,
        back_to.value if back_to else "(paused)",
    )
    await _hide_hitl_buttons_with_badge(
        bot, hitl, "🔁 Авто-перегенерация (GPT просит fix_hints)"
    )


async def _apply_reject(
    session: AsyncSession,
    project: Project,
    hitl: HITLRequest | None,
    transition: StepTransition,
    result: ReviewResult,
    *,
    bot: Bot | None = None,
) -> None:
    if hitl is not None and hitl.decision is HITLDecision.pending:
        hitl.decision = HITLDecision.rejected
    project.status = ProjectStatus.paused
    meta = dict(project.meta or {})
    meta["auto_paused_reason"] = (
        f"{transition.ready_status.value}: GPT отметил как rejected"
    )
    meta["auto_paused_fix_hints"] = result.fix_hints
    project.meta = meta
    await session.flush()
    logger.warning(
        "auto_advance: #{} REJECTED on {}",
        project.id, transition.ready_status.value,
    )
    await _hide_hitl_buttons_with_badge(
        bot, hitl, "❌ GPT-отказ. Проект поставлен на паузу."
    )


# ============================================================
# Главная функция
# ============================================================


async def maybe_auto_advance(
    session: AsyncSession, project: Project, bot: Bot
) -> bool:
    """Возвращает True если проект был продвинут (или поставлен в paused).

    Вызывается ИЗ worker-loop'а ПОСЛЕ обхода running-статусов. Только
    для проектов с `auto_mode=True` в *_ready состоянии.
    """
    if not getattr(project, "auto_mode", False):
        return False
    status = project.status
    if status not in TRANSITIONS:
        return False  # не ready-статус, нечего двигать

    transition = TRANSITIONS[status]
    hitl = await get_latest_hitl(session, project.id, transition.kind)

    # Если HITL уже approved юзером руками — просто двигаемся вперёд.
    if hitl is not None and hitl.decision is HITLDecision.approved:
        # Юзер нажал в боте — bot.py уже погасил кнопки и добавил
        # бейдж. Ничего не рисуем.
        await _apply_approve(session, project, hitl, transition, bot=None)
        return True

    # (single-mass parity #4) Решаем нужен ли vision-чек для этого kind'а.
    if transition.kind in VISUAL_REVIEW_KINDS and not _should_vision_check(
        project, transition.kind
    ):
        logger.info(
            "auto_advance: #{} {} → auto-approve (visual, no GPT check)",
            project.id, status.value,
        )
        await _apply_approve(session, project, hitl, transition, bot=bot)
        return True

    # GPT-чек для текстовых kind'ов:
    if transition.kind in TEXT_REVIEW_KINDS:
        artifact = _artifact_for_kind(project, transition.kind)
        if not artifact:
            logger.warning(
                "auto_advance: #{} нет артефакта для {}, пропускаю",
                project.id, transition.kind.value,
            )
            return False
        result = await _run_text_review(project, transition.kind, artifact)
        return await _apply_review_result(
            session, project, hitl, transition, result, bot=bot
        )

    # Все остальные случаи (visual + AUTO_REVIEW_VISUAL=1) — TODO.
    # Пока — auto-approve.
    await _apply_approve(session, project, hitl, transition, bot=bot)
    return True


def _should_vision_check(project: Project, kind: HITLKind) -> bool:
    """(single-mass parity #4) Per-project / env-driven решение, нужно ли
    запускать GPT-vision на визуальный артефакт.

    Приоритет:
        1. project.meta["auto_review_kinds"] (массовый — наследуется из
           batch.settings_snapshot, BLOCK B) — список kind-value строк.
        2. AUTO_REVIEW_VISUAL_KINDS env (set of HITLKind).
        3. AUTO_REVIEW_VISUAL=1 (legacy) → все визуальные kind'ы.
    """
    meta = getattr(project, "meta", None) or {}
    override = meta.get("auto_review_kinds")
    if isinstance(override, list):
        wanted: set[str] = {str(x).strip().lower() for x in override}
        return (kind.value in wanted) or (kind.name.lower() in wanted)
    if kind in AUTO_REVIEW_VISUAL_KINDS:
        return True
    return AUTO_REVIEW_VISUAL


def _artifact_for_kind(project: Project, kind: HITLKind) -> str | None:
    """Берёт текст артефакта из проекта по типу HITL."""
    if kind is HITLKind.approve_plan:
        return project.general_plan or None
    if kind is HITLKind.approve_script:
        return project.script_text or None
    return None


async def _run_text_review(
    project: Project, kind: HITLKind, artifact: str
) -> ReviewResult:
    """Запускает GPT-ревью текста в новом chat (через ChatGPTBot)."""
    # Локальный импорт чтобы избежать циклов / тяжёлой инициализации.
    from app.bots.browser import browser_session
    from app.bots.chatgpt import ChatGPTBot

    # Папка snapshot'а массового — если проект в батче.
    snap = None
    if getattr(project, "batch_slug", None):
        from pathlib import Path

        from app.settings import settings as _s
        snap = Path(_s.data_dir) / "batches" / project.batch_slug / "prompts"
        if not snap.exists():
            snap = None

    # Постоянный продукт массового (если есть) — для проверки упоминания.
    meta = getattr(project, "meta", None) or {}
    product = meta.get("permanent_product") or {}
    product_name = (product.get("name") or "").strip() or None

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        if kind is HITLKind.approve_plan:
            return await auto_review.review_plan(
                plan_text=artifact, chatgpt_bot=gpt,
                batch_snapshot_dir=snap,
                product_name=product_name,
            )
        if kind is HITLKind.approve_script:
            return await auto_review.review_script(
                script_text=artifact, chatgpt_bot=gpt,
                batch_snapshot_dir=snap,
                product_name=product_name,
            )
    raise RuntimeError(f"_run_text_review: неизвестный kind={kind}")


async def _apply_review_result(
    session: AsyncSession,
    project: Project,
    hitl: HITLRequest | None,
    transition: StepTransition,
    result: ReviewResult,
    *,
    bot: Bot | None = None,
) -> bool:
    """Применяет ReviewResult к проекту + уведомляет (опционально)."""
    if result.decision is HITLDecision.approved:
        await _apply_approve(session, project, hitl, transition, bot=bot)
    elif result.decision is HITLDecision.regenerate:
        await _apply_regen(
            session, project, hitl, transition, result, bot=bot
        )
    elif result.decision is HITLDecision.rejected:
        await _apply_reject(
            session, project, hitl, transition, result, bot=bot
        )
    else:
        # На pending/edit_prompt — игнорируем, оставляем юзеру.
        logger.warning(
            "auto_advance: #{} unexpected decision {}",
            project.id, result.decision,
        )
        return False
    return True


# ============================================================
# Serial worker для массовых
# ============================================================


async def serial_busy_in_batch(session: AsyncSession, batch_id: int) -> int | None:
    """Если в массовом #batch_id есть проект в running-состоянии —
    возвращает его id. Иначе None.

    Используется чтобы НЕ запускать второй подпроект параллельно (юзер
    хочет, чтобы массовое шло «по одному»).
    """
    busy_running = [
        ProjectStatus.planning,
        ProjectStatus.scripting,
        ProjectStatus.splitting,
        ProjectStatus.generating_hero,
        ProjectStatus.generating_items,
        ProjectStatus.enriching_1,
        ProjectStatus.enriching_2,
        ProjectStatus.enriching_3,
        ProjectStatus.enriching_4,
        ProjectStatus.enriching_5,
        ProjectStatus.generating_image_prompts,
        ProjectStatus.generating_images,
        ProjectStatus.generating_animation_prompts,
        ProjectStatus.generating_videos,
        ProjectStatus.generating_audio,
        ProjectStatus.assembling,
        ProjectStatus.publishing,
    ]
    busy = (
        await session.execute(
            select(Project)
            .where(
                Project.batch_id == batch_id,
                Project.status.in_(busy_running),
            )
            .order_by(Project.batch_position.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return busy.id if busy is not None else None


async def serial_next_to_start(
    session: AsyncSession, batch_id: int
) -> Project | None:
    """Следующий по очереди подпроект массового, который ещё не начат и
    у которого `auto_mode=True`. Берётся минимальный `batch_position`
    среди статусов `new` или `paused` (paused → юзер мог снять с
    паузы)."""
    candidates = (
        await session.execute(
            select(Project)
            .where(
                Project.batch_id == batch_id,
                Project.auto_mode == True,  # noqa: E712
                Project.status.in_([ProjectStatus.new]),
            )
            .order_by(Project.batch_position.asc())
            .limit(1)
        )
    ).scalars().all()
    return candidates[0] if candidates else None


async def serial_tick_batches(session: AsyncSession) -> int:
    """Один такт сериализатора. Возвращает кол-во запущенных подпроектов.

    (single-mass parity #6) Раньше между `serial_busy_in_batch` и
    `next_p.status = planning` был await на выборке кандидата,
    во время которого callback из TG или maybe_auto_advance могли
    запустить другой sub-project того же batch'а — получали
    два рунящих параллельно (нарушение max_parallelism=1).
    Теперь повторяем busy-check ПОСЛЕ выбора кандидата и
    ПЕРЕД flip'ом статуса. Двойная проверка покрывает распространённые
    сценарии (await yield), полный SELECT...FOR UPDATE избыточен
    для sqlite/single-process микро-воркера.
    """
    from app.models import BatchProject, BatchStatus

    started = 0
    batches = (
        await session.execute(
            select(BatchProject).where(
                BatchProject.status == BatchStatus.running
            )
        )
    ).scalars().all()
    for batch in batches:
        busy = await serial_busy_in_batch(session, batch.id)
        if busy is not None:
            continue
        next_p = await serial_next_to_start(session, batch.id)
        if next_p is None:
            continue
        # (parity #6) Повторный busy-check ПЕРЕД flip'ом.
        busy_again = await serial_busy_in_batch(session, batch.id)
        if busy_again is not None:
            logger.info(
                "auto_advance: batch #{} TOCTOU — «занятие» #{} появилось "
                "между выбором next_p=#{} и flip\u2019ом, пропуск",
                batch.id, busy_again, next_p.id,
            )
            continue
        # Пересвежаем самого кандидата — вдруг callback в TG в эту же
        # хрупкую миллисекунду уже поменял его статус (например
        # юзер вручную поставил на паузу).
        await session.refresh(next_p)
        if next_p.status is not ProjectStatus.new:
            logger.info(
                "auto_advance: batch #{} sub #{} уже не new ({}), пропуск",
                batch.id, next_p.id, next_p.status.value,
            )
            continue
        # Стартуем подпроект: status new → planning.
        next_p.status = ProjectStatus.planning
        await session.flush()
        started += 1
        logger.info(
            "auto_advance: batch #{} started sub #{} (pos {})",
            batch.id, next_p.id, next_p.batch_position,
        )
    _ = settings  # keep import alive
    return started
