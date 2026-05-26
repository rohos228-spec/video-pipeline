"""Сервис «🔁 Прогнать шаг с нуля».

Удаляет данные, которые конкретный шаг произвёл, плюс ВСЕ downstream-
данные (т.к. они зависят от output'а этого шага). После сброса
вызывается `compute_actual_status()` — он установит `project.status`
на правильный ready-уровень.

Использование:
    from app.services.reset_step import reset_step

    summary = await reset_step(session, project, step_code)
    # project.status уже выставлен в правильный ready-уровень,
    # юзер может ткнуть «▶ Запустить шаг» и шаг пойдёт с нуля.

Шаги (порядок pipeline'а):
    plan → script → split → hero/items → enrich_1..5 → img_pr →
    img → anim_pr → video → audio → assemble

Wrapper-коды:
    objects → hero + items
    enrich  → enrich_1..5
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    FrameStatus,
    Project,
)
from app.services.project_state import compute_actual_status

# ---------------------------------------------------------------------------
# Внутренние «wipe»-функции — каждая чистит выход одного логического шага.

async def _wipe_artifacts_by_kind(
    session: AsyncSession,
    project: Project,
    *kinds: ArtifactKind,
) -> dict[str, int]:
    """Удалить артефакты указанных типов + файлы на диске."""
    arts = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == project.id,
                Artifact.kind.in_(kinds),
            )
        )
    ).scalars().all()
    files_deleted = 0
    for a in arts:
        if a.path:
            p = Path(a.path)
            if p.exists():
                try:
                    p.unlink()
                    files_deleted += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "[#{}] reset_step: не смог удалить файл {}: {}",
                        project.id, p, e,
                    )
        await session.delete(a)
    return {"artifacts": len(arts), "files": files_deleted}


async def _wipe_plan(session: AsyncSession, project: Project) -> dict[str, Any]:
    changed = False
    if project.general_plan is not None:
        project.general_plan = None
        changed = True
    return {"general_plan_cleared": changed}


async def _wipe_script(session: AsyncSession, project: Project) -> dict[str, Any]:
    changed = False
    if project.script_text is not None:
        project.script_text = None
        changed = True
    # voiceover.txt — артефакт на диске (если есть)
    voice_path = project.data_dir / "voiceover.txt"
    voice_deleted = False
    if voice_path.exists():
        try:
            voice_path.unlink()
            voice_deleted = True
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[#{}] reset_step.script: не удалил {}: {}",
                project.id, voice_path, e,
            )
    return {"script_text_cleared": changed, "voiceover_txt_deleted": voice_deleted}


async def _wipe_split(session: AsyncSession, project: Project) -> dict[str, Any]:
    """Удалить все Frame проекта. Артефакты с frame_id каскадно удаляются
    через ondelete=CASCADE. Файлы соответствующих артефактов чистим
    отдельно ДО удаления frame'ов."""
    # сначала собираем пути файлов кадровых артефактов
    frame_arts = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == project.id,
                Artifact.frame_id.isnot(None),
            )
        )
    ).scalars().all()
    files_deleted = 0
    for a in frame_arts:
        if a.path:
            p = Path(a.path)
            if p.exists():
                try:
                    p.unlink()
                    files_deleted += 1
                except Exception:  # noqa: BLE001
                    pass
    # теперь сами frame'ы (cascade-каскад удалит остальное)
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id)
        )
    ).scalars().all()
    for fr in frames:
        await session.delete(fr)
    return {"frames_deleted": len(frames), "frame_artifact_files": files_deleted}


async def _wipe_hero(session: AsyncSession, project: Project) -> dict[str, Any]:
    """Сброс шага 4a «Персонажи»: удалить hero_reference артефакты.
    Описания героев (project.hero_descriptions) и вариации НЕ трогаем —
    их юзер вводил руками; повторный запуск шага сгенерит ИЗ ЭТИХ ЖЕ
    описаний. Если нужен полный сброс с описаниями — это делается через
    отдельное hero_reset_menu_kb («🎨 Сменить стиль»)."""
    return await _wipe_artifacts_by_kind(
        session, project, ArtifactKind.hero_reference
    )


async def _wipe_items(session: AsyncSession, project: Project) -> dict[str, Any]:
    """Сброс шага 4b «Предметы»: удалить item_reference артефакты."""
    return await _wipe_artifacts_by_kind(
        session, project, ArtifactKind.item_reference
    )


def _enrich_slot_wiper(slot: int):
    """Сброс enrich-слота N (1..5).

    Enrich-шаги не имеют отдельного «своего» поля в БД — они обновляют
    project.xlsx через ChatGPT, потом xlsx_sync перетягивает изменения
    в frame.image_prompt/animation_prompt/etc. Поэтому конкретно для
    данного слота единственный честный сброс — это:
    1) убрать override-выбор шаблона enrich_<slot> у проекта
    2) downstream шаги (img_pr/img/anim_pr/...) будут сброшены отдельно
       вызывающим кодом reset_step (мы не дублируем это здесь).
    """
    async def _wipe(session: AsyncSession, project: Project) -> dict[str, Any]:
        overrides = dict(project.prompt_overrides or {})
        code = f"enrich_{slot}"
        had = code in overrides
        if had:
            overrides.pop(code, None)
            project.prompt_overrides = overrides
        return {"override_cleared": had, "slot": slot}
    return _wipe


async def _wipe_img_pr(session: AsyncSession, project: Project) -> dict[str, Any]:
    """Сброс шага 6 «Промты картинок»: frame.image_prompt = None
    у всех кадров."""
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id)
        )
    ).scalars().all()
    cleared = 0
    status_reset = 0
    for fr in frames:
        if fr.image_prompt:
            fr.image_prompt = None
            cleared += 1
        if fr.status is FrameStatus.image_prompt_ready:
            fr.status = FrameStatus.planned
            status_reset += 1
    return {"frames_cleared": cleared, "frames_status_reset": status_reset}


async def _wipe_images(session: AsyncSession, project: Project) -> dict[str, Any]:
    """Сброс шага 7 «Картинки»:
      - удалить scene_image артефакты + файлы
      - дочистить data/projects/<slug>/scenes/*.png
      - сбросить frame.status в image_prompt_ready (или planned, если
        промт пропал) и снять fail_reason из attrs.
    """
    art_stats = await _wipe_artifacts_by_kind(
        session, project, ArtifactKind.scene_image
    )
    # дочистим .png в scenes/, если что-то осталось
    scenes_dir = project.data_dir / "scenes"
    extra_files = 0
    if scenes_dir.exists():
        for p in scenes_dir.glob("*.png"):
            try:
                p.unlink()
                extra_files += 1
            except Exception:  # noqa: BLE001
                pass
    # сбрасываем frame.status
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id)
        )
    ).scalars().all()
    frames_reset = 0
    for fr in frames:
        if fr.status in (
            FrameStatus.image_generated,
            FrameStatus.image_approved,
            FrameStatus.video_generated,
            FrameStatus.video_approved,
            FrameStatus.failed,
            FrameStatus.done,
        ):
            new_status = (
                FrameStatus.image_prompt_ready
                if fr.image_prompt
                else FrameStatus.planned
            )
            fr.status = new_status
            frames_reset += 1
        # снять fail_reason если был
        if fr.attrs and isinstance(fr.attrs, dict) and "fail_reason" in fr.attrs:
            attrs = dict(fr.attrs)
            attrs.pop("fail_reason", None)
            fr.attrs = attrs
    return {
        **art_stats,
        "extra_files": extra_files,
        "frames_reset": frames_reset,
    }


async def _wipe_anim_pr(session: AsyncSession, project: Project) -> dict[str, Any]:
    """Сброс шага 8 «Промты анимации»: animation_prompt + статус кадра."""
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id)
        )
    ).scalars().all()
    cleared = 0
    status_reset = 0
    for fr in frames:
        had_prompt = bool((fr.animation_prompt or "").strip())
        if had_prompt:
            fr.animation_prompt = None
            cleared += 1
        if fr.status is FrameStatus.animation_prompt_ready:
            fr.status = FrameStatus.image_approved
            status_reset += 1
    return {"frames_cleared": cleared, "frames_status_reset": status_reset}


async def _wipe_videos(session: AsyncSession, project: Project) -> dict[str, Any]:
    """Сброс шага 9 «Видео»: scene_video артефакты + файлы. Также
    сбрасываем frame.status video_* → animation_prompt_ready."""
    arts = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == project.id,
                Artifact.kind == ArtifactKind.scene_video,
            )
        )
    ).scalars().all()
    frame_ids_with_video = {a.frame_id for a in arts if a.frame_id is not None}
    art_stats = await _wipe_artifacts_by_kind(
        session, project, ArtifactKind.scene_video
    )
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id)
        )
    ).scalars().all()
    frames_reset = 0
    for fr in frames:
        had_video = fr.id in frame_ids_with_video
        if not had_video and fr.status not in (
            FrameStatus.video_generated,
            FrameStatus.video_approved,
            FrameStatus.done,
        ):
            continue
        fr.status = (
            FrameStatus.animation_prompt_ready
            if fr.animation_prompt
            else FrameStatus.image_approved
        )
        frames_reset += 1
    return {**art_stats, "frames_reset": frames_reset}


async def _wipe_audio(session: AsyncSession, project: Project) -> dict[str, Any]:
    """Сброс шага 10 «Аудио»: audio + whisper_words артефакты."""
    return await _wipe_artifacts_by_kind(
        session,
        project,
        ArtifactKind.audio,
        ArtifactKind.whisper_words,
    )


async def _wipe_assemble(session: AsyncSession, project: Project) -> dict[str, Any]:
    """Сброс шага 11 «Финальная сборка»: final_video + subtitle артефакты."""
    return await _wipe_artifacts_by_kind(
        session,
        project,
        ArtifactKind.final_video,
        ArtifactKind.subtitle,
    )


# ---------------------------------------------------------------------------
# Порядок в pipeline: индексы определяют каскад. Сбрасываем step N
# и всё что после.

_PIPELINE_RESET_LEVELS: list[tuple[str, Any]] = [
    ("plan",      _wipe_plan),
    ("script",    _wipe_script),
    ("split",     _wipe_split),
    ("hero",      _wipe_hero),
    ("items",     _wipe_items),
    ("enrich_1",  _enrich_slot_wiper(1)),
    ("enrich_2",  _enrich_slot_wiper(2)),
    ("enrich_3",  _enrich_slot_wiper(3)),
    ("enrich_4",  _enrich_slot_wiper(4)),
    ("enrich_5",  _enrich_slot_wiper(5)),
    ("img_pr",    _wipe_img_pr),
    ("img",       _wipe_images),
    ("anim_pr",   _wipe_anim_pr),
    ("video",     _wipe_videos),
    ("audio",     _wipe_audio),
    ("assemble",  _wipe_assemble),
]

# Wrapper-коды раскрываются в подшаги (минимальный индекс берётся как
# точка старта каскада).
_WRAPPER_TO_CODES: dict[str, list[str]] = {
    "objects": ["hero", "items"],
    "enrich":  ["enrich_1", "enrich_2", "enrich_3", "enrich_4", "enrich_5"],
}


def _resolve_start_index(step_code: str) -> int | None:
    """Найти стартовый индекс каскада в _PIPELINE_RESET_LEVELS для step_code.
    Возвращает None если код неизвестен."""
    candidates = _WRAPPER_TO_CODES.get(step_code, [step_code])
    keys = [k for k, _ in _PIPELINE_RESET_LEVELS]
    indices = [keys.index(c) for c in candidates if c in keys]
    if not indices:
        return None
    return min(indices)


# Какие шаги вообще поддерживают сброс. Используется в TG для решения,
# показывать ли кнопку «🔁 Прогнать шаг с нуля».
RESET_SUPPORTED_STEP_CODES: frozenset[str] = frozenset({
    "plan", "script", "split",
    "objects", "hero", "items",
    "enrich",
    "enrich_1", "enrich_2", "enrich_3", "enrich_4", "enrich_5",
    "img_pr", "img", "anim_pr", "video", "audio", "assemble",
})


def is_reset_supported(step_code: str) -> bool:
    return step_code in RESET_SUPPORTED_STEP_CODES


_STEP_WIPE_BY_CODE: dict[str, Any] = dict(_PIPELINE_RESET_LEVELS)


async def clear_step_outputs_for_rerun(
    session: AsyncSession,
    project: Project,
    step_code: str,
) -> dict[str, Any]:
    """Очистить только выход этого шага (без downstream) перед повторным запуском.

    Вызывается из `start_step` при «▶ Запустить шаг»: каждый перезапуск
    идёт с нуля (промт+файлы в ChatGPT, все кадры/предметы), но данные
    следующих шагов не трогаем — для полного каскада есть reset_step.
    """
    if not is_reset_supported(step_code):
        return {}

    codes = _WRAPPER_TO_CODES.get(step_code, [step_code])
    summary: dict[str, Any] = {}
    for code in codes:
        handler = _STEP_WIPE_BY_CODE.get(code)
        if handler is None:
            continue
        try:
            details = await handler(session, project)
            if details:
                summary[code] = details
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[#{}] clear_step_outputs_for_rerun: {} failed: {}",
                project.id,
                code,
                e,
            )
            summary[code] = {"error": str(e)}
    if summary:
        await session.flush()
        logger.info(
            "[#{}] clear_step_outputs_for_rerun: step={} cleared={}",
            project.id,
            step_code,
            list(summary.keys()),
        )
    return summary


# ---------------------------------------------------------------------------
# Публичная функция.

async def reset_step(
    session: AsyncSession,
    project: Project,
    step_code: str,
) -> dict[str, Any]:
    """Сбросить шаг `step_code` и все downstream-данные.

    После сброса `project.status` пересчитан через `compute_actual_status`
    (бэйпасс running-чека — нам надо именно перезаписать статус, даже
    если шаг сейчас «бежит» с точки зрения БД).

    Возвращает summary: {step_key: {details}, ..., "__project_status":
    "<новый_статус>", "__steps_wiped": [step_keys]}.
    """
    start_idx = _resolve_start_index(step_code)
    if start_idx is None:
        return {"error": f"unknown step: {step_code}"}

    summary: dict[str, Any] = {}
    steps_wiped: list[str] = []
    # Идём с самого глубокого downstream к самому верхнему шагу — это
    # делает каскад FK-безопасным (если бы у нас были не-CASCADE'ные FK).
    for key, handler in reversed(_PIPELINE_RESET_LEVELS[start_idx:]):
        try:
            details = await handler(session, project)
            if details:
                summary[key] = details
                steps_wiped.append(key)
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[#{}] reset_step: handler {} упал: {}",
                project.id, key, e,
            )
            summary[key] = {"error": str(e)}

    await session.flush()

    # Пересчёт project.status. Не используем recompute_status, т.к. он
    # пропускает running-статусы — а нам как раз надо переписать
    # generating_X после сброса.
    new_status = await compute_actual_status(session, project)
    old_status = project.status
    project.status = new_status
    await session.flush()

    summary["__project_status"] = new_status.value
    summary["__project_status_was"] = old_status.value
    summary["__steps_wiped"] = steps_wiped

    logger.info(
        "[#{}] reset_step: code={} steps_wiped={} status: {} → {}",
        project.id, step_code, steps_wiped,
        old_status.value, new_status.value,
    )

    return summary
