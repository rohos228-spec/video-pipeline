"""Чтение снимка: сцены, кадры, персонажи, шоты. Ничего не удаляет и не пишет."""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.grammar import shot_counts_as_active

_SCENE_KEYS = ("scenes", "сцены", "scenes_archived", "сцены_архив", "archived_scenes")
_CHAR_KEYS = ("characters", "персонажи", "cards", "characters_archived", "персонажи_архив")
_ARCHIVE_KEYS = ("archive", "архив", "archived")
_FRAME_ARCHIVE_KEYS = ("кадры_архив", "frames_archived", "archived_frames", "shots_archived")


def scene_id(scene: dict[str, Any]) -> str | None:
    return _first(scene, ("id", "scene_id", "uuid"))


def scene_number(scene: dict[str, Any]) -> str | None:
    return _first(scene, ("номер", "number"))


def frame_id(frame: Any) -> str | None:
    if isinstance(frame, dict):
        return _first(frame, ("id", "uuid", "frame_id", "shot_id"))
    if isinstance(frame, (str, int)):
        return str(frame)
    return None


def character_id(character: dict[str, Any]) -> str | None:
    return _first(character, ("id", "character_id", "код", "code"))


def is_archived(entity: Any) -> bool:
    if not isinstance(entity, dict):
        return False
    if entity.get("archived") is True or entity.get("архив") is True:
        return True
    status = str(entity.get("status") or entity.get("статус") or "").casefold()
    if status in {"archived", "archive", "архив"}:
        return True
    attrs = entity.get("attrs")
    if isinstance(attrs, dict) and attrs is not entity:
        return is_archived(attrs)
    return False


def frame_is_exempt(frame: Any) -> bool:
    """Неактивный или архивный кадр не считается удалённым."""

    if not isinstance(frame, dict):
        return False
    if not shot_counts_as_active(frame):
        return True
    return is_archived(frame)


def text_of(value: Any) -> str:
    return " ".join(str(value or "").split())


def collect_scenes(side: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    _walk_scenes(side, found, depth=0)
    return found


def collect_characters(side: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    _walk_characters(side, found, depth=0)
    return found


def scene_kadry(scene: dict[str, Any]) -> tuple[str, Any]:
    """``list`` / ``nodata`` / ``absent`` для ключа ``кадры``."""

    if "кадры" not in scene:
        return "absent", None
    value = scene.get("кадры")
    if isinstance(value, list):
        return "list", value
    return "nodata", value


def archived_frame_ids(side: Any) -> set[str]:
    found: set[str] = set()
    _walk_archived_frames(side, found, depth=0, in_archive=False)
    return found


def collect_cells(side: Any) -> list[dict[str, Any]]:
    """Ячейки и вложенные ``attrs.кадры``. Новые словари только как вид, списки те же."""

    found: list[dict[str, Any]] = []
    _walk_cells(side, found, depth=0)
    return found


def iter_shots(side: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[int] = set()
    _walk_shots(side, found, seen, depth=0)
    return found


def _first(entity: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = entity.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _walk_scenes(node: Any, found: list[dict[str, Any]], depth: int) -> None:
    if depth > 6:
        return
    if isinstance(node, dict):
        for key in _SCENE_KEYS:
            _take_scenes(node.get(key), found)
        for key in _ARCHIVE_KEYS:
            if key in node:
                _walk_scenes(node[key], found, depth + 1)
        for key in ("scene", "сцена"):
            value = node.get(key)
            if isinstance(value, dict):
                found.append(value)
        return
    if isinstance(node, list):
        if any(_is_op(item) for item in node if isinstance(item, dict)):
            for item in node:
                if isinstance(item, dict):
                    _walk_scenes(item, found, depth + 1)
            return
        _take_scenes(node, found)


def _take_scenes(value: Any, found: list[dict[str, Any]]) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, dict):
            found.append(item)


def _walk_characters(node: Any, found: list[dict[str, Any]], depth: int) -> None:
    if depth > 6:
        return
    if isinstance(node, dict):
        for key in _CHAR_KEYS:
            value = node.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        found.append(item)
        for key in _ARCHIVE_KEYS:
            if key in node:
                _walk_characters(node[key], found, depth + 1)
        for key in ("character", "персонаж"):
            value = node.get(key)
            if isinstance(value, dict):
                found.append(value)
        return
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and (_is_op(item) or any(key in item for key in _CHAR_KEYS)):
                _walk_characters(item, found, depth + 1)


def _walk_archived_frames(node: Any, found: set[str], depth: int, in_archive: bool) -> None:
    if depth > 6:
        return
    if isinstance(node, dict):
        for key in _FRAME_ARCHIVE_KEYS:
            _take_frame_ids(node.get(key), found)
        if in_archive:
            _take_frame_ids(node.get("кадры"), found)
        for key in _ARCHIVE_KEYS:
            if key in node:
                _walk_archived_frames(node[key], found, depth + 1, True)
        for key in _SCENE_KEYS:
            value = node.get(key)
            if isinstance(value, list):
                for item in value:
                    _walk_archived_frames(item, found, depth + 1, in_archive)
        return
    if isinstance(node, list):
        for item in node:
            _walk_archived_frames(item, found, depth + 1, in_archive)


def _take_frame_ids(value: Any, found: set[str]) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        fid = frame_id(item)
        if fid:
            found.add(fid)


def _walk_cells(node: Any, found: list[dict[str, Any]], depth: int) -> None:
    if depth > 8:
        return
    if isinstance(node, dict):
        if "кадры" in node or "cells" in node or "ячейки" in node:
            if "кадры" in node:
                found.append(node)
        attrs = node.get("attrs")
        if isinstance(attrs, dict) and "кадры" in attrs:
            sub = node.get("camera_subdivide")
            parent = None
            if isinstance(sub, dict):
                parent = sub.get("parent_uuid")
            found.append(
                {
                    "id": node.get("id") or node.get("uuid"),
                    "сцена": node.get("сцена", node.get("scene")),
                    "parent_id": node.get("parent_id") or parent,
                    "кадры": attrs.get("кадры"),
                }
            )
        for key in ("cells", "ячейки", "frames", "scenes", "сцены", "archive", "архив"):
            if key in node:
                _walk_cells(node[key], found, depth + 1)
        for key in ("scene", "сцена"):
            value = node.get(key)
            if isinstance(value, dict):
                _walk_cells(value, found, depth + 1)
        return
    if isinstance(node, list):
        for item in node:
            _walk_cells(item, found, depth + 1)


def _walk_shots(node: Any, found: list[dict[str, Any]], seen: set[int], depth: int) -> None:
    if depth > 8 or isinstance(node, (str, int, float, bool)) or node is None:
        return
    if isinstance(node, list):
        marker = id(node)
        if marker in seen:
            return
        seen.add(marker)
        if node and all(_is_shot(item) for item in node):
            for item in node:
                if isinstance(item, dict):
                    found.append(item)
            return
        for item in node:
            _walk_shots(item, found, seen, depth + 1)
        return
    if isinstance(node, dict):
        for value in node.values():
            _walk_shots(value, found, seen, depth + 1)


def _is_shot(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if "кадры" in item or "cells" in item or "ячейки" in item:
        return False
    if frame_id(item) is None:
        return False
    return any(key in item for key in ("место", "действие", "сцена", "закадр", "активен", "план"))


def _is_op(item: dict[str, Any]) -> bool:
    return any(key in item for key in ("op", "action", "type"))


def cell_scene_key(cell: dict[str, Any]) -> str:
    for key in ("сцена", "scene", "scene_id"):
        value = cell.get(key)
        if value not in (None, ""):
            return str(value)
    shots = cell.get("кадры")
    if isinstance(shots, list):
        numbers = {
            str(shot.get("сцена"))
            for shot in shots
            if isinstance(shot, dict) and shot.get("сцена") not in (None, "")
        }
        if len(numbers) == 1:
            return next(iter(numbers))
    return str(cell.get("id") or cell.get("uuid") or "")
