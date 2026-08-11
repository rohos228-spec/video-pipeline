"""Adaptive split fat scene_design GPT-вызовов при 524/timeout.

Один action/camera на весь ролик часто ловит Cloudflare 524. При capacity-failure:
кадры пополам → ещё раз пополам (depth≤2); дальше — ошибка.
"""

from __future__ import annotations

import copy
from typing import Any

from loguru import logger

from app.services.scene_design import agents as ag
from app.services.scene_design.assemble_chunks import (
    _frame_span,
    _overlaps,
    _scene_span_tuple,
)
from app.services.scene_design.chronology import _norm, frame_offsets

SPLITTABLE_AGENTS = frozenset({"action", "camera"})
# depth 0 = целый запрос; 1 = /2; 2 = /4; дальше не дробим.
MAX_SPLIT_DEPTH = 2


class CapacitySplitExhausted(ag.SceneDesignAgentError):
    """Уже дробили /2 и /4 — дальше только hard-fail, без повторного split."""


def is_capacity_failure(exc: BaseException) -> bool:
    """524 / 5xx / timeout / network — имеет смысл дробить payload."""
    if isinstance(exc, CapacitySplitExhausted):
        return False
    try:
        from app.services.gpt_api import GptApiError
    except Exception:  # noqa: BLE001
        GptApiError = ()  # type: ignore[misc, assignment]

    if GptApiError and isinstance(exc, GptApiError):
        kind = str(exc.context.get("error_kind") or "")
        code = int(
            exc.context.get("provider_code") or exc.context.get("status_code") or 0
        )
        if kind in ("timeout", "network") or code >= 500:
            return True
    msg = str(exc).lower()
    return any(
        x in msg
        for x in (
            "http 524",
            "http 502",
            "http 503",
            "http 500",
            "timeout",
            "cloudflare",
            "server exception",
        )
    )


def split_frames_half(frames: list[Any]) -> tuple[list[Any], list[Any]]:
    """Пополам по порядку number; обе половины непустые."""
    ordered = [f for f in frames if getattr(f, "uuid", None)]
    if len(ordered) < 2:
        raise ValueError("split_frames_half: нужно ≥2 кадра с uuid")
    mid = max(1, min(len(ordered) // 2, len(ordered) - 1))
    return ordered[:mid], ordered[mid:]


def split_frames_batches(frames: list[Any], *, max_frames: int) -> list[list[Any]]:
    """Пачки кадров ≤ max_frames (проактивный чанк до 524)."""
    ordered = [f for f in frames if getattr(f, "uuid", None)]
    n = max(1, int(max_frames))
    if not ordered:
        return []
    return [ordered[i : i + n] for i in range(0, len(ordered), n)]


def chunk_instruction(*, label: str, depth: int) -> str:
    return (
        f"# ЧАНК ЗАКАДРА (часть «{label}», split_depth={depth})\n"
        "Работай ТОЛЬКО с # ПОЛНЫЙ ЗАКАДР и # КАДРЫ этого чанка.\n"
        "start_words / end_words — дословные цитаты из закадра чанка.\n"
        "Не покрывай закадр вне чанка. Не пиши «продолжение следует».\n"
        "Плотность фаз/шотов — как для полного ролика, но только на этот отрезок."
    )


def filter_action_scenes_for_frames(
    scenes: list[Any],
    chunk_frames: list[Any],
    all_frames: list[Any],
    full_vo: str,
) -> list[dict[str, Any]]:
    """Сцены action, пересекающиеся с VO-диапазоном чанка; иначе пропорция списка."""
    dict_scenes = [s for s in scenes if isinstance(s, dict)]
    if not dict_scenes:
        return []
    offsets = frame_offsets(all_frames, full_vo)
    span = _frame_span(chunk_frames, offsets)
    vo_norm = _norm(full_vo)
    lo, hi = span if span else (0, len(vo_norm) or 1)
    out: list[dict[str, Any]] = []
    for sc in dict_scenes:
        if _overlaps(_scene_span_tuple(sc, vo_norm), lo, hi):
            out.append(copy.deepcopy(sc))
    if out:
        return out
    return _proportional_slice(dict_scenes, chunk_frames, all_frames)


def _proportional_slice(
    items: list[dict[str, Any]],
    chunk_frames: list[Any],
    all_frames: list[Any],
) -> list[dict[str, Any]]:
    uuids = [f.uuid for f in all_frames if f.uuid]
    chunk_set = {f.uuid for f in chunk_frames if f.uuid}
    if not uuids or not items:
        return copy.deepcopy(items)
    indices = [i for i, u in enumerate(uuids) if u in chunk_set]
    if not indices:
        return []
    lo_r = min(indices) / len(uuids)
    hi_r = (max(indices) + 1) / len(uuids)
    a = int(lo_r * len(items))
    b = max(int(hi_r * len(items)), a + 1)
    return copy.deepcopy(items[a:b])


def merge_agent_slices(agent: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Склеить частичные JSON-срезы action/camera и провалидировать целиком."""
    list_key = ag.LIST_KEY[agent]
    merged: list[Any] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        items = part.get(list_key)
        if isinstance(items, list):
            merged.extend(items)
    if not merged:
        raise ag.SceneDesignAgentError(
            f"scene_design/{agent}: после склейки чанков пустой «{list_key}»"
        )
    if agent == "action":
        merged = ag.repair_chrono_dyn_year_jumps(merged)
        merged = ag.normalize_chrono_dyn_phase_budget(merged)
        for i, sc in enumerate(merged, start=1):
            if isinstance(sc, dict):
                sc["id_scene"] = f"scene_{i:02d}"
        ag.validate_chrono_dyn_action_scenes(merged)
    elif agent == "camera":
        for i, sh in enumerate(merged, start=1):
            if isinstance(sh, dict) and sh.get("id_shot") is not None:
                sh["id_shot"] = f"shot_{i:02d}"
        ag.validate_chrono_dyn_camera_shots(merged)
    logger.info(
        "scene_design/{}: merged {} chunks → {} {}",
        agent,
        len(parts),
        len(merged),
        list_key,
    )
    return {list_key: merged}
