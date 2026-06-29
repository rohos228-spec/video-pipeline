"""Подготовка к повторному монтажу: сброс ASR/final, перерегистрация voice_full.

Usage:
  python scripts/remontage_prep.py 15
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db import session_scope
from app.models import Artifact, ArtifactKind, Project
from app.services.artifact_recovery import find_voice_full_on_disk, recover_audio_from_disk


def _unlink_quiet(path: Path, *, label: str) -> bool:
    try:
        path.unlink(missing_ok=True)
        print(f"  deleted {label}: {path.name}")
        return True
    except PermissionError:
        print(
            f"  skip {label} (файл занят — закрой плеер/превью): {path.name}"
        )
        return False
    except OSError as exc:
        print(f"  skip {label} ({exc}): {path.name}")
        return False


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id", type=int)
    args = parser.parse_args()

    async with session_scope() as session:
        project = await session.get(Project, args.project_id)
        if project is None:
            raise SystemExit(f"project #{args.project_id} not found")

        data = project.data_dir
        audio_dir = data / "audio"
        print(f"data: {data}")

        removed_whisper = 0
        for art in (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == args.project_id,
                    Artifact.kind == ArtifactKind.whisper_words,
                )
            )
        ).scalars():
            p = Path(art.path) if art.path else None
            if p and p.is_file():
                _unlink_quiet(p, label="artifact")
            await session.delete(art)
            removed_whisper += 1

        removed_final = 0
        for art in (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == args.project_id,
                    Artifact.kind.in_(
                        (ArtifactKind.final_video, ArtifactKind.subtitle)
                    ),
                )
            )
        ).scalars():
            p = Path(art.path) if art.path else None
            if p and p.is_file():
                _unlink_quiet(p, label="artifact")
            await session.delete(art)
            removed_final += 1

        if audio_dir.is_dir():
            for pattern in ("words_*.json", "*.asr_mono.*"):
                for p in audio_dir.glob(pattern):
                    _unlink_quiet(p, label="disk")

        final_dir = data / "final"
        if final_dir.is_dir():
            for p in final_dir.glob("*.mp4"):
                _unlink_quiet(p, label="final")

        voice = find_voice_full_on_disk(audio_dir)
        if voice is None:
            raise SystemExit(
                "Нет voice_full в audio/ (mp3/wav). Положи файл и запусти снова."
            )
        print(f"  voice_full: {voice.name} ({voice.stat().st_size // 1024} KB)")

        voice_res = voice.resolve()
        audio_arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == args.project_id,
                    Artifact.kind == ArtifactKind.audio,
                )
            )
        ).scalars().all()
        for a in audio_arts:
            p = Path(a.path) if a.path else None
            if p is None or not p.is_file() or p.resolve() != voice_res:
                await session.delete(a)

        await recover_audio_from_disk(session, project)
        await session.flush()
        print(f"  cleared whisper_words rows: {removed_whisper}")
        print(f"  cleared final/subtitle rows: {removed_final}")


if __name__ == "__main__":
    asyncio.run(main())
