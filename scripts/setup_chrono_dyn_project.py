"""Клон VO из #59 + группа scene_design_fanout_chrono_dyn на канвасе.

Запуск: python scripts/setup_chrono_dyn_project.py
"""
from __future__ import annotations

import asyncio
import copy
import json
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Frame, FrameStatus, Project, ProjectStatus
from app.services.node_groups import NODE_GROUPS, insert_node_group
from app.services.sidebar_layout import ensure_project_layout
from app.web.routers.projects import _slugify


SOURCE_ID = 59
TITLE = "Спесивцевы chrono_dyn"


async def main() -> None:
    async with SessionLocal() as session:
        src = await session.get(Project, SOURCE_ID)
        if src is None:
            raise SystemExit(f"source #{SOURCE_ID} not found")

        base = _slugify(TITLE)
        slug = base
        n = 2
        while (
            await session.execute(select(Project).where(Project.slug == slug))
        ).scalar_one_or_none() is not None:
            slug = f"{base}-{n}"
            n += 1

        meta = {
            "scene_design_enabled": True,
            "scene_design_variant": "chrono_dyn",
            "cloned_from_project_id": SOURCE_ID,
            "project_child_manual": False,
        }
        # настройки генерации
        for key in (
            "aspect_ratio",
            "image_generator",
            "image_resolution",
            "video_generator",
            "video_resolution",
            "img_streams",
            "outsee_streams",
        ):
            if key in (src.meta or {}):
                meta[key] = copy.deepcopy(src.meta[key])

        p = Project(
            slug=slug,
            title=TITLE,
            topic=(src.topic or "")[:500],
            status=ProjectStatus.frames_ready,
            hero_mode=src.hero_mode,
            auto_mode=False,
            script_text=src.script_text,
            general_plan=src.general_plan,
            meta=meta,
            aspect_ratio=getattr(src, "aspect_ratio", None),
            image_generator=getattr(src, "image_generator", None),
            image_resolution=getattr(src, "image_resolution", None),
            video_generator=getattr(src, "video_generator", None),
            video_resolution=getattr(src, "video_resolution", None),
        )
        session.add(p)
        await session.flush()
        ensure_project_layout(p.id)

        # VO-родители из #59 (без пустых SET-детей)
        src_frames = (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == SOURCE_ID)
                .order_by(Frame.number)
            )
        ).scalars().all()
        vo_frames = [
            f
            for f in src_frames
            if (f.voiceover_text or "").strip()
        ]
        for i, fr in enumerate(vo_frames, start=1):
            attrs = {}
            # не тянем camera_subdivide / scene attrs старого прогона
            nf = Frame(
                project_id=p.id,
                number=i,
                uuid=uuid.uuid4().hex,
                voiceover_text=fr.voiceover_text,
                duration_seconds=fr.duration_seconds,
                start_ts=fr.start_ts,
                end_ts=fr.end_ts,
                status=FrameStatus.planned,
                attrs=attrs,
                sort_key=float(i),
            )
            session.add(nf)

        # xlsx / characters refs
        src_dir = src.data_dir
        dst_dir = p.data_dir
        dst_dir.mkdir(parents=True, exist_ok=True)
        xlsx = src_dir / "project.xlsx"
        if xlsx.is_file():
            shutil.copy2(xlsx, dst_dir / "project.xlsx")
        for sub in ("characters", "items", "hero"):
            s = src_dir / sub
            if s.is_dir():
                d = dst_dir / sub
                if d.exists():
                    shutil.rmtree(d)
                shutil.copytree(s, d)

        await session.commit()
        project_id = p.id
        slug = p.slug
        title = p.title
        status_val = p.status.value
        vo_count = len(vo_frames)

    # Отдельные сессии — не держим write-lock во время ensure_run / insert_group
    # (бэкенд часто держит SQLite busy).
    from app.services.run_sync import ensure_run_for_project, sync_run_for_project
    from app.web.routers.projects import _get_default_workflow_id

    wf = await _get_default_workflow_id()
    if wf is not None:
        for attempt in range(8):
            try:
                await ensure_run_for_project(project_id, wf)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 7 or "locked" not in str(exc).lower():
                    raise
                await asyncio.sleep(2 * (attempt + 1))

    group = NODE_GROUPS["scene_design_fanout_chrono_dyn"]
    for attempt in range(8):
        try:
            async with SessionLocal() as session:
                p = await session.get(Project, project_id)
                if p is None:
                    raise SystemExit(f"project #{project_id} vanished")
                await insert_node_group(
                    session, p, "scene_design_fanout_chrono_dyn", after=None
                )
                await session.commit()
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == 7 or "locked" not in str(exc).lower():
                raise
            await asyncio.sleep(2 * (attempt + 1))

    await sync_run_for_project(project_id)

    async with SessionLocal() as session:
        p = await session.get(Project, project_id)
        from app.services.scene_design import agents as ag

        for name in ("action", "camera", "assemble"):
            text = ag.load_prompt(name, p)
            assert "CHRONO_DYN" in text or "chrono_dyn" in text.lower(), name

    print(
        json.dumps(
            {
                "id": project_id,
                "slug": slug,
                "title": title,
                "status": status_val,
                "vo_frames": vo_count,
                "variant": "chrono_dyn",
                "group": group.group_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
