"""DB v2: гибкая модель «карточки + дробный порядок + связи».

Идеи:
- у каждого кадра стабильный ``uuid`` (адрес не «строка 48», а карточка);
- порядок — дробный ``sort_key``: вставка между кадрами = среднее соседей,
  без перенумерации остальных;
- тексты/промты — отдельные записи (сколько угодно, с версиями);
- связи — таблица ``frame_edges`` («ниточки»), а не текст в ячейке.

Excel остаётся внешним видом: экспорт из этих таблиц, импорт по uuid.

Миграция: ``migrate_db_v2_schema(conn)`` (ALTER frames) вызывается и из
``app.main._init_db``, и из lifespan FastAPI — create_all новые таблицы
создаст сам, колонки в существующую frames добавляем здесь.
"""

from __future__ import annotations

import uuid as _uuid_mod
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Artifact,
    Entity,
    Frame,
    FrameEdge,
    FrameStatus,
    FrameText,
    Project,
    PromptVersion,
    Scene,
)

_SORT_STEP = 10.0

_FRAME_V2_COLS: list[tuple[str, str]] = [
    ("uuid", "VARCHAR(64)"),
    ("sort_key", "FLOAT"),
    ("scene_id", "INTEGER"),
]


async def migrate_db_v2_schema(conn: Any) -> None:
    """Добавить v2-колонки в frames (create_all не умеет ALTER)."""
    rows = (await conn.exec_driver_sql("PRAGMA table_info(frames)")).fetchall()
    existing = {r[1] for r in rows}
    for col, ctype in _FRAME_V2_COLS:
        if col in existing:
            continue
        try:
            await conn.exec_driver_sql(f"ALTER TABLE frames ADD COLUMN {col} {ctype}")
            logger.info("db_v2 migrate: frames.{} added", col)
        except Exception as e:  # noqa: BLE001
            logger.warning("db_v2 migrate: add frames.{} failed: {}", col, e)


def new_frame_uuid() -> str:
    return _uuid_mod.uuid4().hex[:24]


async def replace_all_frames(
    session: AsyncSession,
    project: Project,
    specs: list[dict[str, Any]],
) -> list[Frame]:
    """Полная замена кадров проекта (шаг split, DB SoT).

    ``specs`` — список ``{"voiceover_text"|"закадр": str,
    "duration_seconds"|"длительность"?: float, "uuid"?: str, "meaning"|"смысл"?: str}``.
    Артефакты отвязываются (frame_id=NULL), не удаляются с диска.
    Если в spec есть ``uuid`` — сохраняем его (restore / import), иначе генерируем.
    """
    from sqlalchemy import delete, update

    if not specs or len(specs) < 2:
        raise ValueError(f"replace_all_frames: нужно ≥2 кадра, получили {len(specs)}")

    # Не трогаем файлы на диске — только отвязка от старых Frame.id.
    await session.execute(
        update(Artifact)
        .where(Artifact.project_id == project.id)
        .values(frame_id=None)
    )
    await session.execute(
        delete(FrameEdge).where(FrameEdge.project_id == project.id)
    )
    old_ids = list(
        (
            await session.execute(select(Frame.id).where(Frame.project_id == project.id))
        ).scalars()
    )
    if old_ids:
        await session.execute(
            delete(PromptVersion).where(PromptVersion.frame_id.in_(old_ids))
        )
        await session.execute(
            delete(FrameText).where(FrameText.frame_id.in_(old_ids))
        )
        await session.execute(delete(Frame).where(Frame.project_id == project.id))
    await session.flush()

    # Одна сцена-контейнер
    scene = (
        await session.execute(
            select(Scene).where(Scene.project_id == project.id).order_by(Scene.sort_key)
        )
    ).scalars().first()
    if scene is None:
        scene = Scene(project_id=project.id, sort_key=_SORT_STEP, title="main")
        session.add(scene)
        await session.flush()

    created: list[Frame] = []
    seen_uuids: set[str] = set()
    for i, raw in enumerate(specs, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"replace_all_frames: кадр {i} не объект")
        vo = str(
            raw.get("voiceover_text")
            or raw.get("закадр")
            or raw.get("voiceover")
            or ""
        ).strip()
        if not vo:
            raise ValueError(f"replace_all_frames: кадр {i} без закадра")
        dur_raw = raw.get("duration_seconds", raw.get("длительность"))
        dur: float | None
        try:
            dur = float(dur_raw) if dur_raw is not None and dur_raw != "" else None
        except (TypeError, ValueError) as e:
            raise ValueError(f"replace_all_frames: кадр {i} длительность не число") from e
        meaning_raw = raw.get("meaning", raw.get("смысл"))
        meaning = (
            str(meaning_raw).strip()
            if meaning_raw is not None and str(meaning_raw).strip()
            else None
        )
        raw_uuid = str(raw.get("uuid") or raw.get("frame_uuid") or "").strip()
        if raw_uuid:
            if raw_uuid in seen_uuids:
                raise ValueError(
                    f"replace_all_frames: дубликат uuid {raw_uuid!r} на кадре {i}"
                )
            seen_uuids.add(raw_uuid)
            frame_uuid = raw_uuid
        else:
            frame_uuid = new_frame_uuid()
            while frame_uuid in seen_uuids:
                frame_uuid = new_frame_uuid()
            seen_uuids.add(frame_uuid)
        fr = Frame(
            project_id=project.id,
            number=i,
            voiceover_text=vo,
            meaning=meaning,
            duration_seconds=dur,
            uuid=frame_uuid,
            sort_key=float(i) * _SORT_STEP,
            scene_id=scene.id,
            status=FrameStatus.planned,
            attrs={},
        )
        session.add(fr)
        created.append(fr)
    await session.flush()
    for a, b in zip(created, created[1:], strict=False):
        session.add(
            FrameEdge(
                project_id=project.id,
                from_frame_id=a.id,
                to_frame_id=b.id,
                type="next",
            )
        )
    await session.flush()
    await backfill_project_v2(session, project)
    logger.info(
        "[#{}] replace_all_frames: {} кадров (DB SoT)",
        project.id,
        len(created),
    )
    return created


def sort_key_between(before: float | None, after: float | None) -> float:
    """Дробный ключ между соседями; края — с шагом _SORT_STEP."""
    if before is None and after is None:
        return _SORT_STEP
    if before is None:
        return float(after) - _SORT_STEP  # type: ignore[arg-type]
    if after is None:
        return float(before) + _SORT_STEP
    mid = (float(before) + float(after)) / 2.0
    if mid == float(before):  # ключи слиплись — редкий вырожденный случай
        return float(before) + 1e-6
    return mid


async def backfill_project_v2(session: AsyncSession, project: Project) -> dict[str, int]:
    """Заполнить v2-таблицы из существующих данных проекта (идемпотентно)."""
    stats = {"scenes": 0, "sort_keys": 0, "uuids": 0, "texts": 0, "prompts": 0, "edges": 0}

    frames = list(
        (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
            )
        ).scalars()
    )
    if not frames:
        return stats

    # 1. Сцена по умолчанию
    scene = (
        await session.execute(
            select(Scene).where(Scene.project_id == project.id).order_by(Scene.sort_key)
        )
    ).scalars().first()
    if scene is None:
        scene = Scene(project_id=project.id, sort_key=_SORT_STEP, title="Сцена 1")
        session.add(scene)
        await session.flush()
        stats["scenes"] += 1

    # 2. uuid + sort_key + scene_id у кадров
    for idx, fr in enumerate(frames, start=1):
        if not fr.uuid:
            fr.uuid = new_frame_uuid()
            stats["uuids"] += 1
        if fr.sort_key is None:
            fr.sort_key = float(idx) * _SORT_STEP
            stats["sort_keys"] += 1
        if fr.scene_id is None:
            fr.scene_id = scene.id

    # 3. Тексты: закадр из voiceover_text (если ещё не вынесен)
    for fr in frames:
        has_text = (
            await session.execute(
                select(func.count(FrameText.id)).where(
                    FrameText.frame_id == fr.id, FrameText.kind == "voiceover"
                )
            )
        ).scalar_one()
        if not has_text and (fr.voiceover_text or "").strip():
            session.add(
                FrameText(
                    project_id=project.id,
                    frame_id=fr.id,
                    kind="voiceover",
                    text=fr.voiceover_text.strip(),
                    sort_key=_SORT_STEP,
                )
            )
            stats["texts"] += 1

    # 4. Промты: image_prompt / animation_prompt → версии 1 (активные)
    for fr in frames:
        for kind, src in (("img", fr.image_prompt), ("video", fr.animation_prompt)):
            if not (src or "").strip():
                continue
            has_pv = (
                await session.execute(
                    select(func.count(PromptVersion.id)).where(
                        PromptVersion.frame_id == fr.id, PromptVersion.kind == kind
                    )
                )
            ).scalar_one()
            if not has_pv:
                session.add(
                    PromptVersion(
                        project_id=project.id,
                        frame_id=fr.id,
                        kind=kind,
                        version=1,
                        text=src.strip(),
                        is_active=True,
                    )
                )
                stats["prompts"] += 1

    # 5. Цепочка next по текущему порядку (если связей ещё нет)
    edge_count = (
        await session.execute(
            select(func.count(FrameEdge.id)).where(FrameEdge.project_id == project.id)
        )
    ).scalar_one()
    if not edge_count:
        ordered = sorted(frames, key=lambda f: (f.sort_key or 0.0, f.number))
        for a, b in zip(ordered, ordered[1:], strict=False):
            session.add(
                FrameEdge(
                    project_id=project.id,
                    from_frame_id=a.id,
                    to_frame_id=b.id,
                    type="next",
                )
            )
            stats["edges"] += 1

    await session.flush()
    return stats


async def backfill_all_projects_v2(session: AsyncSession) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    projects = list((await session.execute(select(Project))).scalars())
    for p in projects:
        out[p.id] = await backfill_project_v2(session, p)
    return out


async def insert_frame_after(
    session: AsyncSession,
    project: Project,
    *,
    after_frame_id: int | None,
    scene_id: int | None = None,
) -> Frame:
    """Вставить новую карточку кадра после заданной (или в начало).

    Порядок — среднее соседних sort_key; ``number`` = max+1 (legacy-колонка
    с unique constraint, на порядок отображения не влияет).
    """
    frames = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        ).scalars()
    )
    before_key: float | None = None
    after_key: float | None = None
    if after_frame_id is None:
        after_key = frames[0].sort_key if frames else None
    else:
        for i, fr in enumerate(frames):
            if fr.id == after_frame_id:
                before_key = fr.sort_key
                after_key = frames[i + 1].sort_key if i + 1 < len(frames) else None
                break
        else:
            raise ValueError(f"frame {after_frame_id} не найден в проекте {project.id}")

    key = sort_key_between(before_key, after_key)
    if scene_id is None and after_frame_id is not None:
        src = next((f for f in frames if f.id == after_frame_id), None)
        scene_id = src.scene_id if src else None
    if scene_id is None:
        stats = await backfill_project_v2(session, project)
        _ = stats
        scene = (
            await session.execute(
                select(Scene).where(Scene.project_id == project.id).order_by(Scene.sort_key)
            )
        ).scalars().first()
        scene_id = scene.id if scene else None

    max_number = (
        await session.execute(
            select(func.max(Frame.number)).where(Frame.project_id == project.id)
        )
    ).scalar_one() or 0

    fr = Frame(
        project_id=project.id,
        number=int(max_number) + 1,
        voiceover_text="",
        uuid=new_frame_uuid(),
        sort_key=key,
        scene_id=scene_id,
        attrs={},
    )
    session.add(fr)
    await session.flush()

    # Перекидываем ниточку next: after -> new -> old_next
    if after_frame_id is not None:
        old_next = (
            await session.execute(
                select(FrameEdge).where(
                    FrameEdge.project_id == project.id,
                    FrameEdge.from_frame_id == after_frame_id,
                    FrameEdge.type == "next",
                )
            )
        ).scalars().first()
        session.add(
            FrameEdge(
                project_id=project.id,
                from_frame_id=after_frame_id,
                to_frame_id=fr.id,
                type="next",
            )
        )
        if old_next is not None:
            old_next.from_frame_id = fr.id
    elif frames:
        session.add(
            FrameEdge(
                project_id=project.id,
                from_frame_id=fr.id,
                to_frame_id=frames[0].id,
                type="next",
            )
        )
    await session.flush()
    return fr


async def add_prompt_version(
    session: AsyncSession,
    project_id: int,
    frame_id: int,
    *,
    kind: str,
    text: str,
    set_active: bool = True,
) -> PromptVersion:
    max_ver = (
        await session.execute(
            select(func.max(PromptVersion.version)).where(
                PromptVersion.frame_id == frame_id, PromptVersion.kind == kind
            )
        )
    ).scalar_one() or 0
    if set_active:
        actives = (
            await session.execute(
                select(PromptVersion).where(
                    PromptVersion.frame_id == frame_id,
                    PromptVersion.kind == kind,
                    PromptVersion.is_active.is_(True),
                )
            )
        ).scalars().all()
        for pv in actives:
            pv.is_active = False
    pv = PromptVersion(
        project_id=project_id,
        frame_id=frame_id,
        kind=kind,
        version=int(max_ver) + 1,
        text=text,
        is_active=set_active,
    )
    session.add(pv)
    await session.flush()
    return pv


async def deactivate_prompt_versions(
    session: AsyncSession,
    project_id: int,
    *,
    kind: str,
) -> int:
    """Set is_active=False on all matching PromptVersion rows. Do not delete."""
    rows = (
        await session.execute(
            select(PromptVersion).where(
                PromptVersion.project_id == project_id,
                PromptVersion.kind == kind,
                PromptVersion.is_active.is_(True),
            )
        )
    ).scalars().all()
    for pv in rows:
        pv.is_active = False
    n = len(rows)
    if n:
        await session.flush()
    return n


async def project_graph(session: AsyncSession, project: Project) -> dict[str, Any]:
    """Полный граф проекта для визуализации: сцены → кадры → тексты/промты/связи."""
    scenes = list(
        (
            await session.execute(
                select(Scene).where(Scene.project_id == project.id).order_by(Scene.sort_key)
            )
        ).scalars()
    )
    frames = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        ).scalars()
    )
    texts = list(
        (
            await session.execute(
                select(FrameText).where(FrameText.project_id == project.id)
            )
        ).scalars()
    )
    prompts = list(
        (
            await session.execute(
                select(PromptVersion).where(PromptVersion.project_id == project.id)
            )
        ).scalars()
    )
    edges = list(
        (
            await session.execute(
                select(FrameEdge).where(FrameEdge.project_id == project.id)
            )
        ).scalars()
    )
    entities = list(
        (
            await session.execute(
                select(Entity)
                .where(Entity.project_id == project.id)
                .order_by(Entity.sort_key)
            )
        ).scalars()
    )

    texts_by_frame: dict[int, list[FrameText]] = {}
    for t in texts:
        texts_by_frame.setdefault(t.frame_id, []).append(t)
    prompts_by_frame: dict[int, list[PromptVersion]] = {}
    for p in prompts:
        prompts_by_frame.setdefault(p.frame_id, []).append(p)
    edges_by_from: dict[int, list[FrameEdge]] = {}
    for e in edges:
        edges_by_from.setdefault(e.from_frame_id, []).append(e)

    # URL превью картинок кадров (scenes/frame_NNN_*.png) — для «Хронологии»
    # в «Базе»: фильм-стрип с реальными кадрами, как на доске монтажа.
    from app.services.montage_board import _preview_url  # lazy: против циклов
    from app.services.plan_shot2 import find_shot1_image, find_shot2_image

    scenes_dir = project.data_dir / "scenes"

    def frame_dto(fr: Frame) -> dict[str, Any]:
        img = find_shot1_image(scenes_dir, fr.number) or find_shot2_image(
            scenes_dir, fr.number
        )
        return {
            "id": fr.id,
            "uuid": fr.uuid,
            "number": fr.number,
            "sort_key": fr.sort_key,
            "scene_id": fr.scene_id,
            "status": fr.status.value if fr.status else None,
            "duration_seconds": fr.duration_seconds,
            "voiceover_text": fr.voiceover_text,
            "meaning": fr.meaning,
            "image_prompt": fr.image_prompt,
            "animation_prompt": fr.animation_prompt,
            "image_url": _preview_url(img),
            "attrs": fr.attrs or {},
            "texts": [
                {
                    "id": t.id,
                    "kind": t.kind,
                    "text": t.text,
                    "sort_key": t.sort_key,
                }
                for t in sorted(texts_by_frame.get(fr.id, []), key=lambda x: x.sort_key)
            ],
            "prompts": [
                {
                    "id": p.id,
                    "kind": p.kind,
                    "version": p.version,
                    "is_active": p.is_active,
                    "text": p.text,
                }
                for p in sorted(
                    prompts_by_frame.get(fr.id, []), key=lambda x: (x.kind, x.version)
                )
            ],
            "edges": [
                {"id": e.id, "to_frame_id": e.to_frame_id, "type": e.type}
                for e in edges_by_from.get(fr.id, [])
            ],
        }

    frames_out = [frame_dto(fr) for fr in frames]
    scenes_out = [
        {
            "id": sc.id,
            "sort_key": sc.sort_key,
            "title": sc.title,
            "place": sc.place,
            "meaning": sc.meaning,
            "scene_type": sc.scene_type,
            "frame_ids": [fr.id for fr in frames if fr.scene_id == sc.id],
        }
        for sc in scenes
    ]
    return {
        "project": {
            "id": project.id,
            "slug": project.slug,
            "title": project.title,
            "topic": project.topic,
            "status": project.status.value if project.status else None,
        },
        "scenes": scenes_out,
        "frames": frames_out,
        "entities": [
            {
                "id": en.id,
                "type": en.type,
                "code": en.code,
                "name": en.name,
                "sort_key": en.sort_key,
                "attrs": en.attrs or {},
            }
            for en in entities
        ],
    }
