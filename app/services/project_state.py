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

from sqlalchemy import func, select

from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    Project,
    ProjectStatus,
)

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


async def compute_actual_status(session, project: Project) -> ProjectStatus:
    """Вернуть наивысший status, который подтверждён данными в БД.

    Логика «снизу вверх»: смотрим сначала самый ранний прогресс
    (general_plan), потом всё дальше. Если данных нет — возвращаем
    предыдущий уровень.

    Никогда не возвращает `paused`/`failed`/`*ing` (running) — только
    «контрольные точки» (ready / new / assembled / published).
    """
    pid = project.id
    has_plan = bool(project.general_plan)
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
    skip_hero = project.hero_mode == "no_hero" or (project.hero_count or 0) == 0
    if not skip_hero and hero_arts == 0 and not has_hero_descr:
        # Нет ни сгенерированных hero-картинок, ни описания — шаг 4 не
        # проходил. Стоп на frames_ready.
        return ProjectStatus.frames_ready
    # hero ✓ (минимум описание есть; если артефактов нет — шаг 4 ещё
    # запустится). Логика консервативная: считаем шаг 4 пройденным
    # только при наличии hero_arts.
    if not skip_hero and hero_arts == 0:
        return ProjectStatus.frames_ready
    # image_prompts: считаем готовыми, если ВСЕ frames имеют image_prompt.
    if fr_with_img_prompt < fr_total:
        return ProjectStatus.hero_ready
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
