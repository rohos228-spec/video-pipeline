"""Восстановить картинки кадров из old/scenes и пересинхронизировать БД.

Запуск:
    python -m scripts.recover_scene_images 13
    python -m scripts.recover_scene_images 13 --scan-only
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.db import session_scope
from app.models import Project
from app.services.artifact_recovery import recover_scene_images_full
from app.services.scan_frames import scan_missing_frames


async def main(project_id: int, *, scan_only: bool) -> int:
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            print(f"проект #{project_id} не найден")
            return 1

        scenes = project.data_dir / "scenes"
        on_disk = len(list(scenes.glob("frame_*.png"))) if scenes.is_dir() else 0
        missing_before = await scan_missing_frames(session, project)
        print(
            f"#{project_id} «{project.topic}» slug={project.slug} "
            f"status={project.status.value}"
        )
        print(f"  scenes/: {on_disk} png, без файла (до): {len(missing_before)}")

        if scan_only:
            old_root = project.data_dir / "old" / "scenes"
            batches = (
                len([d for d in old_root.iterdir() if d.is_dir()])
                if old_root.is_dir()
                else 0
            )
            print(f"  old/scenes бэкапов: {batches}")
            if not batches:
                print(
                    "  бэкапов нет — попробуйте «Предыдущие версии» на папке scenes "
                    "(ПКМ - Свойства - Предыдущие версии) или перегенерацию "
                    "«Доделка картинок»."
                )
            return 0

        stats = await recover_scene_images_full(session, project)
        missing_after = await scan_missing_frames(session, project)
        await session.commit()

        print(f"  restored from old/scenes: {stats.get('restored', 0)}")
        print(f"  artifacts registered: {stats.get('artifacts_registered', 0)}")
        print(f"  frames synced: {stats.get('frames_synced', 0)}")
        print(f"  без файла (после): {len(missing_after)}")
        if missing_after:
            head = ", ".join(str(n) for n in missing_after[:25])
            if len(missing_after) > 25:
                head += f", … +{len(missing_after) - 25}"
            print(f"  недостающие кадры: {head}")
            print("  в Studio: Доделка - Доделка картинок (промты в БД сохранены)")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Восстановить scene png проекта")
    parser.add_argument("project_id", type=int, nargs="?", default=13)
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="только диагностика, без копирования",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.project_id, scan_only=args.scan_only)))
