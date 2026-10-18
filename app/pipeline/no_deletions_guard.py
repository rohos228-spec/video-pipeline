"""Точка входа сохранности для этой ветки.

Единственное место импорта. Если на ветке уже есть
``app.orchestrator.validators.preservation`` (#379), берём его.
Иначе остаётся шим с той же сигнатурой и теми же формами
``Violation`` / ``PreservationRejected``.

Возврат неактивного шота в живые без явной операции ``restore`` /
``activate`` (или ``активен: True``) — error ``inactive_shot_revived``.
Id, который был неактивен в любом кадре, остаётся неактивным и в копии
в другом кадре, пока restore не разрешил именно пару ``(frame_uuid, shot_id)``.
Пара, которая в before уже была активной, — не оживление. Два шота без id
с одним текстом считаются копиями: пропажа одной — ``frames_cleared``.
Адрес — пара ``(frame_uuid, shot_id)``, как в #379 ``c48e186b``. Без
кадра ``shot_id`` проходит только если такой id в before один.
``target`` и индекс адрес не заменяют. Сцена с закадром и без живых
шотов — error ``vo_on_inactive``; soft его не гасит, id там сцены.
Закадр при пометке не переносится. Потеря FrameText / FrameEdge
и кадра без id (сверка по тексту) — error. Несписковые ``кадры``
дают ``kadry_invalid``. ``кадры: None`` на стороне after — error
``shot_dropped`` по id живых шотов: сосед без id и шот с ``имя`` /
``name`` список не прячут, отсутствующий ключ ``закадр`` — пустой текст.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from loguru import logger

_IMPL = "shim"


def _rows_payload(rows: Any) -> Any:
    if rows is None or isinstance(rows, list):
        return [] if rows is None else rows
    return rows


def keep_soft_kadry(
    before_rows: Any,
    after_rows: Any,
    *,
    scene_id: Any,
    number: Any,
    ops: Any = None,
) -> Any:
    """``mode="soft"`` для одного списка ``кадры``. Живой пропавший кадр возвращается."""

    sid = str(scene_id or "cell")
    num = 0 if number is None else number
    run_preservation_validators(
        {"сцены": [{"id": sid, "номер": num, "кадры": _rows_payload(before_rows)}]},
        {"сцены": [{"id": sid, "номер": num, "кадры": _rows_payload(after_rows)}]},
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
        с id дописывается в ``кадры[]`` с ``активен: False`` и warn
        ``kept_inactive``. ``inactive_shot_revived`` soft не поглощает.
        ``vo_on_inactive`` с id сцены тоже: закадр сцены без живых шотов
        — отказ. Закадр остаётся на кадре. Сцена, персонаж, текст, ребро,
        кадр без id и несписковые ``кадры`` по-прежнему отказ.
        """

        if mode not in {"reject", "soft"}:
            raise ValueError(f"mode должен быть reject или soft, не {mode!r}")
        violations = _collect(before, after, ops)
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
                    for item in _collect(before, after, ops)
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

    def _collect(before: Any, after: Any, ops: Any = None) -> list[Violation]:
        found: list[Violation] = []
        found.extend(_check_scenes(before, after))
        found.extend(_check_frames(before, after))
        found.extend(_check_frames_cleared(before, after))
        found.extend(_check_revived(before, after, ops))
        found.extend(_check_vo_on_inactive(after))
        found.extend(_check_characters(before, after))
        found.extend(_check_frame_links(before, after))
        found.extend(_check_kadry_invalid(before, after))
        found.extend(_check_shot_dropped(before, after))
        return _sorted(found)

    _ACTIVATE_VERBS = {"activate", "set_active", "restore", "восстановить"}

    def _check_revived(before: Any, after: Any, ops: Any) -> list[Violation]:
        """Неактивный шот в ``after`` живой только с парой кадр и shot_id."""

        from app.services.frame_tombstones import shot_is_active

        operations = [op for op in list(ops or []) if isinstance(op, dict)]
        before_inactive = _inactive_pairs(before)
        before_active = _active_pairs(before)
        inactive_ids = {shot_id for _owner, shot_id in before_inactive}
        activated = _activated_pairs(operations, before_inactive)
        found: list[Violation] = []
        seen: set[tuple[str, str]] = set()
        for scene in _scenes(after):
            status, frames = _kadry(scene)
            if status != "list" or not isinstance(frames, list):
                continue
            entity = _scene_id(scene) or _scene_number(scene) or ""
            for shot in frames:
                if not isinstance(shot, dict) or not shot_is_active(shot):
                    continue
                fid = _frame_id(shot)
                pair = (entity, fid or "")
                # Id был неактивен в любом кадре. Копия в другом кадре — тоже revived,
                # пока restore не разрешил именно эту пару (кадр, shot_id).
                # Пара, которая в before уже была активной, — не оживление.
                if (
                    not fid
                    or fid not in inactive_ids
                    or pair in activated
                    or pair in before_active
                    or pair in seen
                ):
                    continue
                seen.add(pair)
                found.append(
                    Violation(
                        code="inactive_shot_revived",
                        severity="error",
                        path=f"scenes/{entity}/кадры/{fid}" if entity else f"кадры/{fid}",
                        id=fid,
                    )
                )
        return found

    def _check_vo_on_inactive(after: Any) -> list[Violation]:
        """Сцена с закадром и без единого живого шота — ``vo_on_inactive``."""

        from app.services.frame_tombstones import shot_is_active

        found: list[Violation] = []
        seen: set[str] = set()
        for scene in _scenes(after):
            text = " ".join(
                str(
                    scene.get("закадр")
                    or scene.get("voiceover")
                    or scene.get("vo")
                    or scene.get("vo_cell_full")
                    or ""
                ).split()
            )
            if not text:
                continue
            status, frames = _kadry(scene)
            if status != "list" or not isinstance(frames, list):
                continue
            if any(isinstance(shot, dict) and shot_is_active(shot) for shot in frames):
                continue
            entity = _scene_id(scene) or _scene_number(scene) or ""
            if entity in seen:
                continue
            seen.add(entity)
            found.append(
                Violation(
                    code="vo_on_inactive",
                    severity="error",
                    path=f"scenes/{entity}/закадр" if entity else "закадр",
                    id=entity,
                )
            )
        return found

    def _inactive_pairs(side: Any) -> set[tuple[str, str]]:
        from app.services.frame_tombstones import shot_is_active

        found: set[tuple[str, str]] = set()
        for scene in _scenes(side):
            status, frames = _kadry(scene)
            if status != "list" or not isinstance(frames, list):
                continue
            entity = _scene_id(scene) or _scene_number(scene) or ""
            for shot in frames:
                if not isinstance(shot, dict) or shot_is_active(shot):
                    continue
                fid = _frame_id(shot)
                if fid:
                    found.add((entity, fid))
        return found

    def _active_pairs(side: Any) -> set[tuple[str, str]]:
        """Уже живые шоты как (кадр, shot_id). Тот же id в другом кадре их не оживляет."""

        from app.services.frame_tombstones import shot_is_active

        found: set[tuple[str, str]] = set()
        for scene in _scenes(side):
            status, frames = _kadry(scene)
            if status != "list" or not isinstance(frames, list):
                continue
            entity = _scene_id(scene) or _scene_number(scene) or ""
            for shot in frames:
                if not isinstance(shot, dict) or not shot_is_active(shot):
                    continue
                fid = _frame_id(shot)
                if fid:
                    found.add((entity, fid))
        return found

    def _activated_pairs(
        ops: list[dict[str, Any]], before_inactive: set[tuple[str, str]]
    ) -> set[tuple[str, str]]:
        """Restore по ``(frame_uuid, shot_id)``. Без кадра — один такой id."""

        counts: dict[str, int] = {}
        for _owner, fid in before_inactive:
            counts[fid] = counts.get(fid, 0) + 1
        allowed: set[tuple[str, str]] = set()
        for op in ops:
            if not _op_activates(op):
                continue
            fid = _op_shot_id(op)
            if not fid:
                continue
            frame_ref = str(op.get("frame_uuid") or op.get("frame_id") or "")
            if frame_ref:
                allowed.add((frame_ref, fid))
                continue
            if counts.get(fid, 0) == 1:
                for owner, shot in before_inactive:
                    if shot == fid:
                        allowed.add((owner, shot))
        return allowed

    def _op_shot_id(op: dict[str, Any]) -> str:
        for key in ("shot_id", "frame_id", "id"):
            value = op.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    def _op_activates(op: dict[str, Any]) -> bool:
        for source in (op.get("fields"), op):
            if isinstance(source, dict) and "активен" in source:
                if source["активен"] is False:
                    return False
                if source["активен"] is True:
                    return True
        verb = str(op.get("op") or op.get("action") or op.get("type") or "").strip().casefold()
        return verb in _ACTIVATE_VERBS

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
                            path=f"scenes/{entity}/кадры" if entity else "scenes/кадры",
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
            path = f"scenes/{entity}/кадры" if entity else "кадры"
            lost_text = _anonymous_texts(frames) - _anonymous_texts(after_frames)
            for text in sorted(lost_text):
                key = ("frame_deleted", text)
                if key in seen:
                    continue
                seen.add(key)
                found.append(
                    Violation(
                        code="frame_deleted",
                        severity="error",
                        path=path,
                        id=text,
                    )
                )
        return found

    _IDLESS_TEXT_KEYS = ("закадр", "voiceover", "vo", "действие", "текст", "text", "описание")

    def _check_frames_cleared(before: Any, after: Any) -> list[Violation]:
        """Список ``кадры`` стал короче, и токен шота не уехал в ту же сцену.

        Токен — id, иначе текст, иначе ``parent:<id>``. Пустой id — не id.
        Шоты без id с одним текстом считаются копиями, не множеством.
        Неактивный шот может уйти. Несписковые ``кадры`` — не этот код.
        """

        if before is None or after is None:
            return []
        before_cells = [cell for cell in _collect_cells(before) if isinstance(cell.get("кадры"), list)]
        after_cells = _collect_cells(after)
        after_by_id: dict[str, dict[str, Any]] = {}
        present: dict[str, set[str]] = {}
        idless_after: dict[str, Counter[str]] = {}
        for cell in after_cells:
            cid = _cell_id(cell)
            if cid and cid not in after_by_id:
                after_by_id[cid] = cell
            scene_key = _cell_scene_key(cell)
            bucket = present.setdefault(scene_key, set())
            shots = cell.get("кадры")
            if not isinstance(shots, list):
                continue
            for shot in shots:
                token = _shot_token(shot)
                if token:
                    bucket.add(token)
                _count_idless(idless_after, scene_key, shot)
        idless_before: dict[str, Counter[str]] = {}
        for cell in before_cells:
            shots = cell.get("кадры")
            if not isinstance(shots, list):
                continue
            scene_key = _cell_scene_key(cell)
            for shot in shots:
                _count_idless(idless_before, scene_key, shot)
        found: list[Violation] = []
        seen: set[str] = set()
        reported: Counter[tuple[str, str]] = Counter()
        for cell in before_cells:
            shots = cell.get("кадры")
            if not isinstance(shots, list):
                continue
            cid = _cell_id(cell)
            after_cell = after_by_id.get(cid) if cid else None
            if after_cell is None:
                after_shots: list[Any] = []
            else:
                raw = after_cell.get("кадры")
                if not isinstance(raw, list):
                    continue
                after_shots = raw
            if len(after_shots) >= len(shots):
                continue
            scene = _cell_scene_key(cell)
            kept = present.get(scene, set())
            for shot in shots:
                if _frames_cleared_exempt(shot):
                    continue
                token = _loss_token(shot, scene, kept, seen, reported, idless_before, idless_after)
                if token is None:
                    continue
                path = f"scenes/{scene}/кадры/{token}" if scene else f"кадры/{token}"
                found.append(
                    Violation(code="frames_cleared", severity="error", path=path, id=token)
                )
        return found

    def _count_idless(buckets: dict[str, Counter[str]], scene: str, shot: Any) -> None:
        if _frames_cleared_exempt(shot) or _frame_id(shot):
            return
        token = _idless_token(shot)
        if token:
            buckets.setdefault(scene, Counter())[token] += 1

    def _loss_token(
        shot: Any,
        scene: str,
        kept: set[str],
        seen: set[str],
        reported: Counter[tuple[str, str]],
        idless_before: dict[str, Counter[str]],
        idless_after: dict[str, Counter[str]],
    ) -> str | None:
        fid = _frame_id(shot)
        if fid:
            if fid in kept or fid in seen:
                return None
            seen.add(fid)
            return fid
        token = _idless_token(shot)
        if not token:
            return None
        before_n = idless_before.get(scene, Counter()).get(token, 0)
        after_n = idless_after.get(scene, Counter()).get(token, 0)
        slot = (scene, token)
        if reported[slot] >= before_n - after_n:
            return None
        reported[slot] += 1
        return token

    def _shot_token(shot: Any) -> str | None:
        fid = _frame_id(shot)
        if fid:
            return fid
        return _idless_token(shot)

    def _idless_token(shot: Any) -> str | None:
        if _frame_id(shot) is not None or not isinstance(shot, dict):
            return None
        for key in _IDLESS_TEXT_KEYS:
            text = _plain(shot.get(key))
            if text:
                return text
        parent = shot.get("parent_id")
        if parent not in (None, ""):
            return f"parent:{parent}"
        return None

    def _frames_cleared_exempt(shot: Any) -> bool:
        if _exempt(shot):
            return True
        if not isinstance(shot, dict):
            return False
        if shot.get("archived") is True or shot.get("архив") is True:
            return True
        status = str(shot.get("status") or shot.get("статус") or "").casefold()
        return status in {"archived", "archive", "архив"}

    def _cell_id(cell: dict[str, Any]) -> str:
        fid = _frame_id(cell)
        if fid:
            return fid
        raw = cell.get("uuid")
        return "" if raw in (None, "") else str(raw)

    def _cell_scene_key(cell: dict[str, Any]) -> str:
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

    def _collect_cells(side: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        _walk_cells(side, found, 0)
        return found

    def _walk_cells(node: Any, found: list[dict[str, Any]], depth: int) -> None:
        if depth > 8:
            return
        if isinstance(node, dict):
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

    def _check_shot_dropped(before: Any, after: Any) -> list[Violation]:
        """Живой шот с id пропал из снимка — error, в том числе при ``кадры: None``."""

        if before is None or after is None:
            return []
        from app.services.frame_tombstones import shot_is_active

        after_ids = {fid for shot in _iter_shots(after) if (fid := _frame_id(shot))}
        found: list[Violation] = []
        seen: set[str] = set()
        for shot in _iter_shots(before):
            fid = _frame_id(shot)
            if not fid or fid in seen:
                continue
            seen.add(fid)
            if not shot_is_active(shot) or fid in after_ids:
                continue
            scene = shot.get("сцена") if isinstance(shot, dict) else None
            scene_key = "" if scene in (None, "") else str(scene)
            path = f"scenes/{scene_key}/кадры/{fid}" if scene_key else f"кадры/{fid}"
            found.append(
                Violation(code="shot_dropped", severity="error", path=path, id=fid)
            )
        return found

    def _iter_shots(side: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        _walk_shots(side, found, set(), 0)
        return found

    def _walk_shots(
        node: Any, found: list[dict[str, Any]], seen: set[int], depth: int
    ) -> None:
        if depth > 8 or isinstance(node, (str, int, float, bool)) or node is None:
            return
        if isinstance(node, list):
            marker = id(node)
            if marker in seen:
                return
            seen.add(marker)
            for item in node:
                if _is_shot(item):
                    found.append(item)
                else:
                    _walk_shots(item, found, seen, depth + 1)
            return
        if isinstance(node, dict):
            for value in node.values():
                _walk_shots(value, found, seen, depth + 1)

    def _is_shot(item: Any) -> bool:
        """Шот с id. Нет ключа «закадр» — пустой закадр, как ``""``.

        Ключи шота раньше ``имя`` и op: реальный шот с именем остаётся видимым.
        ``описание`` тоже ключ шота: ``{id, имя, описание}`` не теряется.
        Сосед без id не прячет список. Обёртка ``{id, attrs}`` и обёртка
        с ``attrs.кадры`` / ``shots`` / ``cells`` / ``ячейки`` сами не шоты.
        """

        if not isinstance(item, dict):
            return False
        if any(key in item for key in ("кадры", "cells", "ячейки", "shots")):
            return False
        has_shot_key = any(
            key in item
            for key in ("место", "действие", "сцена", "закадр", "активен", "план", "описание")
        )
        attrs = item.get("attrs")
        if isinstance(attrs, dict) and any(
            key in attrs for key in ("кадры", "shots", "cells", "ячейки")
        ):
            return False
        if "attrs" in item and not has_shot_key:
            return False
        if _frame_id(item) is None:
            return False
        if has_shot_key:
            return True
        if any(key in item for key in ("op", "action", "type")):
            return False
        if any(key in item for key in ("имя", "name", "внешность", "code", "код")):
            return False
        if any(
            key in item
            for key in ("номер", "number", "kind", "text", "текст", "from_frame_id", "to_frame_id")
        ):
            return False
        return True

    def _check_kadry_invalid(before: Any, after: Any) -> list[Violation]:
        """Несписковые ``кадры`` — error ``kadry_invalid``, не warn."""

        found: list[Violation] = []
        seen: set[tuple[str, str, str]] = set()
        for side in (before, after):
            for scene in _scenes(side):
                if "кадры" not in scene:
                    continue
                value = scene.get("кадры")
                if isinstance(value, list):
                    continue
                entity = _scene_id(scene) or _scene_number(scene) or ""
                path = f"scenes/{entity}/кадры" if entity else "кадры"
                key = ("kadry_invalid", path, entity)
                if key in seen:
                    continue
                seen.add(key)
                found.append(
                    Violation(
                        code="kadry_invalid",
                        severity="error",
                        path=path,
                        id=entity,
                    )
                )
        return found

    def _anonymous_texts(frames: list[Any]) -> set[str]:
        """Текст без id, который исчез целиком.

        Число копий считает ``_check_frames_cleared`` через Counter.
        Пока хотя бы одна копия текста на месте, это не ``frame_deleted``:
        правка одной копии и выдача ей id список не укорачивают.
        """

        found: set[str] = set()
        for frame in frames:
            if _frame_id(frame) is not None or _exempt(frame):
                continue
            found.add(_anonymous_text(frame))
        return found

    def _anonymous_text(frame: Any) -> str:
        if isinstance(frame, dict):
            # Нет ключа «закадр» — пусто, как "".
            for key in ("закадр", "voiceover", "vo", "text", "текст", "действие"):
                text = _plain(frame.get(key))
                if text:
                    return text
            return ""
        return _plain(frame)

    def _plain(value: Any) -> str:
        return " ".join(str(value or "").split())

    def _check_frame_links(before: Any, after: Any) -> list[Violation]:
        """Потеря FrameText / FrameEdge — error. ``voiceover`` → ``vo_archived`` остаётся."""

        if not isinstance(before, dict) or not isinstance(after, dict):
            return []
        return _sorted(_missing_texts(before, after) + _missing_edges(before, after))

    def _link_rows(side: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
        for key in keys:
            value = side.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _link_id(row: dict[str, Any]) -> str:
        raw = row.get("id")
        if raw in (None, ""):
            return ""
        return str(raw)

    def _missing_texts(before: dict[str, Any], after: dict[str, Any]) -> list[Violation]:
        before_rows = _link_rows(before, ("frame_texts", "texts"))
        after_rows = _link_rows(after, ("frame_texts", "texts"))
        if not before_rows:
            return []
        after_ids = {rid for row in after_rows if (rid := _link_id(row))}
        found: list[Violation] = []
        for row in before_rows:
            rid = _link_id(row)
            if rid and rid in after_ids:
                continue
            if _voiceover_archived(row, after_rows):
                continue
            if not rid and _same_text_row(row, after_rows):
                continue
            token = rid or _text_token(row)
            found.append(
                Violation(
                    code="frame_text_deleted",
                    severity="error",
                    path=f"frame_texts/{token}",
                    id=rid,
                )
            )
        return found

    def _missing_edges(before: dict[str, Any], after: dict[str, Any]) -> list[Violation]:
        before_rows = _link_rows(before, ("frame_edges", "edges"))
        after_rows = _link_rows(after, ("frame_edges", "edges"))
        if not before_rows:
            return []
        after_ids = {rid for row in after_rows if (rid := _link_id(row))}
        after_keys = {_edge_key(row) for row in after_rows}
        found: list[Violation] = []
        for row in before_rows:
            rid = _link_id(row)
            if rid and rid in after_ids:
                continue
            if not rid and _edge_key(row) in after_keys:
                continue
            token = rid or _edge_token(row)
            found.append(
                Violation(
                    code="frame_edge_deleted",
                    severity="error",
                    path=f"frame_edges/{token}",
                    id=rid,
                )
            )
        return found

    def _voiceover_archived(row: dict[str, Any], after_rows: list[dict[str, Any]]) -> bool:
        if str(row.get("kind") or "") != "voiceover":
            return False
        text = _plain(row.get("text"))
        frame = str(row.get("frame_id") or "")
        for item in after_rows:
            if str(item.get("kind") or "") != "vo_archived":
                continue
            if _plain(item.get("text")) == text and str(item.get("frame_id") or "") == frame:
                return True
        return False

    def _text_token(row: dict[str, Any]) -> str:
        return "|".join(
            (
                str(row.get("frame_id") or ""),
                str(row.get("kind") or ""),
                _plain(row.get("text")),
            )
        )

    def _same_text_row(row: dict[str, Any], after_rows: list[dict[str, Any]]) -> bool:
        token = _text_token(row)
        archived = ""
        if str(row.get("kind") or "") == "voiceover":
            archived = _text_token({**row, "kind": "vo_archived"})
        return any(
            _text_token(item) == token or (archived and _text_token(item) == archived)
            for item in after_rows
        )

    def _edge_key(row: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(row.get("from_frame_id") if row.get("from_frame_id") is not None else row.get("from") or ""),
            str(row.get("to_frame_id") if row.get("to_frame_id") is not None else row.get("to") or ""),
            str(row.get("type") or row.get("kind") or "next"),
        )

    def _edge_token(row: dict[str, Any]) -> str:
        left, right, kind = _edge_key(row)
        return f"{left}>{right}:{kind}"

    def _sorted(items: list[Violation]) -> list[Violation]:
        return sorted(items, key=lambda item: (item.code, item.path, item.id, item.severity))
