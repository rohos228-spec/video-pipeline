"""Склейка кадры[] при восстановлении шота.

Не удаляет чужие шоты: в списке остаются кадры, которых не было в снимке.
"""

from __future__ import annotations

from typing import Any


def _shot_key(shot: dict, index: int) -> str:
    sid = str(shot.get("id") or "").strip()
    return sid or f"#{index}"


def merge_kadry_keep_missing(current: list | None, incoming: list | None) -> list:
    """Вернуть шоты снимка в кадры[] и пометить их активен=True.

    Шоты, которые есть только в текущем списке, остаются на месте.
    """

    kept: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for i, shot in enumerate(current or []):
        if not isinstance(shot, dict):
            continue
        row = dict(shot)
        key = _shot_key(row, i)
        index[key] = len(kept)
        kept.append(row)
    for j, shot in enumerate(incoming or []):
        if not isinstance(shot, dict):
            continue
        row = dict(shot)
        row["активен"] = True
        key = _shot_key(row, j)
        if key in index:
            merged = dict(kept[index[key]])
            merged.update(row)
            merged["активен"] = True
            kept[index[key]] = merged
        else:
            kept.append(row)
    return kept
