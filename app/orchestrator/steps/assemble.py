"""Шаг 11: финальная сборка FFmpeg.

1. Подтянуть project.xlsx (лист «план», R49 закадровый текст).
2. Клипы по номеру кадра (clip_NNN_*.mp4 или артефакт scene_video).
3. Длительности из Whisper; подгонка клипов (обрезка / замедление ≤15%).
4. Звук клипов −22 dB, озвучка + BGM −17 dB, ASS-субтитры, HITL approve_final.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    HITLKind,
    Project,
    ProjectStatus,
)
from app.services.assembly import assemble, make_simple_ass
from app.services.assembly_inputs import (
    bgm_enabled_for_project,
    build_clip_specs,
    load_plan_from_xlsx,
    resolve_bgm_path,
)
from app.services.hitl import send_hitl_video
from app.services.xlsx_v8_import import import_v8_xlsx


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.assembling:
        return
    logger.info("[#{}] assemble starting", project.id)

    xlsx_path = project.data_dir / "project.xlsx"
    if xlsx_path.exists():
        try:
            await import_v8_xlsx(
                session, project, xlsx_path,
                keep_fields=True,
                update_frames_voiceover=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[#{}] xlsx sync before assemble: {}", project.id, e)

    plan_columns = await load_plan_from_xlsx(project)

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров")

    audio = (
        await session.execute(
            select(Artifact)
            .where(Artifact.project_id == project.id, Artifact.kind == ArtifactKind.audio)
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if audio is None:
        raise RuntimeError(
            "нет артефакта озвучки — сначала шаг 10 (ElevenLabs + Whisper)"
        )

    clips = await build_clip_specs(session, project, list(frames), plan_columns)

    bgm_path: Path | None = None
    if bgm_enabled_for_project(project):
        bgm_path = resolve_bgm_path(project)
        if bgm_path:
            logger.info("[#{}] BGM: {}", project.id, bgm_path)
        else:
            logger.info(
                "[#{}] BGM включён в настройках, файл не найден (положите audio/bgm*.mp3)",
                project.id,
            )

    subs_dir = project.data_dir / "subs"
    subs_path = subs_dir / f"subs_{uuid.uuid4().hex[:8]}.ass"
    make_simple_ass(
        [((fr.start_ts or 0.0), (fr.end_ts or 0.0), fr.voiceover_text or "") for fr in frames],
        subs_path,
    )
    session.add(Artifact(
        project_id=project.id, kind=ArtifactKind.subtitle,
        uuid=uuid.uuid4().hex, path=str(subs_path),
    ))

    out_dir = project.data_dir / "final"
    out_path = out_dir / f"{project.slug}.mp4"
    await assemble(
        clips,
        Path(audio.path),
        out_path,
        subtitles_ass=subs_path,
        bgm_path=bgm_path,
    )

    session.add(Artifact(
        project_id=project.id, kind=ArtifactKind.final_video,
        uuid=uuid.uuid4().hex, path=str(out_path),
    ))
    project.status = ProjectStatus.assembled
    await session.flush()

    await send_hitl_video(
        bot, session, project,
        kind=HITLKind.approve_final,
        video_path=str(out_path),
        caption=f"Финальный ролик #{project.id} готов. Одобрить и публиковать?",
        payload={"step": "final"},
    )
