"""Сцены и кадры не удаляются: падение числа или пропажа id — blocking."""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.result import make_result
from app.orchestrator.validators.snapshot import load_project_snapshot

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
