"""Починить fleet-проект на hub: стоп цикла, recover с диска, montage/assemble.

Usage:
  python scripts/repair_fleet_montage.py 15
  python scripts/repair_fleet_montage.py 15 --assemble
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db import session_scope
from app.fleet.montage_queue import enqueue_for_montage, process_montage_queue
from app.models import Project, ProjectStatus
from app.services.artifact_recovery import (
    ensure_fleet_montage_voice,
    find_voice_full_on_disk,
    recover_before_assemble,
)
from app.services.step_data_guard import can_enter_running


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id", type=int)
    parser.add_argument("--assemble", action="store_true", help="сразу запустить assemble")
    args = parser.parse_args()

    async with session_scope() as session:
        project = await session.get(Project, args.project_id)
        if project is None:
            print(f"project #{args.project_id} not found")
            return

        audio_dir = project.data_dir / "audio"
        voice_full = find_voice_full_on_disk(audio_dir)
        print(f"#{project.id} {project.slug} status={project.status.value}")
        print(f"  data: {project.data_dir}")
        if voice_full is not None:
            print(f"  voice_full: {voice_full.name} ({voice_full.stat().st_size // 1024} KB)")
        else:
            print("  voice_full: (нет mp3/wav в audio/)")

        clips = list((project.data_dir / "videos").glob("clip_*.mp4")) if (project.data_dir / "videos").is_dir() else []
        from app.services.shot2_montage import find_scene_clips, shot2_frame_numbers

        unique_frames = len({p.name.split("_")[1] for p in clips if p.name.startswith("clip_")})
        s2_cols = shot2_frame_numbers(project)
        s2_with_video = sum(
            1 for n in s2_cols if find_scene_clips(project.data_dir / "videos", n)[1]
        )
        print(f"  clip_*.mp4 files: {len(clips)} (frames ~{unique_frames})")
        if s2_cols:
            print(f"  shot_02 in xlsx: {len(s2_cols)}, with 2nd clip on disk: {s2_with_video}")
        xlsx = project.data_dir / "project.xlsx"
        if xlsx.is_file():
            from app.services.xlsx_v8_import import read_v8_active_frame_count

            n_xlsx = read_v8_active_frame_count(xlsx)
            print(f"  xlsx voiceover columns: {n_xlsx}")
            if len(clips) > n_xlsx:
                print(
                    f"  note: файлов clip_* больше колонок ({len(clips)}>{n_xlsx}) — "
                    "часть дубли retry или shot_02"
                )

        meta = dict(project.meta or {})
        meta.pop("montage_queue_enqueued", None)
        meta.pop("montage_blocked", None)
        meta.pop("assemble_blocked", None)
        meta.pop("step_failure", None)
        project.meta = meta
        project.status = ProjectStatus.music_ready
        await session.flush()

        await recover_before_assemble(session, project)
        if voice_full is None:
            made = await ensure_fleet_montage_voice(session, project)
            if made:
                voice_full = find_voice_full_on_disk(audio_dir)
                print(f"  local TTS → {voice_full.name if voice_full else '?'}")
        ok, reason, _rollback = await can_enter_running(
            session, project, ProjectStatus.assembling
        )
        print(f"  can_assemble: {ok} ({reason or 'ok'})")

        if not ok:
            if voice_full is None and not clips:
                print("\nНА HUB ПУСТО: нет voice_full и clip_*.mp4.")
                print("Запусти RE-PULL-AND-ASSEMBLE.cmd (NucBox #17 -> hub #15).")
            elif voice_full is None:
                print("\nНЕТ voice_full (mp3/wav) в audio/ — положи файл или повтори pull.")
            elif not clips:
                print("\nНЕТ clip_*.mp4 на hub — повтори pull с worker.")
            project.status = ProjectStatus.paused
            meta["montage_blocked"] = reason or "cannot assemble"
            project.meta = meta
            await session.flush()
            print(f"\n→ paused (цикл audio/assemble остановлен)")
            return

        if args.assemble:
            from app.services.asr.engine import require_nvidia_cuda

            try:
                require_nvidia_cuda()
                import torch

                print(f"  ASR: nvidia CUDA ({torch.cuda.get_device_name(0)})")
            except Exception as exc:  # noqa: BLE001
                print(f"\nFAIL ASR: {exc}")
                return

            import subprocess
            import sys

            script = Path(__file__).resolve().parent / "assemble_r15_direct.py"
            print(f"\n→ прямой монтаж Excel R15: {script.name}")
            proc = subprocess.run(
                [sys.executable, str(script), str(args.project_id)],
                cwd=Path(__file__).resolve().parents[1],
            )
            if proc.returncode != 0:
                raise SystemExit(proc.returncode)
            await session.refresh(project)
            print(f"\n→ готово, status={project.status.value}")
            return

        await enqueue_for_montage(session, project, source_node=meta.get("fleet_source_node"))
        n = await process_montage_queue(session)
        await session.refresh(project)
        print(f"\n→ music_ready, montage queue started={n}, status={project.status.value}")


if __name__ == "__main__":
    asyncio.run(main())
