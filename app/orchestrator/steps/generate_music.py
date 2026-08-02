"""Шаг 10: фоновая музыка через outsee.io/audio (Suno 5.5).

1. GPT: voiceover.txt + сопроводительный текст → промт для Suno.
2. Outsee: поле «Название» = тема ролика, промт = ответ GPT → Generate → mp3.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from aiogram import Bot  # noqa: F401
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.outsee import OutseeBot
from app.models import Artifact, ArtifactKind, Project, ProjectStatus
from app.services import gpt_text_builder as gtb
from app.settings import settings


def _find_music_on_disk(music_dir: Path) -> Path | None:
    """Готовый mp3/wav в music/ — без Outsee (API audio нет)."""
    if not music_dir.is_dir():
        return None
    cands: list[Path] = []
    for pat in ("music_*.mp3", "music_*.wav", "*.mp3", "*.wav"):
        cands.extend(p for p in music_dir.glob(pat) if p.is_file() and p.stat().st_size > 1000)
    if not cands:
        return None
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0]


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_music:
        return
    logger.info("[#{}] generate_music starting", project.id)

    music_dir = project.data_dir / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    disk_music = _find_music_on_disk(music_dir)
    if disk_music is not None:
        logger.info(
            "[#{}] generate_music: музыка на диске → {} — пропускаю Outsee/Suno",
            project.id,
            disk_music.name,
        )
        session.add(
            Artifact(
                project_id=project.id,
                kind=ArtifactKind.music,
                uuid=uuid.uuid4().hex,
                path=str(disk_music.resolve()),
            )
        )
        await session.flush()
        from app.services.post_step_validate import finalize_or_retry

        if not await finalize_or_retry(
            session,
            project,
            step="music",
            ready_status=ProjectStatus.music_ready,
            running_status=ProjectStatus.generating_music,
        ):
            return
        project.status = ProjectStatus.music_ready
        await session.flush()
        logger.info("[#{}] generate_music done (disk) → {}", project.id, disk_music.name)
        return

    voiceover_path = project.data_dir / "voiceover.txt"
    voiceover_text = ""
    if voiceover_path.exists():
        voiceover_text = voiceover_path.read_text(encoding="utf-8").strip()
    if not voiceover_text:
        raise RuntimeError("voiceover.txt не найден — сначала шаг «Закадровый текст»")

    title = (project.topic or "").strip()
    if not title:
        raise RuntimeError("не задана тема ролика (название для Suno)")

    chat_msg = gtb.get_effective_text(
        project,
        "music",
        voiceover_text=voiceover_text,
        voiceover_attached=voiceover_path.exists(),
    )

    logger.info("[#{}] generate_music: GPT API → Suno (voiceover + сопр. текст)", project.id)

    from app.services.gpt_client import get_gpt_client

    gpt = get_gpt_client()
    await gpt.new_conversation()
    logger.info("[#{}] generate_music: отправка voiceover.txt в GPT API", project.id)
    suno_prompt = await gpt.ask_with_files(
        chat_msg,
        [voiceover_path],
        timeout=900,
        project_id=project.id,
    )
    suno_prompt = (suno_prompt or "").strip()
    if len(suno_prompt) < 20:
        raise RuntimeError("GPT вернул слишком короткий промт для музыки")
    logger.info(
        "[#{}] generate_music: GPT ответ ({} симв.) → outsee Suno",
        project.id,
        len(suno_prompt),
    )

    short_uuid = uuid.uuid4().hex[:8]
    music_path = music_dir / f"music_{short_uuid}.mp3"
    prompt_id_prefix = f"[ID: P{project.id}-MUSIC-{short_uuid}]"

    async with browser_session() as bs:
        outsee = OutseeBot(bs)
        await outsee.generate_music(
            suno_prompt,
            music_path,
            title=title,
            timeout=900,
            prompt_id_prefix=prompt_id_prefix,
            project_id=project.id,
        )

    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.music,
            uuid=uuid.uuid4().hex,
            path=str(music_path),
        )
    )
    await session.flush()

    from app.services.post_step_validate import finalize_or_retry

    if not await finalize_or_retry(
        session,
        project,
        step="music",
        ready_status=ProjectStatus.music_ready,
        running_status=ProjectStatus.generating_music,
    ):
        return

    project.status = ProjectStatus.music_ready
    await session.flush()
    logger.info("[#{}] generate_music done → {}", project.id, music_path.name)

    if settings.fleet_enabled and (settings.fleet_role or "").lower() == "agent":
        from app.fleet.montage_queue import maybe_mark_for_fleet_montage

        await maybe_mark_for_fleet_montage(session, project)
    elif settings.fleet_enabled and settings.fleet_montage_hub:
        from app.fleet.montage_queue import maybe_mark_for_fleet_montage

        await maybe_mark_for_fleet_montage(session, project)
