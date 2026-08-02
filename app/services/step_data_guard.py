"""Проверка данных в БД/на диске перед переходом на следующий running-шаг."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, Project, ProjectStatus
from app.services.artifact_recovery import (
    recover_audio_from_disk,
    recover_scene_videos_from_disk,
)
from app.services.plan_validation import is_meaningful_general_plan
from app.services.project_state import compute_actual_status, is_running_status
from app.services.xlsx_v8_import import read_v8_active_frame_count
from app.telegram.menu import status_order

# Шаги, которые не требуют уже готовых кадров с voiceover в Excel/БД.
# enriching_*: excel_gpt на канвасе может идти сразу после script (до split) —
# кадры ещё не созданы, вход = project.xlsx + voiceover.txt.
_NO_FRAMES_REQUIRED: frozenset[ProjectStatus] = frozenset(
    {
        ProjectStatus.planning,
        ProjectStatus.scripting,
        ProjectStatus.splitting,
        ProjectStatus.generating_music,
        ProjectStatus.enriching_1,
        ProjectStatus.enriching_2,
        ProjectStatus.enriching_3,
        ProjectStatus.enriching_4,
        ProjectStatus.enriching_5,
        # Герои/предметы на графе тоже часто до разбивки.
        ProjectStatus.generating_hero,
        ProjectStatus.generating_items,
    }
)


async def _count_kind(session: AsyncSession, project_id: int, kind: ArtifactKind) -> int:
    return (
        await session.execute(
            select(func.count(Artifact.id)).where(
                Artifact.project_id == project_id,
                Artifact.kind == kind,
            )
        )
    ).scalar_one()


async def _frames_with_voiceover(session: AsyncSession, project: Project) -> int:
    xlsx = project.data_dir / "project.xlsx"
    n = read_v8_active_frame_count(xlsx) if xlsx.is_file() else 0
    if n > 0:
        return n
    return (
        await session.execute(
            select(func.count(Frame.id)).where(
                Frame.project_id == project.id,
                Frame.voiceover_text.isnot(None),
                Frame.voiceover_text != "",
            )
        )
    ).scalar_one()


async def ready_status_confirmed_by_data(
    session: AsyncSession,
    project: Project,
    ready_status: ProjectStatus,
) -> bool:
    """True если данные в БД подтверждают *_ready (не шаблон/заглушка)."""
    # frames_ready: кадры есть + (split_completed ИЛИ статус уже frames_ready).
    # Не требуем длинный general_plan и НЕ смотрим stale NodeRun.done —
    # иначе recompute после разбивки откатывает в `new` и цепочка встаёт.
    if ready_status is ProjectStatus.frames_ready:
        fr_n = (
            await session.execute(
                select(func.count(Frame.id)).where(Frame.project_id == project.id)
            )
        ).scalar_one()
        if int(fr_n or 0) >= 1:
            meta = project.meta if isinstance(project.meta, dict) else {}
            if meta.get("split_completed") or project.status is ProjectStatus.frames_ready:
                return True

    # enrich_*_ready до split: кадры могут отсутствовать — смотрим meta / статус.
    if ready_status in (
        ProjectStatus.enrich_1_ready,
        ProjectStatus.enrich_2_ready,
        ProjectStatus.enrich_3_ready,
        ProjectStatus.enrich_4_ready,
        ProjectStatus.enrich_5_ready,
    ):
        from app.services.project_state import (
            _enrich_meta_allowed_for_status,
            _enrich_ready_from_meta,
        )

        if _enrich_meta_allowed_for_status(project):
            enrich_st = _enrich_ready_from_meta(project)
            if enrich_st is not None and status_order(enrich_st) >= status_order(
                ready_status
            ):
                return True
            if project.status is ready_status:
                return True

    # Ручная озвучка voice_full.wav: compute_actual_status может быть ниже
    # (нет всех scene_image), но audio_ready уже законно — не откатывать.
    if ready_status in (ProjectStatus.audio_ready, ProjectStatus.music_ready):
        await recover_audio_from_disk(session, project)
        from app.services.frame_audio import find_voice_full_on_disk

        voice = find_voice_full_on_disk(
            project.data_dir,
            meta=project.meta if isinstance(project.meta, dict) else None,
        )
        audio_ok = voice is not None and voice.is_file()
        if not audio_ok:
            art = (
                await session.execute(
                    select(Artifact)
                    .where(
                        Artifact.project_id == project.id,
                        Artifact.kind == ArtifactKind.audio,
                    )
                    .order_by(Artifact.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            audio_ok = bool(
                art is not None and art.path and Path(art.path).is_file()
            )
        if ready_status is ProjectStatus.audio_ready and audio_ok:
            return True
        if ready_status is ProjectStatus.music_ready and audio_ok:
            music_n = await _count_kind(session, project.id, ArtifactKind.music)
            if music_n > 0:
                return True

    actual = await compute_actual_status(session, project)
    return status_order(actual) >= status_order(ready_status)


async def can_enter_running(
    session: AsyncSession,
    project: Project,
    target: ProjectStatus,
) -> tuple[bool, str, ProjectStatus | None]:
    """Можно ли ставить `target` (running) по фактическим данным.

    Возвращает (ok, reason, suggested_status при ok=False).
    """
    if target is ProjectStatus.scripting:
        if not is_meaningful_general_plan(project.general_plan):
            return False, "сценарий не готов (нет general_plan)", ProjectStatus.new

    if target is ProjectStatus.splitting:
        if not (project.script_text or "").strip():
            voice = project.data_dir / "voiceover.txt"
            if not voice.is_file() or voice.stat().st_size < 50:
                actual = await compute_actual_status(session, project)
                return False, "закадровый текст не готов", actual

    if target in (
        ProjectStatus.planning,
        ProjectStatus.scripting,
        ProjectStatus.splitting,
    ):
        return True, "", None

    if target not in _NO_FRAMES_REQUIRED:
        need_frames = await _frames_with_voiceover(session, project)
        if need_frames == 0:
            from app.services.ensure_frames_from_disk import (
                discover_frame_numbers_on_disk,
                ensure_frames_from_disk_media,
            )

            if discover_frame_numbers_on_disk(project.data_dir):
                created = await ensure_frames_from_disk_media(session, project)
                if created:
                    logger.info(
                        "[#{}] step_data_guard: создано {} кадров с диска перед {}",
                        project.id,
                        len(created),
                        target.value,
                    )
                need_frames = await _frames_with_voiceover(session, project)
        if need_frames == 0:
            actual = await compute_actual_status(session, project)
            return False, "нет кадров с voiceover", actual

    need_frames = await _frames_with_voiceover(session, project)

    if target is ProjectStatus.generating_images:
        # Нельзя заходить в img из ранних статусов только из‑за leftover
        # image_prompt (старый прогон) — иначе auto_advance прыгает через
        # script/split/hero/... сразу в генерацию картинок.
        if status_order(project.status) >= status_order(
            ProjectStatus.image_prompts_ready
        ):
            return True, "", None
        # Soft resume: статус чуть отстаёт (ещё generating_image_prompts),
        # но промпты на кадрах уже есть.
        if status_order(project.status) >= status_order(
            ProjectStatus.generating_image_prompts
        ):
            frames = (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.number)
                )
            ).scalars().all()
            with_prompt = sum(
                1
                for fr in frames
                if (getattr(fr, "image_prompt", None) or "").strip()
            )
            if with_prompt > 0:
                return True, "", None
        return (
            False,
            "нет image prompts (сначала img_pr)",
            ProjectStatus.generating_image_prompts,
        )

    if target is ProjectStatus.generating_animation_prompts:
        imgs = await _count_kind(session, project.id, ArtifactKind.scene_image)
        if imgs == 0:
            return (
                False,
                "нет картинок сцен (сначала img)",
                ProjectStatus.image_prompts_ready,
            )
        return True, "", None

    if target is ProjectStatus.generating_videos:
        imgs = await _count_kind(session, project.id, ArtifactKind.scene_image)
        if imgs < need_frames:
            return (
                False,
                f"картинок {imgs}/{need_frames}",
                ProjectStatus.image_prompts_ready,
            )
        return True, "", None

    if target is ProjectStatus.generating_audio:
        await recover_scene_videos_from_disk(session, project)
        vids = await _count_kind(session, project.id, ArtifactKind.scene_video)
        if vids < need_frames:
            return (
                False,
                f"видео-клипов {vids}/{need_frames}",
                ProjectStatus.videos_ready,
            )
        return True, "", None

    if target is ProjectStatus.generating_music:
        voice = project.data_dir / "voiceover.txt"
        if not voice.is_file():
            return False, "нет voiceover.txt", ProjectStatus.script_ready
        if not (project.topic or "").strip():
            return False, "не задана тема ролика", ProjectStatus.new
        return True, "", None

    if target is ProjectStatus.assembling:
        from app.services.ensure_frames_from_disk import ensure_frames_from_disk_media

        await ensure_frames_from_disk_media(session, project)
        await recover_scene_videos_from_disk(session, project)
        await recover_audio_from_disk(session, project)
        vids = await _count_kind(session, project.id, ArtifactKind.scene_video)
        if vids == 0:
            return (
                False,
                "нет ни одного видео-клипа",
                ProjectStatus.generating_videos,
            )
        audio = (
            await session.execute(
                select(Artifact)
                .where(
                    Artifact.project_id == project.id,
                    Artifact.kind == ArtifactKind.audio,
                )
                .order_by(Artifact.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if audio is None or not Path(audio.path).is_file():
            return False, "нет артефакта аудио", ProjectStatus.generating_audio
        return True, "", None

    return True, "", None


async def clamp_status_to_data(
    session: AsyncSession, project: Project
) -> ProjectStatus | None:
    """Если status «впереди» данных — откатить к compute_actual_status.

    Железо: никогда не сбрасывать frames_ready/script_ready → new при живых
    кадрах/script — иначе auto_advance откатывает на предыдущую ноду.
    """
    from app.services.gen_queue_run import is_user_stopped
    from app.services.project_state import clear_stale_downstream_meta

    if is_user_stopped(project):
        return None
    # Running-шаги не трогаем (как recompute_status): иначе при устаревшем
    # объекте в сессии после advance в другой сессии откатываем scripting→
    # plan_ready и auto_advance заново шлёт тот же запрос в GPT.
    if is_running_status(project.status):
        return None

    old = project.status
    # Сначала проверяем: текущий ready уже подтверждён — не трогаем и не
    # чистим meta (иначе clear_stale сносит split_completed до проверки).
    if old.value.endswith("_ready") and await ready_status_confirmed_by_data(
        session, project, old
    ):
        return None

    clear_stale_downstream_meta(project)
    actual = await compute_actual_status(session, project)

    # Тот же BLOCKED, что в recompute_status: * → new при script/кадрах.
    if actual is ProjectStatus.new and old is not ProjectStatus.new:
        fr_n = (
            await session.execute(
                select(func.count(Frame.id)).where(Frame.project_id == project.id)
            )
        ).scalar_one()
        has_script = bool((project.script_text or "").strip())
        if int(fr_n or 0) >= 1 or has_script:
            logger.warning(
                "[#{}] clamp_status_to_data: BLOCKED {} → new "
                "(кадров={}, script={}) — иначе предыдущая нода стартует снова",
                project.id,
                old.value,
                int(fr_n or 0),
                has_script,
            )
            return None

    if status_order(old) > status_order(actual):
        # Повторная страховка: confirmed ready не откатываем.
        if old.value.endswith("_ready") and await ready_status_confirmed_by_data(
            session, project, old
        ):
            return None
        project.status = actual
        await session.flush()
        logger.warning(
            "[#{}] clamp_status_to_data: {} → {} (статус опережал данные)",
            project.id,
            old.value,
            actual.value,
        )
        return actual
    return None
