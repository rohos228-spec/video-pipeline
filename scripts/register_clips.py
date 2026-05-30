"""Зарегистрировать ручные clip_*.mp4 в БД для проекта.

Папка клипов: data/videos/<slug>/videos/clip_001.mp4 … clip_030.mp4

  python scripts/register_clips.py 8
  python scripts/register_clips.py test3
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db import session_scope
from app.models import Artifact, ArtifactKind, Frame, FrameStatus, Project


def _clip_path(videos_dir: Path, frame_number: int) -> Path | None:
    n = frame_number
    candidates = [
        videos_dir / f"clip_{n:03d}.mp4",
        videos_dir / f"clip_{n:03d}.MP4",
        videos_dir / f"clip_{n}.mp4",
        videos_dir / f"clip_{n:02d}.mp4",
    ]
    for p in candidates:
        if p.is_file():
            return p
    matches = sorted(videos_dir.glob(f"clip_{n:03d}_*.mp4"))
    if matches:
        return matches[0]
    matches = sorted(videos_dir.glob(f"clip_{n}_*.mp4"))
    return matches[0] if matches else None


async def main(arg: str) -> int:
    async with session_scope() as session:
        if arg.isdigit():
            project = await session.get(Project, int(arg))
        else:
            project = (
                await session.execute(select(Project).where(Project.slug == arg))
            ).scalar_one_or_none()
        if project is None:
            print(f"проект не найден: {arg}")
            return 1

        videos_dir = project.data_dir / "videos"
        frames = (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
            )
        ).scalars().all()
        if not frames:
            print("нет кадров в проекте")
            return 1

        print(f"проект #{project.id} slug={project.slug}")
        print(f"папка клипов: {videos_dir}")

        registered = 0
        missing: list[int] = []
        for fr in frames:
            clip = _clip_path(videos_dir, fr.number)
            if clip is None:
                missing.append(fr.number)
                continue
            old = (
                await session.execute(
                    select(Artifact).where(
                        Artifact.project_id == project.id,
                        Artifact.frame_id == fr.id,
                        Artifact.kind == ArtifactKind.scene_video,
                    )
                )
            ).scalars().all()
            for a in old:
                await session.delete(a)
            session.add(
                Artifact(
                    project_id=project.id,
                    frame_id=fr.id,
                    kind=ArtifactKind.scene_video,
                    uuid=uuid.uuid4().hex,
                    path=str(clip.resolve()),
                )
            )
            fr.status = FrameStatus.video_approved
            registered += 1

        await session.flush()
        print(f"зарегистрировано: {registered}/{len(frames)}")
        if missing:
            print(f"нет файлов для кадров: {missing[:12]}{'…' if len(missing) > 12 else ''}")
            orphans = sorted(
                p.name
                for p in videos_dir.glob("*.mp4")
                if p.is_file() and not any(
                    _clip_path(videos_dir, fr.number) == p for fr in frames
                )
            )
            if orphans:
                print(f"лишние/не распознаны mp4 в папке: {orphans[:15]}")
        return 0 if not missing else 2


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/register_clips.py <project_id|slug>")
        raise SystemExit(1)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
