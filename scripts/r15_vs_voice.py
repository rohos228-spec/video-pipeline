"""Сверка Excel R15 с реальной озвучкой (words.json + ASR).

Usage:
  python scripts/r15_vs_voice.py 15
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db import session_scope
from app.models import Frame, Project
from app.services.artifact_recovery import find_voice_full_on_disk
from app.services.media_probe import probe_duration
from app.services.plan_timestamps import (
    clips_from_timestamp_cells,
    find_words_json,
    parse_timecode_range,
    r15_voice_diff_lines,
)
from app.services.whisper import load_words_json
from app.storage.plan_sheet_v8 import read_plan_timestamps_cells, read_plan_voiceover_cells


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id", type=int)
    args = parser.parse_args()

    async with session_scope() as session:
        project = await session.get(Project, args.project_id)
        if project is None:
            raise SystemExit(f"project #{args.project_id} not found")

        frames = (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
            )
        ).scalars().all()
        frame_numbers = [f.number for f in frames]
        xlsx = project.data_dir / "project.xlsx"
        audio_dir = project.data_dir / "audio"
        voice = find_voice_full_on_disk(audio_dir)
        words_path = find_words_json(audio_dir)

        print(f"#{project.id} {project.slug}")
        print(f"  xlsx: {xlsx} exists={xlsx.is_file()}")
        print(f"  voice: {voice}")
        print(f"  words: {words_path}")

        if not xlsx.is_file() or voice is None:
            return

        ts_cells, row = read_plan_timestamps_cells(project, frame_numbers)
        cells = read_plan_voiceover_cells(project, frame_numbers)
        master = await probe_duration(voice)
        clips = clips_from_timestamp_cells(cells, ts_cells, voice, master=master)
        if not clips:
            print("  R15: не читается")
            return

        print(f"  R{row} frames={len(clips)} voice={master:.2f}s")
        for num in (1, 2, 3, 4, 10):
            if num > len(ts_cells):
                continue
            lbl = dict(ts_cells).get(num, "")
            parsed = parse_timecode_range(lbl)
            clip = next((c for c in clips if c.frame_number == num), None)
            print(f"  frame {num}: {lbl!r} parsed={parsed} clip={clip.start_ts if clip else None}-{clip.end_ts if clip else None}s")

        if words_path is None or not words_path.is_file():
            print("\n  Нет words.json — сначала remontage или assemble (ASR для субтитров).")
            return

        words = load_words_json(words_path)
        lines = r15_voice_diff_lines(
            clips=clips,
            ts_cells=ts_cells,
            cells=cells,
            words=words,
            master=master,
        )
        if not lines:
            print("\n  OK: R15 start совпадает с ASR (±1.0s) для всех кадров.")
            return

        print(f"\n  MISMATCH ({len(lines)} кадров):")
        print("\n".join(lines[:20]))
        if len(lines) > 20:
            print(f"  ... ещё {len(lines) - 20}")


if __name__ == "__main__":
    asyncio.run(main())
