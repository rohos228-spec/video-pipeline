"""Валидация финального payload сборщика против закадра и кадров."""

from __future__ import annotations

import re
from typing import Any

from app.models import Frame, Project


class SceneDesignValidationError(RuntimeError):
    """Собранный scene_registry не прошёл проверку покрытия/границ."""


def _norm_words(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def validate_payload(
    project: Project,
    frames: list[Frame],
    payload: dict[str, Any],
    full_vo: str,
) -> list[str]:
    """Список проблем; пустой = сборка принята.

    Проверки: границы сцен — дословные цитаты из полного закадра; каждый
    uuid кадра покрыт ровно одним op; ops.id_scene существует в scenes[].
    """
    _ = project
    problems: list[str] = []
    vo_norm = _norm_words(full_vo)
    scenes = payload.get("scenes") or []
    ops = payload.get("ops") or []

    scene_ids: set[str] = set()
    seen_quotes: dict[str, str] = {}
    for sc in scenes:
        if not isinstance(sc, dict):
            problems.append("scenes: элемент — не объект")
            continue
        sid = str(sc.get("id_scene") or "").strip()
        if not sid:
            problems.append("scenes: сцена без id_scene")
            continue
        scene_ids.add(sid)
        for key in ("start_words", "end_words"):
            quote = _norm_words(str(sc.get(key) or ""))
            if not quote:
                problems.append(f"{sid}: пустые {key}")
                continue
            if quote not in vo_norm:
                problems.append(f"{sid}: {key} не найдены в закадре: {quote[:60]!r}")
            owner = seen_quotes.get(quote)
            if owner is not None and owner != sid:
                problems.append(f"{sid}: {key} дублируют цитату сцены {owner}")
            else:
                seen_quotes[quote] = sid

    frame_uuids = {fr.uuid for fr in frames if fr.uuid}
    seen_ops: set[str] = set()
    for op in ops:
        if not isinstance(op, dict):
            problems.append("ops: элемент — не объект")
            continue
        uuid = str(op.get("frame_uuid") or "").strip()
        if uuid not in frame_uuids:
            problems.append(f"ops: неизвестный frame_uuid {uuid!r}")
            continue
        if uuid in seen_ops:
            problems.append(f"ops: дубль frame_uuid {uuid!r}")
        seen_ops.add(uuid)
        fields = op.get("fields") or {}
        if not isinstance(fields, dict) or not fields:
            problems.append(f"ops {uuid}: пустые fields")
            continue
        sid = str(
            fields.get("id_scene") or fields.get("shot01_id_scene") or ""
        ).strip()
        if sid and sid not in scene_ids:
            problems.append(f"ops {uuid}: id_scene {sid!r} отсутствует в scenes[]")

    missing = frame_uuids - seen_ops
    if missing:
        problems.append(f"ops: кадры без привязки: {len(missing)} шт")
    return problems
