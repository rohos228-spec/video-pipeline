"""Монтаж — вариант 2 overlay (Excel R15).

Usage:
  .\\.venv\\Scripts\\python.exe scripts\\assemble_r15_direct.py 17
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db import session_scope
from app.models import Artifact, ArtifactKind, Frame, Project, ProjectStatus
from app.services.artifact_recovery import find_voice_full_on_disk, recover_before_assemble
from app.services.bgm import resolve_bgm
from app.services.media_probe import probe_duration
from app.services.montage import MONTAGE_ENGINE_V2, MONTAGE_VARIANTS, run_variant2
from app.services.montage.r15 import resolve_montage_frame_numbers
from app.services.montage_asr import ensure_montage_words
from app.services.plan_timestamps import ensure_r15_from_asr
from app.storage.plan_sheet_v8 import resolve_plan_voiceover_cells


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Montage variant 2 — overlay on black")
    parser.add_argument("project_id", type=int, nargs="?", default=15)
    args = parser.parse_args()

    print(MONTAGE_VARIANTS)
    print()
    print(f">>> VARIANT 2 ({MONTAGE_ENGINE_V2}) project #{args.project_id}")
    print("Параллельно с Outsee/генерацией — Studio не останавливать.")

    from app.services.montage_coexist import montage_lane_claim, wait_for_montage_slot

    await wait_for_montage_slot(args.project_id)

    with montage_lane_claim(args.project_id):
        await _run_montage(args.project_id)


async def _run_montage(project_id: int) -> None:
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise SystemExit(f"project #{project_id} not found")

        meta = dict(project.meta or {})
        meta.pop("assemble_blocked", None)
        meta.pop("montage_blocked", None)
        meta.pop("step_failure", None)
        project.meta = meta
        project.status = ProjectStatus.assembling
        await session.flush()

        await recover_before_assemble(session, project)

        frames = (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
            )
        ).scalars().all()
        if not frames:
            raise SystemExit("нет кадров в БД")

        voice = find_voice_full_on_disk(
            project.data_dir,
            meta=project.meta if isinstance(project.meta, dict) else None,
        )
        if voice is None:
            raise SystemExit("нет voice_full в audio/")

        db_nums = [f.number for f in frames]
        frame_numbers = resolve_montage_frame_numbers(project, db_nums)
        cells, _src = await resolve_plan_voiceover_cells(session, project, frame_numbers)
        if not any(t.strip() for _, t in cells):
            raise SystemExit("нет текста кадров (R49 / БД) — монтаж невозможен")

        words = await ensure_montage_words(
            session,
            project,
            audio_path=voice,
            audio_dir=project.data_dir / "audio",
            frame_numbers=frame_numbers,
        )
        master = await probe_duration(voice)
        await ensure_r15_from_asr(
            project,
            frame_numbers=frame_numbers,
            cells=cells,
            words=words,
            voice_full_path=voice,
            master=master,
        )

        out_path = project.data_dir / "final" / f"{project.slug}.mp4"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        await run_variant2(
            project,
            frame_numbers,
            voice,
            out_path,
            bgm=resolve_bgm(project),
        )

        session.add(
            Artifact(
                project_id=project.id,
                kind=ArtifactKind.final_video,
                uuid=uuid.uuid4().hex,
                path=str(out_path.resolve()),
            )
        )
        project.status = ProjectStatus.assembled
        await session.commit()

        print(f"DONE -> {out_path}")
        print(f"proof -> {project.data_dir / 'final' / 'variant2_plan.txt'}")


if __name__ == "__main__":
    asyncio.run(main())
