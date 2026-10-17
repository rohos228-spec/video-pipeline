"""Адаптер запрета физического удаления.

Пока WS1 не опубликовал вход в ``db_apply.py`` (ветка
``cursor/ws1-r1-validators``), сравнение живёт здесь. Подмена на
настоящую точку WS1 — правка только этой функции.
"""

from __future__ import annotations

import hashlib
from typing import Any

from loguru import logger

from app.services.frame_tombstones import (
    mark_shots_inactive,
    merge_kadry_keep_missing,
)

_SHOT_LIST_KEYS = ("кадры", "shots")
_ROW_LIST_KEYS = {
    "frame_texts": "text",
    "texts": "text",
    "frame_edges": "edge",
    "edges": "edge",
    "characters": "character",
    "персонажи": "character",
    "persons": "character",
    "blocks": "block",
    "scenes": "scene",
    "сцены": "scene",
    "frames": "frame",
}
_FLAG_KEYS = {"активен", "удалён", "карантин", "пометка", "порядок"}
_TEXT_KIND_IGNORED = True


def guard(
    before: Any,
    after: Any,
    *,
    project_id: Any,
    source: str,
    mode: str = "warn_mark",
) -> dict[str, Any]:
    """Сравнить снимок до записи и после.

    ``refuse`` — не писать: ``after`` в ответе это прежний снимок.
    ``warn_mark`` — вернуть ``after`` с пропавшими шотами обратно,
    ``активен: False``.
    """
    applied = mode if mode in {"refuse", "warn_mark"} else "warn_mark"
    invalid = _invalid_kadry(after)
    missing = sorted(_collect(before) - _collect(after))
    if not invalid and not missing:
        return {
            "ok": True,
            "mode": applied,
            "missing_ids": [],
            "reason": "",
            "after": after,
        }
    reason = ",".join(
        part for part, hit in (("invalid_shape", invalid), ("missing_ids", missing)) if hit
    )
    logger.warning(
        "WARN no_deletions_guard: source={} project={} mode={} reason={} missing={}",
        source,
        project_id,
        applied,
        reason,
        ", ".join(missing) or "-",
    )
    if applied == "refuse":
        return {
            "ok": False,
            "mode": "refuse",
            "missing_ids": missing,
            "reason": reason,
            "after": before,
        }
    _repair(before, after, set(missing))
    return {
        "ok": False,
        "mode": "warn_mark",
        "missing_ids": missing,
        "reason": reason,
        "after": after,
    }


def _invalid_kadry(node: Any, seen: set[int] | None = None) -> bool:
    seen = seen if seen is not None else set()
    mark = id(node)
    if mark in seen:
        return False
    if isinstance(node, (dict, list)) or _frame_like(node):
        seen.add(mark)
    mapping = _mapping(node)
    if mapping is None:
        if isinstance(node, list):
            return any(_invalid_kadry(item, seen) for item in node)
        return False
    bad = False
    for key in _SHOT_LIST_KEYS:
        if key in mapping and not isinstance(mapping.get(key), list):
            bad = True
    for key, value in mapping.items():
        if key in _SHOT_LIST_KEYS:
            if isinstance(value, list):
                bad = any(_invalid_kadry(item, seen) for item in value) or bad
            continue
        if isinstance(value, (dict, list)) or _frame_like(value):
            bad = _invalid_kadry(value, seen) or bad
    return bad


def _collect(node: Any, seen: set[int] | None = None) -> set[str]:
    seen = seen if seen is not None else set()
    mark = id(node)
    if mark in seen:
        return set()
    if isinstance(node, (dict, list)) or _frame_like(node):
        seen.add(mark)
    found: set[str] = set()
    mapping = _mapping(node)
    if mapping is None:
        if isinstance(node, list):
            for item in node:
                found |= _collect(item, seen)
        return found
    if isinstance(node, dict):
        found |= _own_identities(node)
    frame_key = _frame_identity(node)
    if frame_key:
        found.add(frame_key)
    for key in _SHOT_LIST_KEYS:
        rows = mapping.get(key)
        if isinstance(rows, list):
            for row in rows:
                ident = _shot_identity(row)
                if ident:
                    found.add(ident)
                found |= _collect(row, seen)
    for key, kind in _ROW_LIST_KEYS.items():
        rows = mapping.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            ident = _row_identity(row, kind)
            if ident:
                found.add(ident)
            found |= _collect(row, seen)
    for key, value in mapping.items():
        if key in _SHOT_LIST_KEYS or key in _ROW_LIST_KEYS:
            continue
        if isinstance(value, (dict, list)) or _frame_like(value):
            found |= _collect(value, seen)
    return found


def _repair(before: Any, after: Any, missing: set[str], seen: set[int] | None = None) -> None:
    seen = seen if seen is not None else set()
    pair = (id(before), id(after))
    if pair in seen:
        return
    seen.add(pair)
    if isinstance(before, list) and isinstance(after, list):
        _repair_row_list(before, after, missing, "shot")
        for ident, child in _pair_children_list(before).items():
            other = _pair_children_list(after).get(ident)
            if other is not None:
                _repair(child, other, missing, seen)
        return
    for bcont in _containers(before):
        aconts = _containers(after)
        acont = _matching_container(bcont, aconts)
        if acont is None:
            continue
        _restore_lists(bcont, acont, missing)
    bmap = _mapping(before)
    amap = _mapping(after)
    if not bmap or not amap:
        return
    for key, bval in bmap.items():
        if key in _SHOT_LIST_KEYS or key in _ROW_LIST_KEYS:
            rows = bval if isinstance(bval, list) else []
            arows = amap.get(key) if isinstance(amap.get(key), list) else []
            for ident, child in _pair_children_list(rows).items():
                other = _pair_children_list(arows).get(ident)
                if other is not None:
                    _repair(child, other, missing, seen)
            continue
        aval = amap.get(key)
        if isinstance(bval, (dict, list)) and isinstance(aval, (dict, list)):
            _repair(bval, aval, missing, seen)


def _containers(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        attrs = node.get("attrs")
        if isinstance(attrs, dict):
            found.append(attrs)
    elif _frame_like(node):
        attrs = getattr(node, "attrs", None)
        if isinstance(attrs, dict):
            found.append(attrs)
    return found


def _matching_container(
    source: dict[str, Any], targets: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if not targets:
        return None
    if source in targets:
        return source
    for key in (*_SHOT_LIST_KEYS, *_ROW_LIST_KEYS):
        if key in source:
            for target in targets:
                if key in target:
                    return target
    return targets[0]


def _restore_lists(before: dict[str, Any], after: dict[str, Any], missing: set[str]) -> None:
    for key in _SHOT_LIST_KEYS:
        if key not in before:
            if key in after and not isinstance(after.get(key), list):
                after[key] = []
            continue
        brows = before.get(key)
        if not isinstance(brows, list):
            continue
        if not any(_shot_identity(row) in missing for row in brows):
            if isinstance(after.get(key), list):
                continue
        arows = after.get(key) if key in after else None
        if not isinstance(arows, list):
            restored = merge_kadry_keep_missing(brows, arows if arows is not None else brows)
            after[key] = _mark_missing_shots(restored, missing)
            continue
        merged = merge_kadry_keep_missing(brows, arows)
        head, tail = merged[: len(arows)], merged[len(arows) :]
        after[key] = head + [row for row in tail if _shot_identity(row) in missing]
    for key, kind in _ROW_LIST_KEYS.items():
        if key not in before or not isinstance(before.get(key), list):
            continue
        brows = before[key]
        lost = [row for row in brows if _row_identity(row, kind) in missing]
        if not lost:
            continue
        arows = after.get(key) if key in after else None
        restored = mark_shots_inactive(
            [row for row in lost if isinstance(row, dict)],
            where="no_deletions_guard",
        )
        if not isinstance(arows, list):
            after[key] = restored
            continue
        arows.extend(restored)


def _repair_row_list(before: list[Any], after: list[Any], missing: set[str], kind: str) -> None:
    del kind
    if any(
        isinstance(row, dict) and _looks_like_shot(row) and _shot_identity(row) in missing
        for row in before
    ):
        merged = merge_kadry_keep_missing(before, after)
        head, tail = merged[: len(after)], merged[len(after) :]
        after[:] = head + [row for row in tail if _shot_identity(row) in missing]
    present: set[str] = set()
    for row in after:
        if isinstance(row, dict):
            present |= _identities_of(row)
    lost = [
        row
        for row in before
        if isinstance(row, dict) and (_identities_of(row) & missing) and not (_identities_of(row) & present)
    ]
    if not lost:
        return
    after.extend(mark_shots_inactive(lost, where="no_deletions_guard"))


def _mark_missing_shots(rows: Any, missing: set[str]) -> Any:
    if not isinstance(rows, list):
        return rows
    marked: list[Any] = []
    for row in rows:
        if isinstance(row, dict) and _shot_identity(row) in missing:
            item = dict(row)
            item["активен"] = False
            marked.append(item)
            continue
        marked.append(row)
    return marked


def _identities_of(node: dict[str, Any]) -> set[str]:
    found = _own_identities(node)
    frame = _frame_identity(node)
    if frame:
        found.add(frame)
    return found


_SHOT_MARK_KEYS = (
    "id_shot",
    "shot_id",
    "сцена",
    "номер",
    "закадр",
    "описание",
    "описание_кадра",
    "description",
    "действие",
)
_NOT_SHOT_KEYS = (
    "uuid",
    "number",
    "voiceover_text",
    "attrs",
    "text",
    "kind",
    "name",
    "имя",
    "from",
    "to",
    "source",
    "target",
    "blocks",
    "n",
)


def _looks_like_shot(node: dict[str, Any]) -> bool:
    if any(str(node.get(key) or "").strip() for key in _SHOT_MARK_KEYS):
        return True
    if node.get("id") in (None, ""):
        return False
    if any(key in node for key in _NOT_SHOT_KEYS):
        return False
    return True


def _own_identities(node: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    if _looks_like_shot(node):
        ident = _shot_identity(node)
        if ident:
            found.add(ident)
    if "text" in node or str(node.get("kind") or "") in {"voiceover", "vo_archived"}:
        ident = _row_identity(node, "text")
        if ident:
            found.add(ident)
    if "name" in node or "имя" in node:
        ident = _row_identity(node, "character")
        if ident:
            found.add(ident)
    if any(key in node for key in ("from", "to", "source", "target")):
        ident = _row_identity(node, "edge")
        if ident:
            found.add(ident)
    if not _looks_like_shot(node) and ("n" in node or node.get("номер") not in (None, "")):
        ident = _row_identity(node, "scene")
        if ident:
            found.add(ident)
    return found


def _pair_children_list(rows: list[Any]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    if not isinstance(rows, list):
        return found
    for row in rows:
        ident = (
            _frame_identity(row)
            or _row_identity(row, "block")
            or _row_identity(row, "scene")
            or _shot_identity(row)
        )
        if ident and _mapping(row) is not None:
            found.setdefault(ident, row)
    return found


def _mapping(node: Any) -> dict[str, Any] | None:
    if isinstance(node, dict):
        return node
    if _frame_like(node):
        attrs = getattr(node, "attrs", None)
        view: dict[str, Any] = {}
        if isinstance(attrs, dict):
            view["attrs"] = attrs
            for key in _SHOT_LIST_KEYS:
                if key in attrs:
                    view[key] = attrs[key]
        for key in ("frame_texts", "frame_edges", "characters", "texts", "edges"):
            value = getattr(node, key, None)
            if isinstance(value, list):
                view[key] = value
        return view
    return None


def _frame_like(node: Any) -> bool:
    return not isinstance(node, (dict, list, str, bytes)) and isinstance(
        getattr(node, "attrs", None), dict
    )


def _frame_identity(node: Any) -> str | None:
    if isinstance(node, dict):
        uid = str(node.get("uuid") or "").strip()
        if uid:
            return f"frame:{uid}"
        if "number" in node or "voiceover_text" in node or isinstance(node.get("attrs"), dict):
            raw = node.get("id")
            if raw not in (None, ""):
                return f"frame:{raw}"
        return None
    if not _frame_like(node):
        return None
    uid = str(getattr(node, "uuid", "") or "").strip()
    if uid:
        return f"frame:{uid}"
    raw = getattr(node, "id", None)
    if raw not in (None, ""):
        return f"frame:{raw}"
    return None


def _shot_identity(shot: Any) -> str | None:
    if not isinstance(shot, dict):
        return None
    for key in ("id", "id_shot", "shot_id"):
        val = str(shot.get(key) or "").strip()
        if val:
            return f"shot:{val}"
    scene = shot.get("сцена")
    number = shot.get("номер")
    if scene not in (None, "") or number not in (None, ""):
        return f"shot:{scene}:{number}"
    blob = "|".join(
        f"{key}={str(shot.get(key) or '').strip()}"
        for key in ("закадр", "описание", "описание_кадра", "description", "действие")
        if str(shot.get(key) or "").strip()
    )
    if not blob:
        plain = [
            (key, shot.get(key))
            for key in sorted(shot)
            if key not in _FLAG_KEYS and shot.get(key) not in (None, "", [], {})
        ]
        blob = repr(plain)
    digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
    return f"shot:hash:{digest}"


def _row_identity(row: Any, kind: str) -> str | None:
    if not isinstance(row, dict):
        return None
    raw = row.get("id")
    if raw not in (None, ""):
        return f"{kind}:{raw}"
    if kind == "text":
        body = str(row.get("text") or "").strip()
        if body:
            digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]
            return f"text:hash:{digest}"
    if kind == "character":
        name = str(row.get("name") or row.get("имя") or "").strip()
        if name:
            return f"character:name:{name}"
    if kind == "edge":
        ends = (
            str(row.get("from") or row.get("source") or ""),
            str(row.get("to") or row.get("target") or ""),
            str(row.get("type") or ""),
        )
        if any(ends):
            return f"edge:{ends[0]}:{ends[1]}:{ends[2]}"
    if kind == "scene":
        number = row.get("n") if row.get("n") not in (None, "") else row.get("номер")
        if number not in (None, ""):
            return f"scene:{number}"
    return None
