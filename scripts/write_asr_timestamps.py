"""ASR → таймкоды в project.xlsx, строка 15 лист «план».

Usage:
  python scripts/write_asr_timestamps.py 26
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db import session_scope
from app.models import Frame, Project
from app.services.artifact_recovery import find_voice_full_on_disk
from app.services.frame_audio import align_existing_voice_full
from app.services.plan_timestamps import (
    count_parsed_timestamp_cells,
    write_asr_timestamps_to_r15,
)
from app.settings import settings
from app.storage.plan_sheet_v8 import read_plan_timestamps_cells, read_plan_voiceover_cells


async def main() -> None:
    parser = argparse.ArgumentParser(description="Записать таймкоды ASR в строку 15 xlsx")
    parser.add_argument("project_id", type=int)
    parser.add_argument(
        "--force",
        action="store_true",
        help="перезаписать R15 даже если уже заполнена",
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
        if not frames:
            print("нет кадров в БД")
            return

        audio_dir = project.data_dir / "audio"
        voice_full = find_voice_full_on_disk(
            project.data_dir,
            meta=project.meta if isinstance(project.meta, dict) else None,
        )
        if voice_full is None:
            print(f"нет voice_full в {audio_dir}")
            return

        xlsx = project.data_dir / "project.xlsx"
        if not xlsx.is_file():
            print(f"нет {xlsx}")
            return

        frame_numbers = [f.number for f in frames]
        existing, row = read_plan_timestamps_cells(project, frame_numbers)
        _filled, parsed, _bad = count_parsed_timestamp_cells(existing)
        if not args.force and parsed >= max(1, len(frame_numbers) // 2):
            print(
                f"STOP: R{row} уже заполнена ({parsed}/{len(frame_numbers)}). "
                "Нужна перезапись → --force"
            )
            return

        cells = read_plan_voiceover_cells(project, frame_numbers)
        clips, _path, words = await align_existing_voice_full(
            project,
            frames,
            cells,
            voice_full,
            audio_dir,
            whisper_model=settings.whisper_model,
        )
        if not words:
            print("ASR не вернул слова")
            return

        written = write_asr_timestamps_to_r15(project, clips)
        print(f"[#{project.id}] R{row}: записано {written}/{len(clips)} ячеек")


if __name__ == "__main__":
    asyncio.run(main())
