"""Перевычисление `project.status` из реальных данных в БД.

Зачем: status в БД хранится отдельно от собственно данных. Когда-то был
старый failed-bypass в bot.py (юзер тыкал шаг 5 из status=failed —
status молча подменялся на step.requires=hero_ready, при том что план/
скрипт/frames не были выполнены). Менюшка красила ✅ по status_order,
не валидируя данные. После шага клик 5 падал на «нет кадров».

Этот модуль — единая правда: что реально лежит в БД, тот status и есть.

Использование:
    from app.services.project_state import compute_actual_status, recompute_status

    # На каждом старте, для всех проектов:
    new_status = await compute_actual_status(session, project)
    if new_status != project.status:
        project.status = new_status

    # Или через recompute_status — он логирует diff:
    await recompute_status(session, project)
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    Project,
    ProjectStatus,
)
from app.services.plan_validation import is_meaningful_general_plan

# Промежуточные «running» статусы — их при перевычислении не учитываем
# (не зафиксированы в БД). Если статус сейчас `generating_X` — мы вернём
# либо его prerequisite, либо его ready_status (зависит от данных).
_RUNNING_STATUSES = {
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
    ProjectStatus.generating_music,
    ProjectStatus.assembling,
    ProjectStatus.publishing,
}


def is_running_status(status: ProjectStatus) -> bool:
    """True если статус — «running» (шаг сейчас выполняется воркером)."""
    return status in _RUNNING_STATUSES


def _nonempty_item_descriptions(project: Project) -> list[str]:
    raw = project.item_descriptions or []
    return [d.strip() for d in raw if isinstance(d, str) and d.strip()]


def _nonempty_hero_descriptions(project: Project) -> list[str]:
    raw = project.hero_descriptions or []
    return [d.strip() for d in raw if isinstance(d, str) and d.strip()]


def _hero_step_required(project: Project) -> bool:
    """Нужен ли шаг персонажей (не путать с hero_count=0 в xlsx-flow)."""
    if project.hero_mode == "no_hero":
        return False
    if (project.hero_count or 0) > 0:
        return True
    if _nonempty_hero_descriptions(project):
        return True
    if (project.hero_description or "").strip():
        return True
    if _excel_hero_expected_count(project) > 0:
        return True
    return False


def _items_step_required(project: Project) -> bool:
    return len(_nonempty_item_descriptions(project)) > 0


def _enrich_ready_from_meta(project: Project) -> ProjectStatus | None:
    """Максимальный enrich_*_ready по ``meta.enrich_completed_slots``."""
    meta = project.meta if isinstance(project.meta, dict) else {}
    slots: list[int] = []
    for raw in meta.get("enrich_completed_slots") or []:
        try:
            slots.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not slots:
        return None
    by_slot = {
        1: ProjectStatus.enrich_1_ready,
        2: ProjectStatus.enrich_2_ready,
        3: ProjectStatus.enrich_3_ready,
        4: ProjectStatus.enrich_4_ready,
        5: ProjectStatus.enrich_5_ready,
    }
    return by_slot.get(max(slots))


def _excel_hero_expected_count(project: Project) -> int:
    """Сколько персонажей в meta.excel_hero (лист «Персонажи»)."""
    meta = project.meta if isinstance(project.meta, dict) else {}
    cfg = meta.get("excel_hero") or {}
    chars = cfg.get("characters") or []
    n = 0
    for c in chars:
        if isinstance(c, dict) and str((c.get("id") or "")).strip():
            n += 1
    return n


async def _count_excel_hero_artifacts(session, project_id: int) -> int:
    """Число уникальных excel_id среди hero_reference с файлом."""
    rows = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.kind == ArtifactKind.hero_reference,
            )
        )
    ).scalars().all()
    seen: set[str] = set()
    for a in rows:
        xid = (a.meta or {}).get("excel_id")
        if not isinstance(xid, str) or not xid or xid in seen:
            continue
        if a.path and Path(a.path).is_file():
            seen.add(xid)
    return len(seen)


async def compute_actual_status(session, project: Project) -> ProjectStatus:
    """Вернуть наивысший status, который подтверждён данными в БД.

    Логика «снизу вверх»: смотрим сначала самый ранний прогресс
    (general_plan), потом всё дальше. Если данных нет — возвращаем
    предыдущий уровень.

    Никогда не возвращает `paused`/`failed`/`*ing` (running) — только
    «контрольные точки» (ready / new / assembled / published).
    """
    pid = project.id
    has_plan = is_meaningful_general_plan(project.general_plan)
    has_script = bool(project.script_text)
    has_hero_descr = bool(project.hero_description)

    fr_total = (
        await session.execute(
            select(func.count(Frame.id)).where(Frame.project_id == pid)
        )
    ).scalar_one()
    fr_with_img_prompt = (
        await session.execute(
            select(func.count(Frame.id)).where(
                Frame.project_id == pid,
                Frame.image_prompt.isnot(None),
                Frame.image_prompt != "",
            )
        )
    ).scalar_one()
    fr_with_anim_prompt = (
        await session.execute(
            select(func.count(Frame.id)).where(
                Frame.project_id == pid,
                Frame.animation_prompt.isnot(None),
                Frame.animation_prompt != "",
            )
        )
    ).scalar_one()

    hero_arts = (
        await session.execute(
            select(func.count(Artifact.id)).where(
                Artifact.project_id == pid,
                Artifact.kind == ArtifactKind.hero_reference,
            )
        )
    ).scalar_one()
    item_arts = (
        await session.execute(
            select(func.count(Artifact.id)).where(
                Artifact.project_id == pid,
                Artifact.kind == ArtifactKind.item_reference,
            )
        )
    ).scalar_one()
    scene_image_arts = (
        await session.execute(
            select(func.count(Artifact.id)).where(
                Artifact.project_id == pid,
                Artifact.kind == ArtifactKind.scene_image,
            )
        )
    ).scalar_one()
    scene_video_arts = (
        await session.execute(
            select(func.count(Artifact.id)).where(
                Artifact.project_id == pid,
                Artifact.kind == ArtifactKind.scene_video,
            )
        )
    ).scalar_one()
    audio_arts = (
        await session.execute(
            select(func.count(Artifact.id)).where(
                Artifact.project_id == pid,
                Artifact.kind == ArtifactKind.audio,
            )
        )
    ).scalar_one()
    music_arts = (
        await session.execute(
            select(func.count(Artifact.id)).where(
                Artifact.project_id == pid,
                Artifact.kind == ArtifactKind.music,
            )
        )
    ).scalar_one()
    final_arts = (
        await session.execute(
            select(func.count(Artifact.id)).where(
                Artifact.project_id == pid,
                Artifact.kind == ArtifactKind.final_video,
            )
        )
    ).scalar_one()

    # Идём снизу вверх. Каждый уровень — это AND условие: для уровня N
    # все prerequisite N-1 тоже должны быть выполнены.
    if not has_plan:
        return ProjectStatus.new
    # plan ✓
    if not has_script:
        return ProjectStatus.plan_ready
    # script ✓
    if fr_total == 0:
        return ProjectStatus.script_ready
    # frames ✓
    hero_required = _hero_step_required(project)
    if hero_required:
        n_excel = _excel_hero_expected_count(project)
        if hero_arts == 0 and not has_hero_descr:
            if n_excel > 0:
                n_excel_done = await _count_excel_hero_artifacts(session, pid)
                if n_excel_done == 0:
                    return ProjectStatus.frames_ready
                if n_excel_done < n_excel:
                    return ProjectStatus.hero_ready
            return ProjectStatus.frames_ready
        if hero_arts == 0:
            if n_excel > 0:
                n_excel_done = await _count_excel_hero_artifacts(session, pid)
                if n_excel_done < n_excel:
                    return ProjectStatus.hero_ready
            return ProjectStatus.frames_ready
    # Excel-hero / items / enrich — только пока нет image_prompt на всех кадрах.
    # Иначе recompute откатывал image_prompts_ready → hero_ready при частичном hero.
    if fr_with_img_prompt < fr_total:
        # Зафиксированные enrich-слоты важнее частичного excel-hero.
        enrich_st = _enrich_ready_from_meta(project)
        if enrich_st is not None:
            return enrich_st
        if hero_required:
            n_excel = _excel_hero_expected_count(project)
            if n_excel > 0:
                n_excel_done = await _count_excel_hero_artifacts(session, pid)
                if n_excel_done < n_excel:
                    return ProjectStatus.hero_ready
        if _items_step_required(project):
            item_descs = _nonempty_item_descriptions(project)
            if item_arts < len(item_descs):
                return ProjectStatus.hero_ready
            return ProjectStatus.items_ready
        if hero_required:
            return ProjectStatus.hero_ready
        return ProjectStatus.frames_ready
    # image_prompts ✓
    if scene_image_arts < fr_total:
        return ProjectStatus.image_prompts_ready
    # images ✓
    if fr_with_anim_prompt < fr_total:
        return ProjectStatus.images_ready
    # animation_prompts ✓
    if scene_video_arts < fr_total:
        return ProjectStatus.animation_prompts_ready
    # videos ✓
    if audio_arts == 0:
        return ProjectStatus.videos_ready
    # audio ✓
    if final_arts == 0:
        if music_arts > 0:
            return ProjectStatus.music_ready
        return ProjectStatus.audio_ready
    # final ✓
    return ProjectStatus.assembled  # `published` ставится отдельно по факту YT-аплоада


async def recompute_status(
    session,
    project: Project,
    *,
    dry_run: bool = False,
    log_prefix: str = "recompute",
) -> tuple[ProjectStatus, ProjectStatus, bool]:
    """Перевычислить и (если не dry_run) обновить project.status.

    Если проект сейчас в running-статусе (`generating_*`), его НЕ трогаем —
    он реально выполняется, рекомпьют ему может помешать. Идемпотентный
    рекомпьют для running-проектов делает воркер-loop при ошибках шага.

    Возвращает (old_status, new_status, changed).
    """
    from loguru import logger

    old = project.status
    if old in _RUNNING_STATUSES:
        # Бежит шаг — не вмешиваемся. Если шаг упадёт, воркер сам
        # откатит status (см. _run_worker_loop в app/main.py).
        return old, old, False
    if old in (ProjectStatus.paused, ProjectStatus.failed):
        # `paused` — пользователь руками приостановил. `failed` —
        # legacy, _init_db уже сбросил в `new`, но защитимся тут тоже.
        return old, old, False

    new = await compute_actual_status(session, project)
    from app.telegram.menu import status_order as _ord

    # Откат разрешён, если текущий *_ready не подтверждён данными (ложный plan_ready).
    if _ord(new) < _ord(old):
        from app.services.step_data_guard import ready_status_confirmed_by_data

        if await ready_status_confirmed_by_data(session, project, old):
            logger.debug(
                "[#{}] {}: keep {} (computed {} — no downgrade)",
                project.id,
                log_prefix,
                old.value,
                new.value,
            )
            return old, old, False
        logger.warning(
            "[#{}] {}: {} → {} (статус опережал данные — откат)",
            project.id,
            log_prefix,
            old.value,
            new.value,
        )

    if old == new:
        return old, new, False

    if dry_run:
        logger.info(
            "[#{}] {}: {} → {} [dry-run]",
            project.id, log_prefix, old.value, new.value,
        )
        return old, new, True
    project.status = new
    logger.info(
        "[#{}] {}: {} → {}",
        project.id, log_prefix, old.value, new.value,
    )
    return old, new, True


async def recompute_all(session, *, dry_run: bool = False) -> dict[int, tuple[str, str]]:
    """Прогон рекомпьюта по всем проектам. Возвращает {pid: (old, new)}
    только для тех, у кого статус изменился.
    """
    rows = (await session.execute(select(Project))).scalars().all()
    changes: dict[int, tuple[str, str]] = {}
    for p in rows:
        old, new, changed = await recompute_status(session, p, dry_run=dry_run)
        if changed:
            changes[p.id] = (old.value, new.value)
    return changes
