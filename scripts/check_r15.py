"""Проверка: читаются ли таймкоды R15 из project.xlsx.

Usage:
  python scripts/check_r15.py 15
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db import session_scope
from app.models import Frame, Project
from app.services.plan_timestamps import count_parsed_timestamp_cells, parse_timecode_range
from app.storage.plan_sheet_v8 import read_plan_timestamps_cells


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id", type=int)
    args = parser.parse_args()

    async with session_scope() as session:
        project = await session.get(Project, args.project_id)
        if project is None:
            print(f"project #{args.project_id} not found")
            return

        frames = (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
            )
        ).scalars().all()
        frame_numbers = [f.number for f in frames]
        xlsx = project.data_dir / "project.xlsx"
        print(f"#{project.id} {project.slug}")
        print(f"  xlsx: {xlsx} exists={xlsx.is_file()}")
        if not xlsx.is_file():
            return

        cells, row = read_plan_timestamps_cells(project, frame_numbers)
        filled, parsed, bad = count_parsed_timestamp_cells(cells)
        print(f"  row={row} filled={filled}/{len(frame_numbers)} parsed={parsed} bad={len(bad)}")
        if cells[:3]:
            for num, lbl in cells[:3]:
                p = parse_timecode_range(lbl)
                print(f"  frame {num}: {lbl!r} -> {p}")
        for sample_num in (4, 10):
            if sample_num <= len(cells):
                num, lbl = cells[sample_num - 1]
                p = parse_timecode_range(lbl)
                print(f"  frame {num}: {lbl!r} -> {p}")
        if cells:
            num, lbl = cells[-1]
            p = parse_timecode_range(lbl)
            print(f"  frame {num}: {lbl!r} -> {p}")
        if bad[:5]:
            print(f"  bad frames sample: {bad[:5]}")


if __name__ == "__main__":
    asyncio.run(main())
