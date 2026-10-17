"""Точка входа сохранности для этой ветки.

Единственное место импорта. Если на ветке уже есть
``app.orchestrator.validators.preservation`` (#379), берём его.
Иначе остаётся шим с той же сигнатурой и теми же формами
``Violation`` / ``PreservationRejected``.

Шим не повторяет три ложных отказа #379 @ ``40da86ed``: закадр на
неактивном шоте, смена номера при том же id, возврат неактивного в
активные без отдельной операции. Закадр при пометке не переносится.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from loguru import logger

_IMPL = "shim"


def keep_soft_kadry(
    before_rows: Any,
    after_rows: Any,
    *,
    scene_id: Any,
    number: Any,
    ops: Any = None,
) -> Any:
    """``mode="soft"`` для одного списка ``кадры``. Живой пропавший кадр возвращается."""

    if not isinstance(after_rows, list):
        return after_rows
    sid = str(scene_id or "cell")
    num = 0 if number is None else number
    before_list = before_rows if isinstance(before_rows, list) else []
    run_preservation_validators(
        {"сцены": [{"id": sid, "номер": num, "кадры": before_list}]},
        {"сцены": [{"id": sid, "номер": num, "кадры": after_rows}]},
        ops,
        mode="soft",
    )
    return after_rows


def _load_real() -> tuple[Any, Any, Any] | None:
    try:
        from app.orchestrator.validators.preservation import (
            PreservationRejected as _Rejected,
            Violation as _Violation,
            run_preservation_validators as _run,
        )
    except ImportError:
        return None
    return _run, _Violation, _Rejected


_REAL = _load_real()


if _REAL is not None:
    run_preservation_validators, Violation, PreservationRejected = _REAL
    PRESERVATION_IMPL = "ws1"
else:
    PRESERVATION_IMPL = _IMPL

    @dataclass(frozen=True)
    class Violation:
        code: str
        severity: str
        path: str
        id: str

        def as_dict(self) -> dict[str, str]:
            return asdict(self)

    class PreservationRejected(Exception):
        """Отказ записи: среди нарушений есть error.

        ``violations`` — полный список, включая warn. Вызывающий не коммитит.
        """

        def __init__(self, violations: list[Violation]) -> None:
            self.violations = list(violations)
            text = ", ".join(
                f"{item.code}:{item.id or item.path}"
                for item in self.violations
                if item.severity == "error"
            )
            super().__init__(f"сохранность: {text}")

    def run_preservation_validators(
        before: Any,
        after: Any,
        ops: Any = None,
        *,
        mode: str = "reject",
    ) -> list[Violation]:
        """Прогон сохранности перед записью.

        ``mode="reject"`` — любой error поднимает ``PreservationRejected``,
        ``after`` не меняется. ``mode="soft"`` — пропавший живой кадр
        дописывается в ``кадры[]`` с ``активен: False`` и warn
        ``kept_inactive``. Закадр остаётся на кадре. Сцена и персонаж
        по-прежнему отказ.
        """

        if mode not in {"reject", "soft"}:
            raise ValueError(f"mode должен быть reject или soft, не {mode!r}")
        violations = _collect(before, after)
        if mode == "soft":
            restored = _keep_missing_shots(before, after)
            if restored:
                parked = {fid for _, fid in restored}
                absorbed = {
                    "frame_deleted",
                    "shot_dropped",
                    "vo_on_inactive",
                    "inactive_shot_used",
                }
                violations = [
                    item
                    for item in _collect(before, after)
                    if not (item.id in parked and item.code in absorbed)
                ]
                violations.extend(
                    Violation(code="kept_inactive", severity="warn", path=path, id=fid)
                    for path, fid in restored
                )
                violations = _sorted(violations)
                logger.warning(
                    "WARN kept_inactive: {}",
                    ", ".join(fid for _, fid in restored),
                )
        if any(item.severity == "error" for item in violations):
            raise PreservationRejected(violations)
        return violations

    def _collect(before: Any, after: Any) -> list[Violation]:
        found: list[Violation] = []
        found.extend(_check_scenes(before, after))
        found.extend(_check_frames(before, after))
        found.extend(_check_characters(before, after))
        return _sorted(found)

    def _keep_missing_shots(before: Any, after: Any) -> list[tuple[str, str]]:
        if not isinstance(after, dict):
            return []
        after_by_id, after_by_number = _index_scenes(_scenes(after))
        restored: list[tuple[str, str]] = []
        for scene in _scenes(before):
            status, frames = _kadry(scene)
            if status != "list":
                continue
            sid = _scene_id(scene)
            number = _scene_number(scene)
            match = _match_scene(sid, number, after_by_id, after_by_number)
            if match is None:
                continue
            after_status, after_frames = _kadry(match)
            if after_status != "list" or not isinstance(after_frames, list):
                continue
            have = {fid for item in after_frames if (fid := _frame_id(item))}
            entity = sid or number or ""
            for frame in frames:
                if _exempt(frame):
                    continue
                fid = _frame_id(frame)
                if not fid or fid in have:
                    continue
                kept = dict(frame) if isinstance(frame, dict) else {"id": fid}
                kept["активен"] = False
                after_frames.append(kept)
                have.add(fid)
                path = f"scenes/{entity}/кадры/{fid}" if entity else f"кадры/{fid}"
                restored.append((path, fid))
        return restored

    def _check_scenes(before: Any, after: Any) -> list[Violation]:
        """Пропавший id сцены — error. Смена номера при том же id — нет."""

        if not isinstance(before, dict) or not isinstance(after, dict):
            return []
        after_ids = {_scene_id(scene) for scene in _scenes(after)}
        after_ids.discard(None)
        found: list[Violation] = []
        seen: set[str] = set()
        for scene in _scenes(before):
            sid = _scene_id(scene)
            number = _scene_number(scene)
            if sid:
                if sid in after_ids or sid in seen:
                    continue
                seen.add(sid)
                found.append(
                    Violation(
                        code="scene_deleted",
                        severity="error",
                        path=f"scenes/{sid}",
                        id=sid,
                    )
                )
                continue
            if number is None:
                continue
            if any(_scene_number(item) == number for item in _scenes(after)):
                continue
            if number in seen:
                continue
            seen.add(number)
            found.append(
                Violation(
                    code="scene_deleted",
                    severity="error",
                    path=f"scenes/{number}",
                    id=number,
                )
            )
        return found

    def _check_frames(before: Any, after: Any) -> list[Violation]:
        if not isinstance(before, dict) or not isinstance(after, dict):
            return []
        after_by_id, after_by_number = _index_scenes(_scenes(after))
        found: list[Violation] = []
        seen: set[tuple[str, str]] = set()
        for scene in _scenes(before):
            sid = _scene_id(scene)
            number = _scene_number(scene)
            entity = sid or number or ""
            status, frames = _kadry(scene)
            match = _match_scene(sid, number, after_by_id, after_by_number)
            after_status, after_frames = _kadry(match) if match is not None else ("absent", None)
            if status == "nodata" or after_status == "nodata":
                key = ("frames_no_data", entity)
                if key not in seen:
                    seen.add(key)
                    found.append(
                        Violation(
                            code="frames_no_data",
                            severity="warn",
                            path=f"scenes/{entity}/кадры" if entity else "кадры",
                            id=entity,
                        )
                    )
                continue
            if status != "list":
                continue
            if not isinstance(after_frames, list):
                after_frames = []
            kept = {fid for item in after_frames if (fid := _frame_id(item))}
            for frame in frames:
                if _exempt(frame):
                    continue
                fid = _frame_id(frame)
                if not fid or fid in kept:
                    continue
                key = ("frame_deleted", fid)
                if key in seen:
                    continue
                seen.add(key)
                found.append(
                    Violation(
                        code="frame_deleted",
                        severity="error",
                        path=f"scenes/{entity}/кадры/{fid}" if entity else f"кадры/{fid}",
                        id=fid,
                    )
                )
        return found

    def _check_characters(before: Any, after: Any) -> list[Violation]:
        if not isinstance(before, dict) or not isinstance(after, dict):
            return []
        after_ids = {cid for item in _characters(after) if (cid := _character_id(item))}
        found: list[Violation] = []
        seen: set[str] = set()
        for character in _characters(before):
            cid = _character_id(character)
            if not cid or cid in after_ids or cid in seen:
                continue
            seen.add(cid)
            found.append(
                Violation(
                    code="character_deleted",
                    severity="error",
                    path=f"characters/{cid}",
                    id=cid,
                )
            )
        return found

    def _scenes(side: Any) -> list[dict[str, Any]]:
        if not isinstance(side, dict):
            return []
        found: list[dict[str, Any]] = []
        for key in ("scenes", "сцены"):
            value = side.get(key)
            if isinstance(value, list):
                found.extend(item for item in value if isinstance(item, dict))
        return found

    def _characters(side: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for key in ("characters", "персонажи"):
            value = side.get(key)
            if isinstance(value, list):
                found.extend(item for item in value if isinstance(item, dict))
        for scene in _scenes(side):
            for key in ("characters", "персонажи"):
                value = scene.get(key)
                if isinstance(value, list):
                    found.extend(item for item in value if isinstance(item, dict))
        return found

    def _kadry(scene: dict[str, Any] | None) -> tuple[str, Any]:
        if not isinstance(scene, dict) or "кадры" not in scene:
            return "absent", None
        value = scene.get("кадры")
        if isinstance(value, list):
            return "list", value
        return "nodata", value

    def _index_scenes(
        scenes: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        by_id: dict[str, dict[str, Any]] = {}
        by_number: dict[str, dict[str, Any]] = {}
        for scene in scenes:
            sid = _scene_id(scene)
            number = _scene_number(scene)
            if sid and sid not in by_id:
                by_id[sid] = scene
            if number and number not in by_number:
                by_number[number] = scene
        return by_id, by_number

    def _match_scene(
        sid: str | None,
        number: str | None,
        by_id: dict[str, dict[str, Any]],
        by_number: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        if sid and sid in by_id:
            return by_id[sid]
        if number and number in by_number:
            return by_number[number]
        return None

    def _scene_id(scene: dict[str, Any]) -> str | None:
        return _first(scene, ("id", "scene_id", "uuid"))

    def _scene_number(scene: dict[str, Any]) -> str | None:
        return _first(scene, ("номер", "number"))

    def _frame_id(frame: Any) -> str | None:
        if isinstance(frame, dict):
            return _first(frame, ("id", "uuid", "frame_id", "shot_id"))
        if isinstance(frame, (str, int)) and not isinstance(frame, bool):
            return str(frame)
        return None

    def _character_id(character: dict[str, Any]) -> str | None:
        return _first(character, ("id", "character_id", "код", "code"))

    def _first(entity: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = entity.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    def _exempt(frame: Any) -> bool:
        if not isinstance(frame, dict):
            return False
        from app.services.frame_tombstones import shot_is_active

        return not shot_is_active(frame)

    def _sorted(items: list[Violation]) -> list[Violation]:
        return sorted(items, key=lambda item: (item.code, item.path, item.id, item.severity))
