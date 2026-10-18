"""Сцены и кадры не удаляются: падение числа или пропажа id — blocking."""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.entities import (
    archived_frame_ids,
    character_id,
    collect_characters,
    collect_scenes,
    frame_id,
    frame_is_exempt,
    scene_id,
    scene_kadry,
    scene_number,
    text_of,
)
from app.orchestrator.validators.result import make_result
from app.orchestrator.validators.snapshot import load_project_snapshot
from app.orchestrator.validators.violations import Violation, sorted_violations

_NAME = "no_deletions"
_SCENE_KEYS = ("scenes", "сцены")
_FRAME_KEYS = ("frames", "shots", "кадры")


def check(project_id: int, context: dict[str, Any]) -> Any:
    before_raw = context.get("before") if "before" in context else None
    after_raw = context.get("after") if "after" in context else None
    loaded = False
    if after_raw is None and _wants_load(context):
        loaded = True
        after_raw = _load(project_id, context)
        if _unavailable(after_raw):
            if before_raw is None:
                return make_result(
                    name=_NAME,
                    passed=True,
                    blocking=False,
                    details={"checked": False, "reason": "snapshot_unavailable"},
                )
            return _block({"reason": "snapshot_unavailable", "loaded": True})
    if before_raw is None and after_raw is None:
        return make_result(
            name=_NAME,
            passed=True,
            blocking=False,
            details={"checked": False, "reason": "no_snapshot"},
        )
    if before_raw is None or after_raw is None:
        return _block({"reason": "snapshot_incomplete", "loaded": loaded})
    before = _normalize(before_raw)
    after = _normalize(after_raw)
    report: dict[str, Any] = {}
    missing = False
    for bucket in ("scenes", "frames"):
        diff = _diff(before[bucket], after[bucket])
        report[bucket] = diff
        if diff["count_decreased"] or diff["missing_ids"]:
            missing = True
    if missing:
        report["reason"] = "deletion"
        return _block(report)
    report["reason"] = "ok"
    return make_result(name=_NAME, passed=True, blocking=False, details=report)


def _unavailable(raw: Any) -> bool:
    return isinstance(raw, dict) and raw.get("available") is False


def _wants_load(context: dict[str, Any]) -> bool:
    return any(key in context for key in ("snapshot_loader", "db_path", "snapshot_conn")) or bool(
        context.get("load_snapshot")
    )


def _load(project_id: int, context: dict[str, Any]) -> Any:
    loader = context.get("snapshot_loader")
    if callable(loader):
        return loader(project_id)
    return load_project_snapshot(
        project_id,
        conn=context.get("snapshot_conn"),
        db_path=context.get("db_path"),
    )


def _block(details: dict[str, Any]) -> Any:
    return make_result(name=_NAME, passed=False, blocking=True, details=details)


def _normalize(side: Any) -> dict[str, list[Any]]:
    if isinstance(side, list):
        return {"scenes": [], "frames": list(side)}
    if not isinstance(side, dict):
        return {"scenes": [], "frames": []}
    return {"scenes": _pick(side, _SCENE_KEYS), "frames": _pick(side, _FRAME_KEYS)}


def _pick(side: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    for key in keys:
        if key in side:
            value = side[key]
            if isinstance(value, list):
                return list(value)
            return []
    return []


def _diff(before: list[Any], after: list[Any]) -> dict[str, Any]:
    before_ids = _ids(before)
    after_ids = _ids(after)
    after_set = set(after_ids)
    missing = [item for item in before_ids if item not in after_set]
    return {
        "count_before": len(before),
        "count_after": len(after),
        "count_decreased": len(after) < len(before),
        "missing_ids": missing,
    }


_DELETE_VERBS = {"delete", "remove", "drop", "удалить"}
_CHAR_KINDS = {"character", "characters", "персонаж", "персонажи"}


def check_characters(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    """Персонаж остаётся в снимке. ``archived`` допустим, op удаления — нет."""

    violations: list[Violation] = []
    seen: set[tuple[str, str, str]] = set()
    if before is not None and after is not None:
        after_ids = {
            cid for item in collect_characters(after) if (cid := character_id(item))
        }
        for character in collect_characters(before):
            cid = character_id(character)
            if not cid or cid in after_ids:
                continue
            _append(
                violations,
                seen,
                Violation(
                    code="character_deleted",
                    severity="error",
                    path=f"characters/{cid}",
                    id=cid,
                ),
            )
    for index, op in enumerate(list(ops or [])):
        cid = _character_delete_id(op)
        if cid is None:
            continue
        _append(
            violations,
            seen,
            Violation(
                code="character_deleted",
                severity="error",
                path=f"ops/{index}",
                id=cid,
            ),
        )
    return sorted_violations(violations)


def check_frames(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    """``кадры[]`` сцены не теряет живой кадр.

    ``активен: False`` и архив — не нарушение. Несписковое ``кадры``
    (dict, строка, null) — ``frames_no_data`` со severity warn, это не
    «все кадры удалены».
    """

    del ops
    if before is None or after is None:
        return []
    after_by_id, after_by_number = _index_scenes(collect_scenes(after))
    archive_ids = archived_frame_ids(after)
    violations: list[Violation] = []
    seen: set[tuple[str, str, str]] = set()
    for scene in collect_scenes(before):
        sid = scene_id(scene)
        number = scene_number(scene)
        entity = sid or number or ""
        status, frames = scene_kadry(scene)
        match = _match_scene(sid, number, after_by_id, after_by_number)
        after_status, after_frames = scene_kadry(match) if match is not None else ("absent", None)
        if status == "nodata" or after_status == "nodata":
            _append(
                violations,
                seen,
                Violation(
                    code="frames_no_data",
                    severity="warn",
                    path=f"scenes/{entity}/кадры" if entity else "scenes/кадры",
                    id=entity,
                ),
            )
            continue
        if status != "list":
            continue
        if not isinstance(after_frames, list):
            after_frames = []
        kept = {fid for item in after_frames if (fid := frame_id(item))}
        for frame in frames:
            if frame_is_exempt(frame):
                continue
            fid = frame_id(frame)
            if not fid or fid in kept or fid in archive_ids:
                continue
            _append(
                violations,
                seen,
                Violation(
                    code="frame_deleted",
                    severity="error",
                    path=f"scenes/{entity}/кадры/{fid}" if entity else f"кадры/{fid}",
                    id=fid,
                ),
            )
        # «После» не пропускает неактивные: шот на месте с активен:False
        # присутствует, как при удалён. Настоящий уход текста всё ещё виден.
        gone = _anonymous_texts(frames) - _anonymous_texts(after_frames, skip_exempt=False)
        for text in sorted(gone):
            _append(
                violations,
                seen,
                Violation(
                    code="frame_deleted",
                    severity="error",
                    path=f"scenes/{entity}/кадры" if entity else "кадры",
                    id=text,
                ),
            )
    return sorted_violations(violations)


def check_scenes(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    """Сцена на месте, если жив её id. Архив и ``активен: False`` допустимы.

    Сопоставление по id. Номер смотрим только у сцены без id: смена номера
    при том же id — не ``scene_deleted``. Снимок только читается.
    """

    del ops
    if before is None or after is None:
        return []
    before_scenes = collect_scenes(before)
    after_scenes = collect_scenes(after)
    by_id: dict[str, list[dict[str, Any]]] = {}
    for scene in after_scenes:
        sid = scene_id(scene)
        if sid:
            by_id.setdefault(sid, []).append(scene)
    violations: list[Violation] = []
    seen: set[tuple[str, str]] = set()
    for scene in before_scenes:
        sid = scene_id(scene)
        number = scene_number(scene)
        entity = sid or number or ""
        path = f"scenes/{entity}" if entity else "scenes"
        lost = False
        if sid:
            if not by_id.get(sid):
                lost = True
        elif number is not None and not any(scene_number(item) == number for item in after_scenes):
            lost = True
        if not lost or (path, entity) in seen:
            continue
        seen.add((path, entity))
        violations.append(
            Violation(code="scene_deleted", severity="error", path=path, id=entity)
        )
    return sorted_violations(violations)


def _index_scenes(
    scenes: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_number: dict[str, dict[str, Any]] = {}
    for scene in scenes:
        sid = scene_id(scene)
        number = scene_number(scene)
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


def _anonymous_texts(frames: list[Any], *, skip_exempt: bool = True) -> set[str]:
    """Уникальный текст кадров без id. Число копий одного текста не важно.

    ``skip_exempt`` — для «до»: уже неактивный шот может уйти.
    На «после» шот без id, оставшийся в списке, считается присутствующим.
    """

    found: set[str] = set()
    for frame in frames:
        if frame_id(frame) is not None:
            continue
        if skip_exempt and frame_is_exempt(frame):
            continue
        found.add(_anonymous_text(frame))
    return found


def _anonymous_text(frame: Any) -> str:
    if isinstance(frame, dict):
        for key in ("закадр", "voiceover", "vo", "text", "текст", "действие"):
            text = text_of(frame.get(key))
            if text:
                return text
        return ""
    return text_of(frame)


def _character_delete_id(op: Any) -> str | None:
    if not isinstance(op, dict):
        return None
    verb = str(op.get("op") or op.get("action") or op.get("type") or "").strip().casefold()
    kind = str(op.get("entity") or op.get("target") or op.get("kind") or "").strip().casefold()
    if verb in {"archive", "archived", "архив"} or verb.startswith("archive"):
        return None
    is_delete = (
        verb in _DELETE_VERBS
        or verb.startswith("delete")
        or verb.startswith("remove")
        or verb.startswith("удал")
    )
    is_character = kind in _CHAR_KINDS or "character" in verb or "персонаж" in verb
    if not (is_delete and is_character):
        return None
    for key in ("id", "character_id", "entity_id"):
        if op.get(key) not in (None, ""):
            return str(op[key])
    return ""


def _append(violations: list[Violation], seen: set[tuple[str, str, str]], item: Violation) -> None:
    key = (item.code, item.path, item.id)
    if key in seen:
        return
    seen.add(key)
    violations.append(item)


def _ids(items: list[Any]) -> list[str]:
    found: list[str] = []
    for item in items:
        if isinstance(item, dict):
            if item.get("id") is None:
                continue
            found.append(str(item["id"]))
        else:
            found.append(str(item))
    return found
