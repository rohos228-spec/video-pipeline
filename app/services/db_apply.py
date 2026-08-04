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

# Аналитика / shot → Excel (лист «план»), хранится в Frame.attrs.
ROW_MAIN_ACTION_V8 = 52
ROW_PLACE_V8 = 54
ROW_ACCENT_V8 = 55
ROW_SCENE_SENSE_V8 = 56
ROW_VISUAL_TYPE_V8 = 57
ROW_SCENE_FEATURE_V8 = 58
ROW_CLUSTER_V8 = 59

# attr_key → номер строки Excel при экспорте
_ATTR_EXCEL_ROWS: dict[str, int] = {
    "main_action": ROW_MAIN_ACTION_V8,
    "place": ROW_PLACE_V8,
    "accent": ROW_ACCENT_V8,
    "scene_sense": ROW_SCENE_SENSE_V8,
    "visual_type": ROW_VISUAL_TYPE_V8,
    "scene_feature": ROW_SCENE_FEATURE_V8,
    "cluster": ROW_CLUSTER_V8,
    # shot_01
    "shot01_id_scene": 2,
    "shot01_id_shot": 3,
    "shot01_number": 4,
    "shot01_prev": 5,
    "shot01_next": 6,
    "shot01_bg": 7,
    "shot01_props": 9,
    "shot01_action": 10,
    "shot01_description": 11,
    "shot01_transition": 12,
    "shot01_notes": 13,
    "shot01_status": 14,
    # shot_02 (без 22–24)
    "shot02_id_scene": 16,
    "shot02_id_shot": 17,
    "shot02_number": 18,
    "shot02_prev": 19,
    "shot02_next": 20,
    "shot02_characters": 21,
    "shot02_action": 25,
    "shot02_description": 26,
    "shot02_transition": 27,
    "shot02_notes": 28,
    "shot02_status": 29,
}

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
    "промт_анимации": "animation_prompt",
    "промпт_анимации": "animation_prompt",
    "анимация": "animation_prompt",
    "meaning": "meaning",
    "смысл": "meaning",
    "описание_кадра": "meaning",
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
    # ID персонажей кадра (c01, c03) → Frame.attrs["characters"] + Excel R8
    "characters": "characters",
    "персонажи": "characters",
    "persons": "characters",
    # Аналитика 54–59 + главное действие 52 → Frame.attrs + Excel
    "main_action": "main_action",
    "главное_действие": "main_action",
    "главное_действие_в_кадре": "main_action",
    "place": "place",
    "место": "place",
    "accent": "accent",
    "акцент": "accent",
    "scene_sense": "scene_sense",
    "смысл_сцены": "scene_sense",
    "visual_type": "visual_type",
    "тип_сцены": "visual_type",
    "scene_feature": "scene_feature",
    "особенность_сцены": "scene_feature",
    "особенность": "scene_feature",
    "cluster": "cluster",
    "номер_кластера": "cluster",
    "кластер": "cluster",
    # shot_01
    "shot01_id_scene": "shot01_id_scene",
    "id_scene": "shot01_id_scene",
    "shot01_id_shot": "shot01_id_shot",
    "id_shot": "shot01_id_shot",
    "shot01_number": "shot01_number",
    "номер_кадра": "shot01_number",
    "shot01_prev": "shot01_prev",
    "предыдущий_кадр": "shot01_prev",
    "shot01_next": "shot01_next",
    "следующий_кадр": "shot01_next",
    "shot01_bg": "shot01_bg",
    "фон": "shot01_bg",
    "shot01_props": "shot01_props",
    "предметы": "shot01_props",
    "shot01_action": "shot01_action",
    "действие": "shot01_action",
    "shot01_description": "shot01_description",
    "описание_shot01": "shot01_description",
    "shot01_transition": "shot01_transition",
    "логика_перехода": "shot01_transition",
    "shot01_notes": "shot01_notes",
    "заметки_по_консистенции": "shot01_notes",
    "shot01_status": "shot01_status",
    "статус": "shot01_status",
    # shot_02
    "shot02_id_scene": "shot02_id_scene",
    "shot02_id_shot": "shot02_id_shot",
    "shot02_number": "shot02_number",
    "shot02_prev": "shot02_prev",
    "shot02_next": "shot02_next",
    "shot02_characters": "shot02_characters",
    "персонажи_shot02": "shot02_characters",
    "shot02_action": "shot02_action",
    "действие_shot02": "shot02_action",
    "shot02_description": "shot02_description",
    "описание_shot02": "shot02_description",
    "shot02_transition": "shot02_transition",
    "логика_перехода_shot02": "shot02_transition",
    "shot02_notes": "shot02_notes",
    "shot02_status": "shot02_status",
}

# Поля, которые живут в Frame.attrs (не колонки Frame.*).
_ATTR_FIELD_KEYS = frozenset(_ATTR_EXCEL_ROWS) | {
    "characters",
    "image_prompt_shot2",
    "animation_prompt_shot2",
}

PROJECT_FIELD_ALIASES: dict[str, str] = {
    "general_plan": "general_plan",
    "общий_план": "general_plan",
    "план": "general_plan",
    "script_text": "script_text",
    "закадровый_текст": "script_text",
    "сценарий": "script_text",
    "voiceover": "script_text",
}

TARGETS = ("frame", "project", "replace_frames")


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
    """Достать JSON с ``ops`` / ``characters`` из ответа модели."""
    if not text:
        return None
    candidates: list[str] = [m.group(1) for m in _JSON_FENCE_RE.finditer(text)]
    idxs = [
        i
        for i in (
            text.find('"ops"'),
            text.find('"actions"'),
            text.find('"characters"'),
        )
        if i != -1
    ]
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
            isinstance(data.get("ops"), list)
            or isinstance(data.get("actions"), list)
            or isinstance(data.get("characters"), list)
        ):
            return data
    return None


def _normalize_character_card(raw: dict[str, Any]) -> dict[str, Any]:
    """Карточка персонажа из ответа агента → code/name/attrs."""
    if not isinstance(raw, dict):
        raise ApplyOpsError("characters: каждый элемент — объект")
    code = str(
        raw.get("id") or raw.get("code") or raw.get("ID") or ""
    ).strip()
    if not code:
        raise ApplyOpsError("characters: нужен id (c01…)")
    name = str(raw.get("имя") or raw.get("name") or "").strip()
    look = str(raw.get("внешность") or raw.get("look") or "").strip()
    clothes = str(raw.get("одежда") or raw.get("clothes") or "").strip()
    char = str(raw.get("характер") or raw.get("char") or "").strip()
    rules = str(raw.get("правила") or raw.get("rules") or "").strip()
    return {
        "code": code,
        "name": name,
        "attrs": {
            "look": look,
            "clothes": clothes,
            "char": char,
            "rules": rules,
            "внешность": look,
            "одежда": clothes,
            "характер": char,
            "правила": rules,
        },
    }


async def upsert_characters(
    session: AsyncSession,
    project: Project,
    characters: list[Any],
) -> int:
    """Записать/обновить сущности type=character по code (c01…)."""
    from app.models import Entity

    if not isinstance(characters, list):
        raise ApplyOpsError("characters: нужен список")
    if not characters:
        return 0
    existing = list(
        (
            await session.execute(
                select(Entity).where(
                    Entity.project_id == project.id,
                    Entity.type == "character",
                )
            )
        ).scalars()
    )
    by_code = {
        str(e.code or "").strip(): e for e in existing if e.code
    }
    max_key = max((float(e.sort_key or 0) for e in existing), default=0.0)
    n = 0
    for raw in characters:
        card = _normalize_character_card(raw)
        code = card["code"]
        en = by_code.get(code)
        if en is None:
            max_key += 10.0
            en = Entity(
                project_id=project.id,
                type="character",
                code=code,
                name=card["name"],
                attrs=card["attrs"],
                sort_key=max_key,
            )
            session.add(en)
            by_code[code] = en
        else:
            en.name = card["name"]
            en.attrs = card["attrs"]
            en.type = "character"
        n += 1
    await session.flush()
    return n


def _write_persons_sheet(wb: Any, entities: list[Any]) -> int:
    """Синк листа «Персонажи» из Entity (колонки B.. как в excel_characters)."""
    from app.services.excel_characters import (
        ROW_CHAR,
        ROW_CLOTHES,
        ROW_ID,
        ROW_LOOK,
        ROW_NAME,
        ROW_RULES,
        SHEET_PERSONS,
    )

    chars = [
        e
        for e in entities
        if getattr(e, "type", None) == "character" and getattr(e, "code", None)
    ]
    chars.sort(key=lambda e: (float(getattr(e, "sort_key", 0) or 0), str(e.code)))
    if SHEET_PERSONS in wb.sheetnames:
        ws = wb[SHEET_PERSONS]
    else:
        ws = wb.create_sheet(SHEET_PERSONS)
        ws["A1"] = "ID персонажа"
        ws["A3"] = "имя"
        ws["A4"] = "внешность"
        ws["A5"] = "одежда"
        ws["A6"] = "характер"
        ws["A7"] = "правила"
    # Чистим старые колонки B.. (до разумного лимита)
    for col in range(2, max(ws.max_column or 2, 2) + 1):
        for row in (ROW_ID, ROW_NAME, ROW_LOOK, ROW_CLOTHES, ROW_CHAR, ROW_RULES):
            ws.cell(row=row, column=col, value=None)
    cells = 0
    for i, en in enumerate(chars):
        col = 2 + i
        attrs = en.attrs if isinstance(en.attrs, dict) else {}
        look = str(attrs.get("look") or attrs.get("внешность") or "")
        clothes = str(attrs.get("clothes") or attrs.get("одежда") or "")
        char = str(attrs.get("char") or attrs.get("характер") or "")
        rules = str(attrs.get("rules") or attrs.get("правила") or "")
        ws.cell(row=ROW_ID, column=col, value=str(en.code))
        ws.cell(row=ROW_NAME, column=col, value=str(en.name or ""))
        ws.cell(row=ROW_LOOK, column=col, value=look)
        ws.cell(row=ROW_CLOTHES, column=col, value=clothes)
        ws.cell(row=ROW_CHAR, column=col, value=char)
        ws.cell(row=ROW_RULES, column=col, value=rules)
        cells += 6
    return cells


def _fmt_timecode(start: float | None, end: float | None) -> str | None:
    if start is None or end is None:
        return None

    def _ts(v: float) -> str:
        m = int(v // 60)
        s = v - m * 60
        return f"{m}:{s:05.2f}"

    return f"{_ts(float(start))}-{_ts(float(end))}"


def export_project_xlsx(
    project: Project,
    frames: list[Frame],
    *,
    entities: list[Any] | None = None,
) -> dict:
    """DB → project.xlsx (план + Общий план + Персонажи + R8).

    Пишет строки DB: R45/R46/R48/R64/R49, R50, R15, плюс R8 (персонажи кадра)
    и лист «Персонажи» из Entity. Перед записью — backup.
    """
    from app.services.plan_shot2 import (
        SHOT2_PROMPT_ATTR,
        SHOT2_STATUS_ATTR,
        SHOT2_VIDEO_PROMPT_ATTR,
    )
    from app.services.xlsx_versioning import backup_to_old

    ROW_PERSONS_PRIMARY = 8  # как в generate_images / baza

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
            persons = str(
                attrs.get("characters")
                or attrs.get("persons")
                or attrs.get("персонажи")
                or ""
            ).strip()
            ws.cell(row=ROW_PERSONS_PRIMARY, column=col, value=persons)
            cells += 1
            # Аналитика 52/54–59 + shot_01/shot_02 из attrs
            for attr_key, row in _ATTR_EXCEL_ROWS.items():
                if attr_key not in attrs:
                    continue
                val = attrs.get(attr_key)
                if val is None or str(val).strip() == "":
                    continue
                ws.cell(row=row, column=col, value=str(val))
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
        if entities is not None:
            cells += _write_persons_sheet(wb, entities)
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
    ops: list[dict[str, Any]] | None = None,
    *,
    characters: list[Any] | None = None,
    export_xlsx: bool = True,
) -> dict:
    """Применить JSON-операции к проекту (fail-closed, одна транзакция).

    ``ops`` — список ``{"target": "frame"|"project"|"replace_frames", ...}``.
    ``characters`` — реестр персонажей ``[{id, имя, внешность, …}]`` → Entity.
    ``replace_frames``: ``{"target":"replace_frames","frames":[{"закадр":"…"},…]}``.
    После записи по умолчанию экспортируем в project.xlsx.
    """
    from app.models import Entity
    from app.services.plan_shot2 import (
        SHOT2_PROMPT_ATTR,
        SHOT2_STATUS_ATTR,
        SHOT2_VIDEO_PROMPT_ATTR,
    )

    ops = list(ops or [])
    chars_n = 0
    if characters:
        chars_n = await upsert_characters(session, project, characters)
    if not ops and not chars_n:
        raise ApplyOpsError("пустой ops — нечего применять")
    for op in ops:
        if op.get("target", "frame") not in TARGETS:
            raise ApplyOpsError(
                f"неизвестный target {op.get('target')!r}; разрешены: {list(TARGETS)}"
            )

    replace_ops = [op for op in ops if op.get("target") == "replace_frames"]
    if len(replace_ops) > 1:
        raise ApplyOpsError("replace_frames: только одна операция за раз")
    if replace_ops and len(ops) > 1:
        raise ApplyOpsError(
            "replace_frames нельзя смешивать с другими ops в одном запросе"
        )

    frame_ops = [op for op in ops if op.get("target", "frame") == "frame"]
    project_ops = [op for op in ops if op.get("target", "frame") == "project"]

    if replace_ops:
        specs = replace_ops[0].get("frames") or replace_ops[0].get("кадры") or []
        if not isinstance(specs, list):
            raise ApplyOpsError("replace_frames: нужен список frames")
        try:
            created = await db_v2.replace_all_frames(session, project, specs)
        except ValueError as e:
            raise ApplyOpsError(str(e)) from None
        exported = None
        if export_xlsx:
            exported = export_project_xlsx(project, created)
        return {
            "ok": True,
            "updated": len(created),
            "exported": exported,
            "replace_frames": len(created),
        }

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
            # Чтобы шаг images подхватил кадр после excel_gpt/img_pr apply-ops.
            from app.models import FrameStatus
            from app.generation_options import is_skippable_empty_prompt

            if not is_skippable_empty_prompt(fr.image_prompt or "") and fr.status not in (
                FrameStatus.image_generated,
                FrameStatus.image_approved,
            ):
                fr.status = FrameStatus.image_prompt_ready
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
            from app.generation_options import is_skippable_empty_prompt

            if not is_skippable_empty_prompt(attrs[SHOT2_PROMPT_ATTR]):
                attrs[SHOT2_STATUS_ATTR] = "image_prompt_ready"

        if "animation_prompt_shot2" in fields:
            attrs[SHOT2_VIDEO_PROMPT_ATTR] = str(fields["animation_prompt_shot2"] or "")
        if "characters" in fields:
            persons = str(fields["characters"] or "").strip()
            attrs["characters"] = persons
            attrs["persons"] = persons
            attrs["персонажи"] = persons
        for attr_key in _ATTR_EXCEL_ROWS:
            if attr_key in fields:
                attrs[attr_key] = str(fields[attr_key] or "")
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
            plan_val = str(fields["general_plan"] or "")
            meta["general_plan"] = plan_val
            # Колонка HITL/статусов — тот же SoT, что meta (не только Excel B2).
            project.general_plan = plan_val
        if "script_text" in fields:
            project.script_text = str(fields["script_text"] or "")
            # Файл voiceover.txt — артефакт для audio/music (зеркало DB).
            try:
                vo_path = project.data_dir / "voiceover.txt"
                vo_path.parent.mkdir(parents=True, exist_ok=True)
                vo_path.write_text(project.script_text or "", encoding="utf-8")
            except OSError:
                pass
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
        ents = list(
            (
                await session.execute(
                    select(Entity).where(Entity.project_id == project.id)
                )
            ).scalars()
        )
        exported = export_project_xlsx(project, all_frames, entities=ents)
    return {
        "ok": True,
        "updated": updated,
        "characters": chars_n,
        "exported": exported,
    }
