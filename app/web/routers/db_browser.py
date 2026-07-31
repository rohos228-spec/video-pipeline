"""REST для визуализации DB v2 (кнопка «База» в Studio).

Чтение графа + настройки: правка кадра, вставка между кадрами, тексты,
версии промтов, сущности, связи. Всё по id/uuid карточек — без адресов
вида «строка 48».
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Entity,
    Frame,
    FrameEdge,
    FrameStatus,
    FrameText,
    Project,
    PromptVersion,
    Scene,
)
from app.services import db_v2
from app.services.xlsx_v8_import import (
    ROW_DURATION_V8,
    ROW_IMAGE_PROMPT_2_V8,
    ROW_IMAGE_PROMPT_V8,
    ROW_TIMECODE_V8,
    ROW_VIDEO_PROMPT_2_V8,
    ROW_VIDEO_PROMPT_V8,
    ROW_VOICEOVER_V8,
    SHEET_GENERAL_V8,
    _cell_text,
    _normalize_sheet_name,
    _resolve_plan_sheet,
)
from app.web.deps import get_session

router = APIRouter(prefix="/db", tags=["db"])

# Лист «план» v8: строки, которые оператор правит в Excel и на которые
# ссылаются промты. Показываем их в «Базе» рядом с карточкой кадра.
_ROWS_PERSONS = (8, 23, 38)
_ROWS_ITEMS = (9, 24, 39)


async def _project(session: AsyncSession, project_id: int) -> Project:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, f"проект {project_id} не найден")
    return p


async def _frame(session: AsyncSession, frame_id: int) -> Frame:
    fr = await session.get(Frame, frame_id)
    if fr is None:
        raise HTTPException(404, f"кадр {frame_id} не найден")
    return fr


def _merged_plan_ids(ws, col: int, rows: tuple[int, ...]) -> str | None:
    seen: list[str] = []
    for row in rows:
        text = _cell_text(ws, row, col)
        if not text:
            continue
        for part in text.replace(";", ",").split(","):
            item = part.strip()
            if item and item not in seen:
                seen.append(item)
    return ", ".join(seen) if seen else None


def _excel_rows_for_project(project: Project) -> dict[int, dict[str, str | int | None]]:
    """Строки листа «план» project.xlsx по номеру кадра (для UI «Базы»).

    Excel пока остаётся источником правды для промтов — карточка кадра
    обязана показывать те же R45/R48/R49, что видел оператор в книге.
    """
    path = project.data_dir / "project.xlsx"
    if not path.is_file():
        return {}
    try:
        from openpyxl import load_workbook
    except ImportError:
        return {}
    try:
        wb = load_workbook(path, data_only=True)
    except Exception:  # noqa: BLE001
        return {}
    try:
        ws = _resolve_plan_sheet(wb)
        if ws is None:
            return {}
        out: dict[int, dict[str, str | int | None]] = {}
        max_col = ws.max_column or 0
        for col in range(3, max_col + 1):
            frame_no = col - 2
            values = {
                "column": col,
                "r15_timecode": _cell_text(ws, ROW_TIMECODE_V8, col),
                "r45_image_prompt": _cell_text(ws, ROW_IMAGE_PROMPT_V8, col),
                "r46_image_prompt_2": _cell_text(ws, ROW_IMAGE_PROMPT_2_V8, col),
                "r48_video_prompt": _cell_text(ws, ROW_VIDEO_PROMPT_V8, col),
                "r64_video_prompt_2": _cell_text(ws, ROW_VIDEO_PROMPT_2_V8, col),
                "r49_voiceover": _cell_text(ws, ROW_VOICEOVER_V8, col),
                "r50_duration": _cell_text(ws, ROW_DURATION_V8, col),
                "persons": _merged_plan_ids(ws, col, _ROWS_PERSONS),
                "items": _merged_plan_ids(ws, col, _ROWS_ITEMS),
            }
            if any(v not in (None, "") for k, v in values.items() if k != "column"):
                out[frame_no] = values
        return out
    finally:
        wb.close()


def _fmt_timecode(start: float | None, end: float | None) -> str | None:
    if start is None or end is None:
        return None

    def _ts(v: float) -> str:
        m = int(v // 60)
        s = v - m * 60
        return f"{m}:{s:05.2f}"

    return f"{_ts(float(start))}-{_ts(float(end))}"


def _export_project_xlsx(project: Project, frames: list[Frame]) -> dict:
    """DB → project.xlsx (лист «план»). Excel — производный view от DB.

    Пишет только строки, за которые DB отвечает: R45/R46/R48/R64/R49,
    плюс R50 (длительность) и R15 (таймкод), если они есть в DB.
    Персонажи/предметы/прочие строки не трогаем. Перед записью — backup.
    """
    from app.services.plan_shot2 import SHOT2_PROMPT_ATTR, SHOT2_VIDEO_PROMPT_ATTR
    from app.services.xlsx_versioning import backup_to_old

    path = project.data_dir / "project.xlsx"
    if not path.is_file():
        raise HTTPException(400, "project.xlsx не найден — сначала сгенерируй лист «план»")
    from openpyxl import load_workbook

    wb = load_workbook(path)
    try:
        ws = _resolve_plan_sheet(wb)
        if ws is None:
            raise HTTPException(400, "в project.xlsx нет листа «план» (v8)")
        snap = None
        try:
            snap = backup_to_old(path)
        except Exception:  # noqa: BLE001
            snap = None
        cells = 0
        for fr in frames:
            col = fr.number + 2
            attrs = fr.attrs or {}
            writes = {
                ROW_IMAGE_PROMPT_V8: fr.image_prompt or "",
                ROW_IMAGE_PROMPT_2_V8: str(attrs.get(SHOT2_PROMPT_ATTR) or ""),
                ROW_VIDEO_PROMPT_V8: fr.animation_prompt or "",
                ROW_VIDEO_PROMPT_2_V8: str(attrs.get(SHOT2_VIDEO_PROMPT_ATTR) or ""),
                ROW_VOICEOVER_V8: fr.voiceover_text or "",
            }
            for row, value in writes.items():
                ws.cell(row=row, column=col, value=value)
                cells += 1
            if fr.duration_seconds is not None:
                ws.cell(row=ROW_DURATION_V8, column=col, value=float(fr.duration_seconds))
                cells += 1
            tc = _fmt_timecode(fr.start_ts, fr.end_ts)
            if tc:
                ws.cell(row=ROW_TIMECODE_V8, column=col, value=tc)
                cells += 1
        general_plan = (project.meta or {}).get("general_plan")
        if general_plan:
            for name in wb.sheetnames:
                if _normalize_sheet_name(name).casefold() == _normalize_sheet_name(
                    SHEET_GENERAL_V8
                ).casefold():
                    wb[name]["B2"] = str(general_plan)
                    cells += 1
                    break
        wb.save(path)
    finally:
        wb.close()
    return {
        "frames": len(frames),
        "cells": cells,
        "backup": getattr(snap, "name", None),
        "path": str(path),
    }


@router.get("/overview")
async def db_overview(session: AsyncSession = Depends(get_session)) -> dict:
    projects = list((await session.execute(select(Project).order_by(Project.id))).scalars())
    out = []
    for p in projects:
        frames_n = (
            await session.execute(
                select(func.count(Frame.id)).where(Frame.project_id == p.id)
            )
        ).scalar_one()
        scenes_n = (
            await session.execute(
                select(func.count(Scene.id)).where(Scene.project_id == p.id)
            )
        ).scalar_one()
        entities_n = (
            await session.execute(
                select(func.count(Entity.id)).where(Entity.project_id == p.id)
            )
        ).scalar_one()
        edges_n = (
            await session.execute(
                select(func.count(FrameEdge.id)).where(FrameEdge.project_id == p.id)
            )
        ).scalar_one()
        out.append(
            {
                "id": p.id,
                "slug": p.slug,
                "title": p.title,
                "topic": p.topic,
                "status": p.status.value if p.status else None,
                "frames": frames_n,
                "scenes": scenes_n,
                "entities": entities_n,
                "edges": edges_n,
            }
        )
    return {"projects": out}


@router.get("/projects/{project_id}/graph")
async def db_graph(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    project = await _project(session, project_id)
    await db_v2.backfill_project_v2(session, project)
    await session.commit()
    graph = await db_v2.project_graph(session, project)
    graph["excel_rows"] = _excel_rows_for_project(project)
    return graph


@router.post("/projects/{project_id}/export-xlsx")
async def export_xlsx(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """Кнопка «Экспорт в Excel»: переписать строки листа «план» из DB."""
    project = await _project(session, project_id)
    frames = list(
        (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
            )
        ).scalars()
    )
    return _export_project_xlsx(project, frames)


# Человеческие имена полей → канонические ключи DB. Агент пишет по-человечески
# («закадр», «промт_картинки»), прога переводит сама. Ключ нормализуется:
# lower + пробелы→подчёркивания.
_FIELD_ALIASES: dict[str, str] = {
    "voiceover_text": "voiceover_text",
    "voiceover": "voiceover_text",
    "закадр": "voiceover_text",
    "закадровый_текст": "voiceover_text",
    "реплика": "voiceover_text",
    "image_prompt": "image_prompt",
    "img_prompt": "image_prompt",
    "промт_картинки": "image_prompt",
    "промпт_картинки": "image_prompt",
    "промт_картинка": "image_prompt",
    "картинка": "image_prompt",
    "animation_prompt": "animation_prompt",
    "video_prompt": "animation_prompt",
    "промт_видео": "animation_prompt",
    "промпт_видео": "animation_prompt",
    "видео_промт": "animation_prompt",
    "meaning": "meaning",
    "смысл": "meaning",
    "duration_seconds": "duration_seconds",
    "длительность": "duration_seconds",
    "время": "duration_seconds",
    "секунды": "duration_seconds",
    "image_prompt_shot2": "image_prompt_shot2",
    "img_prompt_2": "image_prompt_shot2",
    "промт_картинки_2": "image_prompt_shot2",
    "промпт_картинки_2": "image_prompt_shot2",
    "animation_prompt_shot2": "animation_prompt_shot2",
    "video_prompt_2": "animation_prompt_shot2",
    "промт_видео_2": "animation_prompt_shot2",
    "промпт_видео_2": "animation_prompt_shot2",
}

_PROJECT_FIELD_ALIASES: dict[str, str] = {
    "general_plan": "general_plan",
    "общий_план": "general_plan",
    "план": "general_plan",
}

_TARGETS = ("frame", "project")


def _canon_key(raw: str, aliases: dict[str, str]) -> str | None:
    key = str(raw).strip().lower().replace(" ", "_")
    return aliases.get(key)


def _normalize_fields(raw: dict, aliases: dict[str, str], *, scope: str) -> dict:
    out: dict = {}
    unknown: list[str] = []
    for k, v in (raw or {}).items():
        canon = _canon_key(k, aliases)
        if canon is None:
            unknown.append(str(k))
            continue
        out[canon] = v
    if unknown:
        allowed = sorted(set(aliases))
        raise HTTPException(
            400,
            f"{scope}: неизвестные поля {unknown}. "
            f"Разрешённые (можно по-человечески): {allowed}",
        )
    return out


class ApplyOp(BaseModel):
    target: str = Field(default="frame", max_length=16)
    frame_uuid: str | None = Field(default=None, max_length=64)
    fields: dict = Field(default_factory=dict)


class ApplyOpsBody(BaseModel):
    ops: list[ApplyOp]
    export_xlsx: bool = True


@router.post("/projects/{project_id}/apply-ops")
async def apply_ops(
    project_id: int,
    body: ApplyOpsBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fail-closed запись от GPT: JSON-операции.

    ``target="frame"`` (по умолчанию) — правка кадра по ``frame_uuid``;
    ``target="project"`` — поля уровня проекта (общий план). Поля можно
    писать по-человечески («закадр», «промт_картинки») — сервер сам
    переводит в канонические ключи (_FIELD_ALIASES). Неизвестный uuid,
    неизвестное поле, пустые fields/ops — 400, транзакция откатывается.
    После записи по умолчанию экспортируем в project.xlsx.
    """
    from app.services.plan_shot2 import SHOT2_PROMPT_ATTR, SHOT2_VIDEO_PROMPT_ATTR

    project = await _project(session, project_id)
    if not body.ops:
        raise HTTPException(400, "пустой ops — нечего применять")
    for op in body.ops:
        if op.target not in _TARGETS:
            raise HTTPException(
                400, f"неизвестный target {op.target!r}; разрешены: {list(_TARGETS)}"
            )

    frame_ops = [op for op in body.ops if op.target == "frame"]
    project_ops = [op for op in body.ops if op.target == "project"]

    by_uuid: dict[str, Frame] = {}
    if frame_ops:
        uuids = [op.frame_uuid or "" for op in frame_ops]
        if any(not u for u in uuids):
            raise HTTPException(400, "для target=frame нужен frame_uuid")
        frames = list(
            (
                await session.execute(
                    select(Frame).where(
                        Frame.project_id == project.id, Frame.uuid.in_(uuids)
                    )
                )
            ).scalars()
        )
        by_uuid = {f.uuid: f for f in frames}
        missing = [u for u in uuids if u not in by_uuid]
        if missing:
            raise HTTPException(400, f"неизвестные frame_uuid: {missing}")

    updated = 0
    for op in frame_ops:
        fields = _normalize_fields(
            op.fields, _FIELD_ALIASES, scope=f"кадр {op.frame_uuid}"
        )
        if not fields:
            raise HTTPException(400, f"кадр {op.frame_uuid}: пустые fields")
        fr = by_uuid[op.frame_uuid or ""]
        if "voiceover_text" in fields:
            fr.voiceover_text = str(fields["voiceover_text"] or "")
        if "meaning" in fields:
            fr.meaning = None if fields["meaning"] is None else str(fields["meaning"])
        if "duration_seconds" in fields:
            try:
                fr.duration_seconds = (
                    None
                    if fields["duration_seconds"] is None
                    else float(fields["duration_seconds"])
                )
            except (TypeError, ValueError):
                raise HTTPException(
                    400, f"кадр {op.frame_uuid}: длительность должна быть числом"
                ) from None
        if "image_prompt" in fields:
            fr.image_prompt = (
                None if fields["image_prompt"] is None else str(fields["image_prompt"])
            )
            await db_v2.add_prompt_version(
                session,
                project.id,
                fr.id,
                kind="img",
                text=fr.image_prompt or "",
                set_active=True,
            )
        if "animation_prompt" in fields:
            fr.animation_prompt = (
                None
                if fields["animation_prompt"] is None
                else str(fields["animation_prompt"])
            )
            await db_v2.add_prompt_version(
                session,
                project.id,
                fr.id,
                kind="video",
                text=fr.animation_prompt or "",
                set_active=True,
            )
        attrs = dict(fr.attrs or {})
        if "image_prompt_shot2" in fields:
            attrs[SHOT2_PROMPT_ATTR] = str(fields["image_prompt_shot2"] or "")
        if "animation_prompt_shot2" in fields:
            attrs[SHOT2_VIDEO_PROMPT_ATTR] = str(fields["animation_prompt_shot2"] or "")
        fr.attrs = attrs
        updated += 1

    for op in project_ops:
        fields = _normalize_fields(op.fields, _PROJECT_FIELD_ALIASES, scope="проект")
        if not fields:
            raise HTTPException(400, "проект: пустые fields")
        meta = dict(project.meta or {})
        if "general_plan" in fields:
            meta["general_plan"] = str(fields["general_plan"] or "")
        project.meta = meta
        updated += 1

    await session.flush()
    exported = None
    if body.export_xlsx:
        all_frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.number)
                )
            ).scalars()
        )
        exported = _export_project_xlsx(project, all_frames)
    await session.commit()
    return {"ok": True, "updated": updated, "exported": exported}


class FramePatch(BaseModel):
    status: str | None = None
    duration_seconds: float | None = None
    meaning: str | None = None
    voiceover_text: str | None = None
    attrs: dict | None = None


@router.patch("/frames/{frame_id}")
async def patch_frame(
    frame_id: int,
    body: FramePatch,
    session: AsyncSession = Depends(get_session),
) -> dict:
    fr = await _frame(session, frame_id)
    if body.status is not None:
        try:
            fr.status = FrameStatus(body.status)
        except ValueError:
            raise HTTPException(400, f"неизвестный статус: {body.status}") from None
    if body.duration_seconds is not None:
        fr.duration_seconds = body.duration_seconds
    if body.meaning is not None:
        fr.meaning = body.meaning
    if body.voiceover_text is not None:
        fr.voiceover_text = body.voiceover_text
    if body.attrs is not None:
        fr.attrs = body.attrs
    await session.commit()
    return {"ok": True, "id": fr.id, "uuid": fr.uuid, "sort_key": fr.sort_key}


class InsertFrameBody(BaseModel):
    after_frame_id: int | None = None
    scene_id: int | None = None


@router.post("/projects/{project_id}/frames/insert", status_code=201)
async def insert_frame(
    project_id: int,
    body: InsertFrameBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await _project(session, project_id)
    try:
        fr = await db_v2.insert_frame_after(
            session,
            project,
            after_frame_id=body.after_frame_id,
            scene_id=body.scene_id,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
    await session.commit()
    return {"id": fr.id, "uuid": fr.uuid, "sort_key": fr.sort_key, "scene_id": fr.scene_id}


class TextBody(BaseModel):
    kind: str = Field(default="extra", max_length=32)
    text: str


@router.post("/frames/{frame_id}/texts", status_code=201)
async def add_text(
    frame_id: int,
    body: TextBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    fr = await _frame(session, frame_id)
    t = FrameText(
        project_id=fr.project_id, frame_id=fr.id, kind=body.kind, text=body.text
    )
    session.add(t)
    await session.commit()
    return {"id": t.id, "kind": t.kind}


@router.delete("/texts/{text_id}")
async def delete_text(
    text_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    t = await session.get(FrameText, text_id)
    if t is None:
        raise HTTPException(404, "текст не найден")
    await session.delete(t)
    await session.commit()
    return {"ok": True}


class PromptBody(BaseModel):
    kind: str = Field(default="img", max_length=24)
    text: str
    set_active: bool = True


@router.post("/frames/{frame_id}/prompts", status_code=201)
async def add_prompt(
    frame_id: int,
    body: PromptBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    fr = await _frame(session, frame_id)
    pv = await db_v2.add_prompt_version(
        session,
        fr.project_id,
        fr.id,
        kind=body.kind,
        text=body.text,
        set_active=body.set_active,
    )
    if body.set_active and body.kind == "img":
        fr.image_prompt = body.text
    elif body.set_active and body.kind == "video":
        fr.animation_prompt = body.text
    await session.commit()
    return {"id": pv.id, "version": pv.version, "is_active": pv.is_active}


@router.post("/prompts/{prompt_id}/activate")
async def activate_prompt(
    prompt_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    pv = await session.get(PromptVersion, prompt_id)
    if pv is None:
        raise HTTPException(404, "версия промта не найдена")
    siblings = (
        await session.execute(
            select(PromptVersion).where(
                PromptVersion.frame_id == pv.frame_id,
                PromptVersion.kind == pv.kind,
                PromptVersion.is_active.is_(True),
            )
        )
    ).scalars().all()
    for s in siblings:
        s.is_active = False
    pv.is_active = True
    fr = await session.get(Frame, pv.frame_id)
    if fr is not None and pv.kind == "img":
        fr.image_prompt = pv.text
    elif fr is not None and pv.kind == "video":
        fr.animation_prompt = pv.text
    await session.commit()
    return {"ok": True, "id": pv.id}


class EntityBody(BaseModel):
    type: str = Field(max_length=24)
    code: str | None = Field(default=None, max_length=24)
    name: str | None = None
    attrs: dict = Field(default_factory=dict)


@router.post("/projects/{project_id}/entities", status_code=201)
async def add_entity(
    project_id: int,
    body: EntityBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await _project(session, project_id)
    max_key = (
        await session.execute(
            select(func.max(Entity.sort_key)).where(Entity.project_id == project.id)
        )
    ).scalar_one() or 0.0
    en = Entity(
        project_id=project.id,
        type=body.type,
        code=body.code,
        name=body.name,
        attrs=body.attrs,
        sort_key=float(max_key) + 10.0,
    )
    session.add(en)
    await session.commit()
    return {"id": en.id}


@router.patch("/entities/{entity_id}")
async def patch_entity(
    entity_id: int,
    body: EntityBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    en = await session.get(Entity, entity_id)
    if en is None:
        raise HTTPException(404, "сущность не найдена")
    en.type = body.type
    en.code = body.code
    en.name = body.name
    en.attrs = body.attrs
    await session.commit()
    return {"ok": True}


@router.delete("/entities/{entity_id}")
async def delete_entity(
    entity_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    en = await session.get(Entity, entity_id)
    if en is None:
        raise HTTPException(404, "сущность не найдена")
    await session.delete(en)
    await session.commit()
    return {"ok": True}


class EdgeBody(BaseModel):
    to_frame_id: int
    type: str = Field(default="next", max_length=24)


@router.post("/frames/{frame_id}/edges", status_code=201)
async def add_edge(
    frame_id: int,
    body: EdgeBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    fr = await _frame(session, frame_id)
    target = await _frame(session, body.to_frame_id)
    if target.project_id != fr.project_id:
        raise HTTPException(400, "связь между разными проектами запрещена")
    e = FrameEdge(
        project_id=fr.project_id,
        from_frame_id=fr.id,
        to_frame_id=target.id,
        type=body.type,
    )
    session.add(e)
    await session.commit()
    return {"id": e.id}


@router.delete("/edges/{edge_id}")
async def delete_edge(
    edge_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    e = await session.get(FrameEdge, edge_id)
    if e is None:
        raise HTTPException(404, "связь не найдена")
    await session.delete(e)
    await session.commit()
    return {"ok": True}


class SceneBody(BaseModel):
    title: str | None = None
    place: str | None = None
    meaning: str | None = None
    scene_type: str | None = None
    after_scene_id: int | None = None


@router.post("/projects/{project_id}/scenes", status_code=201)
async def add_scene(
    project_id: int,
    body: SceneBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await _project(session, project_id)
    scenes = list(
        (
            await session.execute(
                select(Scene).where(Scene.project_id == project.id).order_by(Scene.sort_key)
            )
        ).scalars()
    )
    before: float | None = None
    after: float | None = None
    if body.after_scene_id is not None:
        for i, sc in enumerate(scenes):
            if sc.id == body.after_scene_id:
                before = sc.sort_key
                after = scenes[i + 1].sort_key if i + 1 < len(scenes) else None
                break
    elif scenes:
        before = scenes[-1].sort_key
    sc = Scene(
        project_id=project.id,
        sort_key=db_v2.sort_key_between(before, after),
        title=body.title,
        place=body.place,
        meaning=body.meaning,
        scene_type=body.scene_type,
    )
    session.add(sc)
    await session.commit()
    return {"id": sc.id, "sort_key": sc.sort_key}
