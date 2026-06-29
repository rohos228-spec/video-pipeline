"""ASR → таймкоды в project.xlsx, строка 15 лист «план».

Usage:
  python scripts/write_asr_timestamps.py 15
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db import session_scope
from app.models import Frame, Project
from app.services.artifact_recovery import find_voice_full_on_disk
from app.services.plan_timestamps import compute_frame_timestamp_ranges
from app.storage.plan_sheet_v8 import write_plan_timestamps


async def main() -> None:
    parser = argparse.ArgumentParser(description="Записать таймкоды ASR в строку 15 xlsx")
    parser.add_argument("project_id", type=int)
    parser.add_argument(
        "--force",
        action="store_true",
        help="перезаписать R15 даже если уже заполнена вручную",
    )
    args = parser.parse_args()

    async with session_scope() as session:
        project = await session.get(Project, args.project_id)
        if project is None:
            print(f"project #{args.project_id} not found")
            return

        frames = (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.number)
            )
        ).scalars().all()
        frame_numbers = [f.number for f in frames]
        if not frame_numbers:
            print("нет кадров в БД")
            return

        audio_dir = project.data_dir / "audio"
        voice_full = find_voice_full_on_disk(audio_dir)
        if voice_full is None:
            print(f"нет voice_full в {audio_dir}")
            return

        xlsx = project.data_dir / "project.xlsx"
        if not xlsx.is_file():
            print(f"нет {xlsx}")
            return

        from app.services.plan_timestamps import count_parsed_timestamp_cells
        from app.storage.plan_sheet_v8 import read_plan_timestamps_cells

        existing, row = read_plan_timestamps_cells(project, frame_numbers)
        _filled, parsed, _bad = count_parsed_timestamp_cells(existing)
        if not args.force and parsed >= max(1, len(frame_numbers) // 2):
            print(
                f"STOP: R{row} уже заполнена ({parsed}/{len(frame_numbers)}). "
                "Ручные метки не трогаем. Нужна перезапись ASR → --force"
            )
            return

        rows = await compute_frame_timestamp_ranges(
            project,
            frame_numbers,
            voice_full_path=voice_full,
        )
        if not rows:
            print("не удалось построить таймкоды")
            return

        neg = [r for r in rows if r[3] <= r[2]]
        if neg:
            print(f"WARN: {len(neg)} кадров с некорректным интервалом")

        written = write_plan_timestamps(
            project,
            [(num, label) for num, label, _s, _e in rows],
        )
        durs = [e - s for _n, _l, s, e in rows]
        print(f"[#{project.id}] {project.slug}: R15 записано {written}/{len(rows)} ячеек")
        print(f"  длительность кадра: min {min(durs):.2f}s  avg {sum(durs)/len(durs):.2f}s  max {max(durs):.2f}s")
        for num, label, start, end in rows[:3]:
            print(f"  frame {num}: {label}  ({start:.2f}s–{end:.2f}s)")
        if len(rows) > 4:
            num, label, start, end = rows[-1]
            print(f"  … frame {num}: {label}  ({start:.2f}s–{end:.2f}s)")


if __name__ == "__main__":
    asyncio.run(main())
