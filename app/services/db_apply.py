"""Единый fail-closed контракт записи в DB v2 (`apply-ops`).

Используется в двух местах:

1. REST endpoint `POST /api/db/projects/{id}/apply-ops` (тонкая обёртка).
2. GPT-ноды оркестратора: модель возвращает JSON ``{"ops": [...]}`` —
   ``extract_apply_ops_json`` достаёт его из ответа, ``apply_ops`` пишет.

Поля можно писать по-человечески («закадр», «промт_картинки») —
``FIELD_ALIASES`` переводит в канонические ключи. Ошибки — ``ApplyOpsError``
(неизвестный uuid/поле/target, пустые ops/fields): вызывающий код решает,
вернуть ли 400 (HTTP) или залогировать и откатить шаг (оркестратор).
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
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
    _normalize_sheet_name,
    _resolve_plan_sheet,
)

# Человеческие имена полей → канонические ключи DB. Ключ нормализуется:
# lower + пробелы→подчёркивания.
FIELD_ALIASES: dict[str, str] = {
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

PROJECT_FIELD_ALIASES: dict[str, str] = {
    "general_plan": "general_plan",
    "общий_план": "general_plan",
    "план": "general_plan",
}

TARGETS = ("frame", "project")


class ApplyOpsError(ValueError):
    """Ошибка валидации apply-ops (неизвестный uuid/поле/target, пустые ops)."""


def _canon_key(raw: str, aliases: dict[str, str]) -> str | None:
    key = str(raw).strip().lower().replace(" ", "_")
    return aliases.get(key)


def normalize_fields(raw: dict, aliases: dict[str, str], *, scope: str) -> dict:
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
        raise ApplyOpsError(
            f"{scope}: неизвестные поля {unknown}. "
            f"Разрешённые (можно по-человечески): {allowed}"
        )
    return out


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_apply_ops_json(text: str) -> dict[str, Any] | None:
    """Достать JSON с ключом ``ops`` из ответа модели (fence или голый объект)."""
    if not text:
        return None
    candidates: list[str] = [m.group(1) for m in _JSON_FENCE_RE.finditer(text)]
    idxs = [i for i in (text.find('"ops"'), text.find('"actions"')) if i != -1]
    idx = min(idxs) if idxs else -1
    if idx != -1:
        start = text.rfind("{", 0, idx)
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                ch = text[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[start : i + 1])
                        break
    for cand in candidates:
        try:
            data = json.loads(cand)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict) and (
            isinstance(data.get("ops"), list) or isinstance(data.get("actions"), list)
        ):
            return data
    return None


def _fmt_timecode(start: float | None, end: float | None) -> str | None:
    if start is None or end is None:
        return None

    def _ts(v: float) -> str:
        m = int(v // 60)
        s = v - m * 60
        return f"{m}:{s:05.2f}"

    return f"{_ts(float(start))}-{_ts(float(end))}"


def export_project_xlsx(project: Project, frames: list[Frame]) -> dict:
    """DB → project.xlsx (лист «план» + «Общий план»!B2 из Project.meta).

    Пишет только строки, за которые DB отвечает: R45/R46/R48/R64/R49,
    плюс R50 (длительность) и R15 (таймкод), если они есть в DB.
    Персонажи/предметы/прочие строки не трогаем. Перед записью — backup.
    """
    from app.services.plan_shot2 import SHOT2_PROMPT_ATTR, SHOT2_VIDEO_PROMPT_ATTR
    from app.services.xlsx_versioning import backup_to_old

    path = project.data_dir / "project.xlsx"
    if not path.is_file():
        raise ApplyOpsError("project.xlsx не найден — сначала сгенерируй лист «план»")
    from openpyxl import load_workbook

    wb = load_workbook(path)
    try:
        ws = _resolve_plan_sheet(wb)
        if ws is None:
            raise ApplyOpsError("в project.xlsx нет листа «план» (v8)")
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


async def apply_ops(
    session: AsyncSession,
    project: Project,
    ops: list[dict[str, Any]],
    *,
    export_xlsx: bool = True,
) -> dict:
    """Применить JSON-операции к проекту (fail-closed, одна транзакция).

    ``ops`` — список ``{"target": "frame"|"project", "frame_uuid": str|None,
    "fields": {...}}``. После записи по умолчанию экспортируем в project.xlsx.
    """
    from app.services.plan_shot2 import SHOT2_PROMPT_ATTR, SHOT2_VIDEO_PROMPT_ATTR

    if not ops:
        raise ApplyOpsError("пустой ops — нечего применять")
    for op in ops:
        if op.get("target", "frame") not in TARGETS:
            raise ApplyOpsError(
                f"неизвестный target {op.get('target')!r}; разрешены: {list(TARGETS)}"
            )

    frame_ops = [op for op in ops if op.get("target", "frame") == "frame"]
    project_ops = [op for op in ops if op.get("target", "frame") == "project"]

    by_uuid: dict[str, Frame] = {}
    if frame_ops:
        uuids = [str(op.get("frame_uuid") or "") for op in frame_ops]
        if any(not u for u in uuids):
            raise ApplyOpsError("для target=frame нужен frame_uuid")
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
            raise ApplyOpsError(f"неизвестные frame_uuid: {missing}")

    updated = 0
    for op in frame_ops:
        uuid = str(op.get("frame_uuid") or "")
        fields = normalize_fields(op.get("fields") or {}, FIELD_ALIASES, scope=f"кадр {uuid}")
        if not fields:
            raise ApplyOpsError(f"кадр {uuid}: пустые fields")
        fr = by_uuid[uuid]
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
                raise ApplyOpsError(
                    f"кадр {uuid}: длительность должна быть числом"
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
        fields = normalize_fields(
            op.get("fields") or {}, PROJECT_FIELD_ALIASES, scope="проект"
        )
        if not fields:
            raise ApplyOpsError("проект: пустые fields")
        meta = dict(project.meta or {})
        if "general_plan" in fields:
            meta["general_plan"] = str(fields["general_plan"] or "")
        project.meta = meta
        updated += 1

    await session.flush()
    exported = None
    if export_xlsx:
        all_frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.number)
                )
            ).scalars()
        )
        exported = export_project_xlsx(project, all_frames)
    return {"ok": True, "updated": updated, "exported": exported}
