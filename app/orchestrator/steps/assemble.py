"""Шаг 11: финальная сборка ролика FFmpeg — нарезка клипов по реальным
длительностям из Whisper, concat, наложение mp3, ASS-субтитры, затем HITL
approve_final.
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
from app.services.assembly import ClipSpec, assemble, make_simple_ass
from app.services.hitl import send_hitl_video
from app.services.mapper import FrameTiming
from app.services.subtitles import build_subtitle_cues_from_cells
from app.services.whisper import load_words_json
from app.storage.plan_sheet_v8 import read_plan_voiceover_cells


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.assembling:
        return
    logger.info("[#{}] assemble starting", project.id)

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
        raise RuntimeError("нет артефакта аудио")

    whisper_art = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.kind == ArtifactKind.whisper_words,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if whisper_art is None:
        raise RuntimeError(
            "нет word-level таймкодов Whisper — перезапустите шаг «Аудио» перед сборкой"
        )
    words = load_words_json(Path(whisper_art.path))
    if not words:
        raise RuntimeError("Whisper не вернул слова для субтитров")

    cells = read_plan_voiceover_cells(project, [fr.number for fr in frames])
    if not any(text.strip() for _, text in cells):
        raise RuntimeError(
            "нет текста на листе «план» (строка 49) — субтитры и синхронизация "
            "строятся только из ячеек Excel (одна ячейка = одно видео)"
        )

    frame_timings = [
        FrameTiming(
            fr.number,
            float(fr.start_ts or 0.0),
            float(fr.end_ts or 0.0),
            float(fr.duration_seconds or 0.0),
        )
        for fr in frames
    ]
    if any(t.duration <= 0 for t in frame_timings):
        raise RuntimeError(
            "у части кадров нет длительности — перезапустите шаг «Аудио» "
            "(Whisper + таймкоды Excel)"
        )

    clips: list[ClipSpec] = []
    for fr in frames:
        video_art = (
            await session.execute(
                select(Artifact)
                .where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_video,
                )
                .order_by(Artifact.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if video_art is None:
            raise RuntimeError(f"нет клипа для кадра {fr.number}")
        duration = float(fr.duration_seconds or 0.0)
        if duration <= 0:
            raise RuntimeError(f"длительность кадра {fr.number} ≤ 0")
        clips.append(ClipSpec(src=Path(video_art.path), duration=duration))

    subs_dir = project.data_dir / "subs"
    subs_path = subs_dir / f"subs_{uuid.uuid4().hex[:8]}.ass"
    sub_entries = build_subtitle_cues_from_cells(
        cells,
        words,
        frame_timings,
        max_words=2,
    )
    if not sub_entries:
        raise RuntimeError("не удалось построить субтитры из Excel + Whisper")
    make_simple_ass(sub_entries, subs_path)
    session.add(Artifact(
        project_id=project.id, kind=ArtifactKind.subtitle,
        uuid=uuid.uuid4().hex, path=str(subs_path),
    ))

    out_dir = project.data_dir / "final"
    out_path = out_dir / f"{project.slug}.mp4"
    await assemble(clips, Path(audio.path), out_path, subtitles_ass=subs_path)

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
