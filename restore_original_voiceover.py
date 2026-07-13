"""Восстановление исходного закадрового текста у родительских проектов.

Глубокий поиск: old/, .trash/, tmp_gpt/, xlsx-бэкапы, кадры в БД, дочерние.

Запуск (все родители):
    python3 -m restore_original_voiceover --all-parents --dry-run
    python3 -m restore_original_voiceover --all-parents

Один родитель — посмотреть все найденные источники:
    python3 -m restore_original_voiceover 12 --scan

Windows (корзина + БД):
    powershell -ExecutionPolicy Bypass -File scripts\\Recover-VoiceoverFromRecycleBin.ps1 -ThenRestoreDb
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import select

from app.db import session_scope
from app.models import Project
from app.services.mass_factory import mass_parent_id
from app.services.voiceover_recovery import (
    discover_original_candidates,
    find_original_voiceover,
    restore_all_parent_voiceovers,
    restore_original_voiceover,
)


async def _run(args: argparse.Namespace) -> int:
    async with session_scope() as session:
        if args.all_parents:
            summary = await restore_all_parent_voiceovers(
                session,
                dry_run=args.dry_run,
                force=args.force,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        project_id = args.project_id
        if project_id is None:
            print("укажите project_id или --all-parents", file=sys.stderr)
            return 2

        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is None:
            print(f"проект #{project_id} не найден", file=sys.stderr)
            return 1

        if mass_parent_id(project) is not None:
            print(
                json.dumps(
                    {
                        "project_id": project.id,
                        "restored": False,
                        "reason": "child_project_skipped",
                        "mass_parent_id": mass_parent_id(project),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1

        if args.scan:
            candidates = await discover_original_candidates(session, project)
            info = {
                "project_id": project.id,
                "slug": project.slug,
                "candidates": [
                    {
                        "source": c.source,
                        "priority": c.priority,
                        "chars": len(c.text),
                        "preview": c.text[:300],
                    }
                    for c in candidates[:20]
                ],
            }
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0

        if args.inspect:
            cand = await find_original_voiceover(session, project)
            info = {
                "project_id": project.id,
                "slug": project.slug,
                "original": (
                    {
                        "source": cand.source,
                        "chars": len(cand.text),
                        "preview": cand.text[:300],
                    }
                    if cand
                    else None
                ),
            }
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0

        result = await restore_original_voiceover(
            session,
            project,
            dry_run=args.dry_run,
            force=args.force,
        )
        if result.get("restored") and not args.dry_run:
            await session.commit()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Восстановить voiceover (только родители)")
    p.add_argument("project_id", nargs="?", type=int, help="ID родительского проекта")
    p.add_argument("--all-parents", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--inspect", action="store_true", help="лучший кандидат")
    p.add_argument("--scan", action="store_true", help="все найденные источники")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
