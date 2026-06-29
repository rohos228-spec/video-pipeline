"""Проверить финальный mp4 и proof-файлы варианта 1.

Usage:
  python scripts/verify_final_engine.py 15
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from app.db import session_scope
from app.models import Project
from app.services.media_probe import probe_duration


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("project_id", type=int, default=15, nargs="?")
    args = parser.parse_args()

    async with session_scope() as session:
        project = await session.get(Project, args.project_id)
        if project is None:
            raise SystemExit(f"project #{args.project_id} not found")

        final_dir = project.data_dir / "final"
        mp4 = final_dir / f"{project.slug}.mp4"
        plan = final_dir / "variant2_plan.txt"
        r15 = final_dir / "r15_read.txt"
        stamp = final_dir / "MONTAGE_STAMP.txt"

        print(f"#{project.id} {project.slug}")
        print(f"  mp4: {mp4} exists={mp4.is_file()}")
        if mp4.is_file():
            dur = await probe_duration(mp4)
            st = mp4.stat()
            print(f"  duration={dur:.2f}s size={st.st_size // (1024 * 1024)} MB")
            try:
                meta = subprocess.check_output(
                    [
                        "ffprobe", "-v", "error",
                        "-show_entries", "format_tags=comment",
                        "-of", "default=nw=1",
                        str(mp4),
                    ],
                    text=True,
                ).strip()
                print(f"  comment={meta or '(empty)'}")
            except Exception as exc:  # noqa: BLE001
                print(f"  ffprobe failed: {exc}")

        for label, path in (("r15", r15), ("plan", plan), ("stamp", stamp)):
            print(f"  {label}: {path.name} exists={path.is_file()}")
            if path.is_file() and path.suffix == ".txt":
                lines = path.read_text(encoding="utf-8").splitlines()
                for line in lines[:8]:
                    print(f"    {line}")


if __name__ == "__main__":
    asyncio.run(main())
