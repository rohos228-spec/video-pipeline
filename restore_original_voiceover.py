"""Восстановление исходного закадрового текста у родительских проектов.

Ищет самый ранний бэкап в data/videos/<slug>/old/*_voiceover.txt,
копии у дочерних проектов, затем записывает в voiceover.txt + script_text.

Запуск (все родители):
    python3 -m restore_original_voiceover --all-parents

Один проект:
    python3 -m restore_original_voiceover 12

Только посмотреть, без записи:
    python3 -m restore_original_voiceover --all-parents --dry-run
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
                        "hint": "восстановление только для родительских проектов",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1

        if args.inspect:
            cand = await find_original_voiceover(session, project)
            info = {
                "project_id": project.id,
                "slug": project.slug,
                "is_parent": mass_parent_id(project) is None,
                "original": (
                    {
                        "source": cand.source,
                        "chars": len(cand.text),
                        "preview": cand.text[:200],
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
    p = argparse.ArgumentParser(description="Восстановить исходный voiceover")
    p.add_argument("project_id", nargs="?", type=int, help="ID проекта")
    p.add_argument(
        "--all-parents",
        action="store_true",
        help="все родительские проекты (mass_parent_id is null)",
    )
    p.add_argument("--dry-run", action="store_true", help="только показать план")
    p.add_argument("--force", action="store_true", help="перезаписать даже если совпадает")
    p.add_argument("--inspect", action="store_true", help="показать найденный исходник")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
