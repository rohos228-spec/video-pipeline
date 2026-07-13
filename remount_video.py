"""Перемонтаж ролика при сбитой синхронизации озвучки и кадров.

Пример — «почему идет дождь»:
    python3 -m remount_video --topic "дожд"
    python3 -m remount_video 12

Только выровнять озвучку, без сборки:
    python3 -m remount_video --topic "дожд" --audio-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import select

from app.db import session_scope
from app.models import Project
from app.services.remount_video import find_project_by_topic_fragment, remount_video


async def _run(args: argparse.Namespace) -> int:
    async with session_scope() as session:
        project: Project | None = None
        if args.project_id is not None:
            project = (
                await session.execute(
                    select(Project).where(Project.id == args.project_id)
                )
            ).scalar_one_or_none()
        elif args.topic:
            project = await find_project_by_topic_fragment(session, args.topic)
        else:
            print("укажите project_id или --topic", file=sys.stderr)
            return 2

        if project is None:
            print("проект не найден", file=sys.stderr)
            return 1

        result = await remount_video(
            session,
            project,
            run_assemble=not args.audio_only,
        )
        await session.commit()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("done") or result.get("next") else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Перемонтаж видео (озвучка + сборка)")
    p.add_argument("project_id", nargs="?", type=int)
    p.add_argument("--topic", help="фрагмент названия, напр. «дожд»")
    p.add_argument(
        "--audio-only",
        action="store_true",
        help="только пересинхронизировать озвучку, без assemble",
    )
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
