"""Неактивный шот не участвует в QC и рендере.

Закадр на неактивном шоте остаётся на месте: в ``закадр_архив`` его не
переносим и сам текст не трогаем. Потребители отфильтровывают такие шоты
через ``shot_is_active`` / ``shots_of``. Это не ``vo_on_inactive``.

Кто живой, решает ``shots_of`` (#371). Этот модуль только читает.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.orchestrator.validators.entities import (
    collect_scenes,
    frame_id,
    scene_id,
    scene_number,
    text_of,
)
from app.orchestrator.validators.grammar import is_kadry_invalid, shot_counts_as_active, shots_of
from app.orchestrator.validators.violations import Violation, sorted_violations

_QC_VERBS = {"qc", "render", "рендер", "qc_shot", "render_shot"}
_ACTIVATE_VERBS = {"activate", "set_active", "restore", "восстановить"}
_DEACTIVATE_VERBS = {"deactivate", "set_inactive"}


def check_kadry_invalid(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    """Несписковое ``кадры`` — warn, а не тихий пустой список."""

    del ops
    violations: list[Violation] = []
    seen: set[tuple[str, str, str]] = set()
    for side in (before, after):
        for scene in collect_scenes(side):
            if "кадры" not in scene:
                continue
            if not is_kadry_invalid(shots_of({"shots": scene.get("кадры")})):
                continue
            entity = scene_id(scene) or scene_number(scene) or ""
            _add_warn(
                violations,
                seen,
                "kadry_invalid",
                f"scenes/{entity}/кадры" if entity else "кадры",
                entity,
            )
    return sorted_violations(violations)


def check_active_shots(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    """Проверки живых шотов на снимке ``after`` и на ops.

    Неактивный шот сам не становится живым. Явный op ``restore`` /
    ``activate`` или флаг ``активен: True`` (в ``fields`` или на op)
    разрешает возврат. Без такого op код ``inactive_shot_revived``.
    """

    operations = [op for op in list(ops or []) if isinstance(op, dict)]
    violations: list[Violation] = []
    seen: set[tuple[str, str, str]] = set()
    before_scenes = _scene_nodes(before)
    before_inactive = _inactive_pairs(before_scenes)
    before_active = _active_pairs(before_scenes)
    inactive_ids = {shot_id for _owner, shot_id in before_inactive}
    before_by_id, before_by_number = _index_scenes(before_scenes)
    activated = _activated_pairs(operations, before_inactive)
    deactivated = [fid for op in operations if (fid := _targeted(op, False))]

    for scene in _scene_nodes(after):
        raw = scene.get("кадры")
        if not isinstance(raw, list):
            continue
        active = list(shots_of({"shots": raw}) or [])
        active_mark = {id(item) for item in active}
        entity = scene_id(scene) or scene_number(scene) or ""
        for shot in raw:
            if not isinstance(shot, dict) or id(shot) in active_mark:
                continue
            fid = frame_id(shot) or ""
            path = _shot_path(entity, fid)
            if _marked_qc(shot) or _ops_target(operations, fid):
                _add(violations, seen, "inactive_shot_used", path, fid)
        for shot in raw:
            if not isinstance(shot, dict) or id(shot) not in active_mark:
                continue
            fid = frame_id(shot) or ""
            # Id был неактивен в любом кадре. Копия в другом кадре — тоже revived,
            # пока restore не разрешил именно эту пару (кадр, shot_id).
            # Пара, которая в before уже была активной, — не оживление.
            if (
                fid
                and fid in inactive_ids
                and (entity, fid) not in activated
                and (entity, fid) not in before_active
            ):
                _add(
                    violations,
                    seen,
                    "inactive_shot_revived",
                    _shot_path(entity, fid),
                    fid,
                )
        scene_text = _scene_text(scene)
        # Покрытие, не равенство склейки: живые фрагменты обязательны и
        # собираются в любом порядке, неактивные закрывают пропуски без
        # порядка, их повторы можно не использовать. То же расхождение,
        # что уже было на before, не блокирует.
        if scene_text and not _scene_voiceover_covered(scene):
            prior = _prior_scene(scene, before_by_id, before_by_number)
            if prior is None or _scene_voiceover_covered(prior) or _vo_layout(prior) != _vo_layout(scene):
                _add(
                    violations,
                    seen,
                    "vo_on_inactive",
                    f"scenes/{entity}/закадр" if entity else "закадр",
                    entity,
                )
        if deactivated and scene_text and not active:
            ids = {frame_id(shot) for shot in raw if isinstance(shot, dict)}
            for fid in deactivated:
                if fid in ids:
                    _add(
                        violations,
                        seen,
                        "last_active_deactivated",
                        _shot_path(entity, fid),
                        fid,
                    )
    return sorted_violations(violations)


def _scene_nodes(side: Any) -> list[dict[str, Any]]:
    if side is None:
        return []
    scenes = [scene for scene in collect_scenes(side) if isinstance(scene.get("кадры"), list)]
    if scenes:
        return scenes
    if isinstance(side, dict):
        for key in ("кадры", "shots", "frames"):
            if isinstance(side.get(key), list):
                return [
                    {
                        "id": side.get("id") or "root",
                        "номер": side.get("номер"),
                        "закадр": side.get("закадр") or side.get("voiceover") or "",
                        "кадры": side[key],
                    }
                ]
    return []


def _inactive_pairs(scenes: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Неактивные шоты как (кадр, shot_id). Один id в разных кадрах — разные ключи."""

    found: set[tuple[str, str]] = set()
    for scene in scenes:
        raw = scene.get("кадры")
        if not isinstance(raw, list):
            continue
        entity = scene_id(scene) or scene_number(scene) or ""
        for shot in raw:
            if not isinstance(shot, dict) or shot_counts_as_active(shot):
                continue
            fid = frame_id(shot)
            if fid:
                found.add((entity, fid))
    return found


def _active_pairs(scenes: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Уже живые шоты как (кадр, shot_id). Тот же id в другом кадре их не оживляет."""

    found: set[tuple[str, str]] = set()
    for scene in scenes:
        raw = scene.get("кадры")
        if not isinstance(raw, list):
            continue
        entity = scene_id(scene) or scene_number(scene) or ""
        for shot in raw:
            if not isinstance(shot, dict) or not shot_counts_as_active(shot):
                continue
            fid = frame_id(shot)
            if fid:
                found.add((entity, fid))
    return found


def _activated_pairs(
    ops: list[dict[str, Any]], before_inactive: set[tuple[str, str]]
) -> set[tuple[str, str]]:
    """Restore разрешён парой (frame_uuid, shot_id).

    Без кадра — только если такой shot_id в before ровно один. Иначе копия
    в другом кадре тоже стала бы живой.
    """

    counts: dict[str, int] = {}
    for _owner, fid in before_inactive:
        counts[fid] = counts.get(fid, 0) + 1
    allowed: set[tuple[str, str]] = set()
    for op in ops:
        if _flag(op) is not True:
            continue
        fid = _op_id(op)
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


def _scene_text(scene: dict[str, Any]) -> str:
    return text_of(
        scene.get("закадр") or scene.get("voiceover") or scene.get("vo") or scene.get("vo_cell_full")
    )


def _shot_text(shot: Any) -> str:
    if not isinstance(shot, dict):
        return ""
    return text_of(shot.get("закадр") or shot.get("voiceover") or shot.get("vo"))


def _index_scenes(
    scenes: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_number: dict[str, dict[str, Any]] = {}
    for scene in scenes:
        sid = scene_id(scene) or ""
        number = scene_number(scene) or ""
        if sid and sid not in by_id:
            by_id[sid] = scene
        if number and number not in by_number:
            by_number[number] = scene
    return by_id, by_number


def _prior_scene(
    scene: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_number: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    sid = scene_id(scene) or ""
    if sid and sid in by_id:
        return by_id[sid]
    number = scene_number(scene) or ""
    if number and number in by_number:
        return by_number[number]
    return None


def _shot_voice_parts(scene: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Непустые закадры живых шотов и неактивных, в порядке списка."""

    raw = scene.get("кадры")
    if not isinstance(raw, list):
        return [], []
    active = list(shots_of({"shots": raw}) or [])
    active_mark = {id(item) for item in active}
    live: list[str] = []
    parked: list[str] = []
    for shot in raw:
        if not isinstance(shot, dict):
            continue
        text = _shot_text(shot)
        if not text:
            continue
        if id(shot) in active_mark:
            live.append(text)
        else:
            parked.append(text)
    return live, parked


def _vo_layout(scene: dict[str, Any]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    live, parked = _shot_voice_parts(scene)
    return (_scene_text(scene), tuple(sorted(live)), tuple(sorted(parked)))


def _scene_voiceover_covered(scene: dict[str, Any]) -> bool:
    """Закадр сцены собирается из живых фрагментов и любых неактивных.

    Живые фрагменты используются все. Неактивные — только те, что нужны,
    чтобы закрыть пропуски: повтор текста с неактивного шота не обязателен.
    Порядок шотов не важен.
    """

    scene_text = _scene_text(scene)
    if not scene_text:
        return True
    live, parked = _shot_voice_parts(scene)
    # Закадр только на неактивных шотах — это потеря, даже если текст сходится.
    if not live:
        return False
    return _parts_cover(scene_text, live, parked)


def _parts_cover(scene_text: str, required: list[str], optional: list[str]) -> bool:
    scene = tuple(scene_text.split())
    req = [tuple(part.split()) for part in required if part.split()]
    opt = [tuple(part.split()) for part in optional if part.split()]
    if not req and not opt:
        return False
    if len(req) + len(opt) > 18:
        return _bag_cover(scene, req, opt)
    return _assemble(scene, req, opt)


def _bag_cover(
    scene: tuple[str, ...],
    required: list[tuple[str, ...]],
    optional: list[tuple[str, ...]],
) -> bool:
    """Запасной путь для длинных списков: покрытие по словам, без склейки фраз."""

    have = Counter(scene)
    need: Counter[str] = Counter()
    for piece in required:
        need.update(piece)
    if any(need[token] > have[token] for token in need):
        return False
    left = have - need
    if not left:
        return True
    pieces = optional
    mask_limit = 1 << len(pieces)
    if len(pieces) > 18:
        spare: Counter[str] = Counter()
        for piece in pieces:
            spare.update(piece)
        return all(left[token] <= spare[token] for token in left)
    for mask in range(mask_limit):
        got: Counter[str] = Counter()
        for index, piece in enumerate(pieces):
            if mask & (1 << index):
                got.update(piece)
        if got == left:
            return True
    return False


def _assemble(
    scene: tuple[str, ...],
    required: list[tuple[str, ...]],
    optional: list[tuple[str, ...]],
) -> bool:
    """Собрать закадр из всех живых фраз и подмножества неактивных."""

    have = Counter(scene)
    need: Counter[str] = Counter()
    for piece in required:
        need.update(piece)
    if any(need[token] > have[token] for token in need):
        return False
    n_req = len(required)
    full_req = (1 << n_req) - 1
    memo: dict[tuple[int, int, int], bool] = {}

    def rec(pos: int, req_mask: int, opt_mask: int) -> bool:
        key = (pos, req_mask, opt_mask)
        cached = memo.get(key)
        if cached is not None:
            return cached
        if pos == len(scene):
            found = req_mask == full_req
            memo[key] = found
            return found
        found = False
        for index, piece in enumerate(required):
            bit = 1 << index
            if req_mask & bit:
                continue
            end = pos + len(piece)
            if scene[pos:end] == piece and rec(end, req_mask | bit, opt_mask):
                found = True
                break
        if not found:
            for index, piece in enumerate(optional):
                bit = 1 << index
                if opt_mask & bit:
                    continue
                end = pos + len(piece)
                if scene[pos:end] == piece and rec(end, req_mask, opt_mask | bit):
                    found = True
                    break
        memo[key] = found
        return found

    return rec(0, 0, 0)


def _shot_path(entity: str, fid: str) -> str:
    if entity and fid:
        return f"scenes/{entity}/кадры/{fid}"
    if entity:
        return f"scenes/{entity}/кадры"
    return f"кадры/{fid}" if fid else "кадры"


def _marked_qc(shot: dict[str, Any]) -> bool:
    for key in ("qc", "QC", "render", "рендер"):
        if shot.get(key):
            return True
    return False


def _verb(op: dict[str, Any]) -> str:
    return str(op.get("op") or op.get("action") or op.get("type") or "").strip().casefold()


def _op_id(op: dict[str, Any]) -> str:
    for key in ("shot_id", "frame_id", "id"):
        if op.get(key) not in (None, ""):
            return str(op[key])
    return ""


def _flag(op: dict[str, Any]) -> bool | None:
    for source in (op.get("fields"), op):
        if isinstance(source, dict) and "активен" in source:
            if source["активен"] is False:
                return False
            if source["активен"] is True:
                return True
    verb = _verb(op)
    if verb in _ACTIVATE_VERBS:
        return True
    if verb in _DEACTIVATE_VERBS:
        return False
    return None


def _targeted(op: dict[str, Any], active: bool) -> str:
    if _flag(op) is not active:
        return ""
    return _op_id(op)


def _ops_target(ops: list[dict[str, Any]], fid: str) -> bool:
    if not fid:
        return False
    for op in ops:
        if _verb(op) in _QC_VERBS and _op_id(op) == fid:
            return True
    return False


def _add_warn(
    violations: list[Violation],
    seen: set[tuple[str, str, str]],
    code: str,
    path: str,
    entity_id: str,
) -> None:
    key = (code, path, entity_id)
    if key in seen:
        return
    seen.add(key)
    violations.append(Violation(code=code, severity="warn", path=path, id=entity_id))


def _add(
    violations: list[Violation],
    seen: set[tuple[str, str, str]],
    code: str,
    path: str,
    entity_id: str,
) -> None:
    key = (code, path, entity_id)
    if key in seen:
        return
    seen.add(key)
    violations.append(Violation(code=code, severity="error", path=path, id=entity_id))
