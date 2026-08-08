"""Хронологическая выкладка staging-ячеек scene_design.

Ячейки → сгруппированные сущности → сцены/шоты/этапы стиля в порядке
закадра (по ``vo_offset``) → кадры, привязанные к сценам по текстовым
диапазонам. Результат — упорядоченный вход финального сборщика: он
получает данные уже в хронологии и с привязкой, а не сырые срезы.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.models import Frame, Project, SceneDesignCell


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def _parse_value(raw: str) -> Any:
    raw = (raw or "").strip()
    if raw.startswith("[") or raw.startswith("{"):
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return raw
    return raw


def group_cells(
    cells: list[SceneDesignCell],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Ячейки → {kind: {target_key: {field: value, _vo_offset, _agent}}}."""
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for cell in cells:
        if cell.status != "ok":
            continue
        entity = grouped.setdefault(cell.kind, {}).setdefault(
            cell.target_key, {"_vo_offset": cell.vo_offset, "_agent": cell.agent}
        )
        entity[cell.field] = _parse_value(cell.value)
    return grouped


def _sorted_entities(entities: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Сущности в хронологии закадра; без offset — в конец, по ключу."""

    def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
        key, data = item
        offset = data.get("_vo_offset")
        return (offset if isinstance(offset, int) else 1 << 30, key)

    out: list[dict[str, Any]] = []
    for key, data in sorted(entities.items(), key=_sort_key):
        row = {k: v for k, v in data.items() if not k.startswith("_")}
        row["id"] = key
        out.append(row)
    return out


def frame_offsets(frames: list[Frame], full_vo: str) -> dict[str, int | None]:
    """Смещение закадра каждого кадра в полном закадре.

    Поиск последовательный (курсор движется вперёд), чтобы повторяющиеся
    реплики привязывались к своим позициям, а не к первому вхождению.
    """
    vo_norm = _norm(full_vo)
    offsets: dict[str, int | None] = {}
    cursor = 0
    for fr in frames:
        text = _norm(fr.voiceover_text or "")
        if not text or not fr.uuid:
            continue
        idx = vo_norm.find(text, cursor)
        if idx == -1:
            idx = vo_norm.find(text)  # fallback: с начала
        if idx == -1:
            offsets[fr.uuid] = None
            continue
        offsets[fr.uuid] = idx
        cursor = idx + len(text)
    return offsets


def _scene_spans(scenes: list[dict[str, Any]], full_vo: str) -> list[dict[str, Any]]:
    """Диапазоны [start, end) каждой сцены в нормализованном закадре."""
    vo_norm = _norm(full_vo)
    spans: list[dict[str, Any]] = []
    for row in scenes:
        start_q = _norm(str(row.get("start_words") or ""))
        end_q = _norm(str(row.get("end_words") or ""))
        start = vo_norm.find(start_q) if start_q else -1
        end_idx = vo_norm.find(end_q) if end_q else -1
        end = end_idx + len(end_q) if end_idx != -1 else -1
        spans.append({"row": row, "start": start, "end": end})
    return spans


def assign_frames_to_scenes(
    scenes: list[dict[str, Any]],
    frames: list[Frame],
    offsets: dict[str, int | None],
    full_vo: str,
) -> list[dict[str, Any]]:
    """Прописать каждой сцене её кадры (in-place, ключ ``кадры``).

    Возвращает кадры, не попавшие ни в один диапазон (например, когда
    сцен нет вовсе) — сборщик обязан покрыть и их.
    """
    spans = _scene_spans(scenes, full_vo)
    for sp in spans:
        sp["row"]["кадры"] = []
    unassigned: list[dict[str, Any]] = []
    for fr in frames:
        if not fr.uuid:
            continue
        offset = offsets.get(fr.uuid)
        frame_row = {
            "uuid": fr.uuid,
            "number": fr.number,
            "закадр": (fr.voiceover_text or "").strip(),
            "длительность": fr.duration_seconds,
        }
        target = None
        if offset is not None:
            for sp in spans:
                if sp["start"] == -1:
                    continue
                end = sp["end"] if sp["end"] != -1 else 1 << 30
                if sp["start"] <= offset < end:
                    target = sp
                    break
            if target is None:
                # Кадр за пределами всех диапазонов — к ближайшей сцене слева.
                left = [sp for sp in spans if sp["start"] != -1 and sp["start"] <= offset]
                target = left[-1] if left else (spans[0] if spans else None)
        if target is None:
            unassigned.append(frame_row)
        else:
            target["row"]["кадры"].append(frame_row)
    return unassigned


def build_assembly_input(
    project: Project,
    frames: list[Frame],
    cells: list[SceneDesignCell],
    full_vo: str,
) -> dict[str, Any]:
    """Упорядоченный вход сборщика из staging-ячеек.

    Сцены, шоты и этапы стиля — в хронологии закадра; у каждой сцены —
    её кадры с uuid. Сборщику остаётся слить сущности и выдать ops.
    """
    _ = project
    grouped = group_cells(cells)
    scenes = _sorted_entities(grouped.get("scene", {}))
    for row in scenes:
        row["id_scene"] = row.pop("id")
    shots = _sorted_entities(grouped.get("shot", {}))
    stages = _sorted_entities(grouped.get("style_stage", {}))
    characters = _sorted_entities(grouped.get("character", {}))
    locations = _sorted_entities(grouped.get("location", {}))

    offsets = frame_offsets(frames, full_vo)
    unassigned = assign_frames_to_scenes(scenes, frames, offsets, full_vo)

    payload: dict[str, Any] = {
        "characters": characters,
        "locations": locations,
        "style_arc": stages,
        "scenes_chrono": scenes,
        "shot_plan_chrono": shots,
    }
    if unassigned:
        payload["кадры_без_диапазона"] = unassigned
    return payload
