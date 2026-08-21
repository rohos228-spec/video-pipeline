"""Нарезка входа сборщика на чанки + склейка частичных payload.

Один GPT-запрос на весь ролик (~100k+ JSON) ломает kie (524/500).
Режем по группам Frame-строк пайплайна; в каждый чанк кладём только
пересекающиеся scenes_chrono / shot_plan_chrono.
"""

from __future__ import annotations

import copy
from typing import Any

from app.models import Frame
from app.services.scene_design.chronology import frame_offsets, _norm
from app.services.scene_design.context_builder import frame_seconds


def _frame_span(
    frames: list[Frame],
    offsets: dict[str, int | None],
) -> tuple[int, int] | None:
    """[lo, hi) в нормализованном закадре для набора кадров."""
    lo: int | None = None
    hi: int | None = None
    for fr in frames:
        if not fr.uuid:
            continue
        off = offsets.get(fr.uuid)
        if off is None:
            continue
        text_len = len(_norm(fr.voiceover_text or ""))
        end = off + max(text_len, 1)
        lo = off if lo is None else min(lo, off)
        hi = end if hi is None else max(hi, end)
    if lo is None or hi is None:
        return None
    return lo, hi


def _overlaps(span: tuple[int, int], lo: int, hi: int) -> bool:
    a, b = span
    if a < 0:
        return True  # неизвестные границы — не отбрасываем
    end = b if b > a else a + 1
    return a < hi and end > lo


def _shot_offset(shot: dict[str, Any], vo_norm: str) -> int | None:
    quote = _norm(str(shot.get("цитата") or ""))
    if not quote:
        return None
    idx = vo_norm.find(quote)
    return idx if idx >= 0 else None


def _scene_span_tuple(scene: dict[str, Any], vo_norm: str) -> tuple[int, int]:
    start_q = _norm(str(scene.get("start_words") or ""))
    end_q = _norm(str(scene.get("end_words") or ""))
    start = vo_norm.find(start_q) if start_q else -1
    end_idx = vo_norm.find(end_q) if end_q else -1
    end = end_idx + len(end_q) if end_idx != -1 else -1
    return start, end


def split_frame_batches(
    frames: list[Frame],
    *,
    max_frames: int,
) -> list[list[Frame]]:
    """Пачки Frame по порядку number; max_frames >= 1."""
    ordered = [f for f in frames if f.uuid]
    n = max(1, int(max_frames))
    if not ordered:
        return []
    return [ordered[i : i + n] for i in range(0, len(ordered), n)]


def filter_assembly_input_for_frames(
    assembly_input: dict[str, Any],
    chunk_frames: list[Frame],
    all_frames: list[Frame],
    full_vo: str,
    *,
    include_characters: bool,
) -> dict[str, Any]:
    """Срез assembly_input под чанк: только пересекающиеся сцены/шоты."""
    offsets = frame_offsets(all_frames, full_vo)
    span = _frame_span(chunk_frames, offsets)
    vo_norm = _norm(full_vo)
    lo, hi = span if span else (0, len(vo_norm) or 1)

    scenes_in = list(assembly_input.get("scenes_chrono") or [])
    shots_in = list(assembly_input.get("shot_plan_chrono") or [])

    scenes_out: list[dict[str, Any]] = []
    for sc in scenes_in:
        if not isinstance(sc, dict):
            continue
        sc_span = _scene_span_tuple(sc, vo_norm)
        if _overlaps(sc_span, lo, hi):
            row = copy.deepcopy(sc)
            # Оставляем только кадры этого чанка внутри сцены.
            chunk_uuids = {f.uuid for f in chunk_frames if f.uuid}
            kids = [
                k
                for k in (row.get("кадры") or [])
                if isinstance(k, dict) and k.get("uuid") in chunk_uuids
            ]
            if kids:
                row["кадры"] = kids
                row["кадров"] = len(kids)
                row["время_сек"] = round(
                    sum(float(k.get("время_сек") or 0.0) for k in kids), 1
                )
            scenes_out.append(row)

    shots_out: list[dict[str, Any]] = []
    for sh in shots_in:
        if not isinstance(sh, dict):
            continue
        off = _shot_offset(sh, vo_norm)
        if off is None or (lo <= off < hi):
            shots_out.append(copy.deepcopy(sh))

    # Если фильтр сцен пуст — не оставляем сборщик без ориентира: отдай пустой
    # scenes и полный shot overlap; GPT соберёт границы по camera.
    out: dict[str, Any] = {
        "characters": (
            copy.deepcopy(list(assembly_input.get("characters") or []))
            if include_characters
            else []
        ),
        "locations": copy.deepcopy(list(assembly_input.get("locations") or [])),
        "style_arc": copy.deepcopy(list(assembly_input.get("style_arc") or [])),
        "scenes_chrono": scenes_out,
        "shot_plan_chrono": shots_out,
        "chunk_meta": {
            "vo_lo": lo,
            "vo_hi": hi,
            "frame_numbers": [f.number for f in chunk_frames],
            "frame_count": len(chunk_frames),
        },
    }
    return out


def build_chunk_context(
    full_vo: str,
    chunk_frames: list[Frame],
    *,
    chunk_index: int,
    chunk_total: int,
) -> str:
    """Контекст чанка: полный закадр (для цитат) + только кадры чанка."""
    frames_payload = []
    total_sec = 0.0
    for fr in chunk_frames:
        sec, source = frame_seconds(fr)
        total_sec += sec
        frames_payload.append(
            {
                "uuid": fr.uuid,
                "number": fr.number,
                "закадр": (fr.voiceover_text or "").strip(),
                "время_сек": sec,
                "время_источник": source,
            }
        )
    import json

    return (
        f"# ЧАСТИЧНАЯ СБОРКА — чанк {chunk_index}/{chunk_total}\n"
        "Собери scenes[] и ops[] ТОЛЬКО для кадров из КАДРЫ ниже.\n"
        "ops: ровно один на каждый uuid из этого списка, без чужих uuid.\n"
        "characters[]: полный реестр только если чанк 1; иначе [].\n"
        "Границы сцен — дословные цитаты из ПОЛНОГО ЗАКАДРА, покрывающие "
        "закадр этих кадров (стык с соседними чанками по смыслу).\n"
        "Camera shot_plan в ячейках — закон визуала для этого отрезка.\n\n"
        f"# ПОЛНЫЙ ЗАКАДР\n{full_vo}\n\n"
        f"# КАДРЫ ЧАНКА (JSON) — ИТОГО {round(total_sec, 1)} сек\n"
        + json.dumps(frames_payload, ensure_ascii=False, indent=1)
    )


def normalize_op_frame_uuid(op: dict[str, Any]) -> str:
    """frame_uuid из корня или fields (модели часто пишут uuid/frame)."""
    for key in ("frame_uuid", "uuid", "frame"):
        u = str(op.get(key) or "").strip()
        if u:
            return u
    fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
    for key in ("frame_uuid", "uuid", "frame"):
        u = str(fields.get(key) or "").strip()
        if u:
            return u
    return ""


def merge_assembler_payloads(
    parts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Склеить частичные ответы: characters из первого непустого, scenes/ops подряд с перенумерацией id_scene."""
    characters: list[Any] = []
    scenes: list[dict[str, Any]] = []
    ops: list[dict[str, Any]] = []
    reports: list[str] = []
    id_map: dict[str, str] = {}
    scene_i = 0
    dropped_no_uuid = 0

    for part in parts:
        if not isinstance(part, dict):
            continue
        chars = part.get("characters") or []
        if not characters and isinstance(chars, list) and chars:
            characters = list(chars)
        for sc in part.get("scenes") or []:
            if not isinstance(sc, dict):
                continue
            old = str(sc.get("id_scene") or "").strip()
            scene_i += 1
            new = f"scene_{scene_i:02d}"
            if old:
                id_map[old] = new
            row = dict(sc)
            row["id_scene"] = new
            scenes.append(row)
        for op in part.get("ops") or []:
            if not isinstance(op, dict):
                continue
            op2 = dict(op)
            fields = dict(op2.get("fields") or {}) if isinstance(op2.get("fields"), dict) else {}
            uid = normalize_op_frame_uuid(op2)
            if uid:
                op2["frame_uuid"] = uid
            else:
                dropped_no_uuid += 1
            sid = str(fields.get("id_scene") or fields.get("shot01_id_scene") or "").strip()
            if sid and sid in id_map:
                fields["id_scene"] = id_map[sid]
            op2["fields"] = fields
            ops.append(op2)
        rep = str(part.get("report") or "").strip()
        if rep:
            reports.append(rep)

    # Дедуп ops по frame_uuid (последний побеждает).
    by_uuid: dict[str, dict[str, Any]] = {}
    for op in ops:
        u = str(op.get("frame_uuid") or "").strip()
        if u:
            by_uuid[u] = op
    ops = list(by_uuid.values())

    return {
        "characters": characters,
        "scenes": scenes,
        "ops": ops,
        "report": (
            f"chunks:{len(parts)}; scenes:{len(scenes)}; ops:{len(ops)}; "
            f"dropped_no_uuid:{dropped_no_uuid}; "
            + " | ".join(reports)[:1500]
        ),
    }

