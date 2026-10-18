"""Правка meta проекта: только свой ключ, версия и снимок.

Целиком словарь после GET не записывается. Устаревшая версия — отказ 409,
чтобы поздняя правка другого ключа не затиралась.
"""

from __future__ import annotations

import copy
from typing import Any

from app.orchestrator.node_store.errors import VersionConflict


_ASSEMBLE_KEYS = (
    "post_voiceover_tail_seconds",
    "bgm_level",
    "subtitles_enabled",
    "skip_intro_enabled",
    "skip_intro_seconds",
)
_CONFIG_FIELDS = (
    "role",
    "outputMode",
    "emitKinds",
    "takeFromEdges",
    "useSnapshot",
    "checkMode",
    "checkFix",
    "checkPromptSource",
)


class MetaBook:
    """Память meta одного проекта: version растёт на каждую успешную правку."""

    def __init__(self, meta: dict | None = None) -> None:
        self.meta: dict[str, Any] = {
            "prompt_slot_variants": {},
            "node_step_params": {},
            "disabled_nodes": [],
            "node_config": {},
        }
        if meta:
            for key, value in meta.items():
                self.meta[key] = copy.deepcopy(value)
        self.version = 0
        self.snapshots: list[dict[str, Any]] = []

    def patch_meta(self, key: str, value: Any, *, version: int) -> int:
        """Заменяет один ключ meta. Остальные ключи не читаются и не пишутся."""
        self._check(version)
        before = copy.deepcopy(self.meta.get(key))
        stored = copy.deepcopy(value)
        self.meta[key] = stored
        self.version += 1
        self._record("patch_meta", key, before, copy.deepcopy(stored))
        return self.version

    def set_prompt_variant(
        self,
        *,
        node_key: str,
        slot_id: str = "main",
        variant: str,
        version: int,
    ) -> dict:
        """Пишет один слот ноды. Соседние слоты и другие ноды не пересобираются."""
        self._check(version)
        slots = self.meta.setdefault("prompt_slot_variants", {})
        node = slots.setdefault(node_key, {})
        before = copy.deepcopy(node.get(slot_id))
        node[slot_id] = variant
        self.version += 1
        target = {"node_key": node_key, "slot_id": slot_id}
        self._record("node.set_prompt_variant", target, before, variant)
        return {**target, "variant": variant, "version": self.version}

    def set_assemble_params(self, *, values: dict, version: int) -> dict:
        """Обновляет только ключи сборки внутри node_step_params['assemble']."""
        self._check(version)
        changed = {key: values[key] for key in _ASSEMBLE_KEYS if key in values}
        if not changed:
            raise ValueError("не указано ни одного параметра сборки")
        params = self.meta.setdefault("node_step_params", {})
        before = copy.deepcopy(params.get("assemble") or {})
        bucket = dict(before)
        bucket.update(changed)
        params["assemble"] = bucket
        self.version += 1
        self._record(
            "montage.set_assemble_params",
            "assemble",
            before,
            copy.deepcopy(bucket),
        )
        return {"assemble": changed, "version": self.version}

    def set_config(self, *, node_key: str, fields: dict, version: int) -> dict:
        """Сливает поля конфига одной ноды. Прежние поля этой ноды остаются."""
        self._check(version)
        body = {key: fields[key] for key in _CONFIG_FIELDS if key in fields}
        if not body:
            raise ValueError("не указано ни одного поля настройки")
        configs = self.meta.setdefault("node_config", {})
        before = copy.deepcopy(configs.get(node_key) or {})
        merged = dict(before)
        merged.update(body)
        configs[node_key] = merged
        self.version += 1
        self._record("node.set_config", node_key, before, copy.deepcopy(merged))
        return {"ok": True, "config": copy.deepcopy(merged), "version": self.version}

    def set_disabled(self, *, node_key: str, disabled: bool, version: int) -> dict:
        """Добавляет или убирает одну ноду. Список не заменяется снимком чужого GET."""
        self._check(version)
        current = [str(item) for item in (self.meta.get("disabled_nodes") or [])]
        before = list(current)
        if disabled:
            updated = current if node_key in current else [*current, node_key]
        else:
            updated = [item for item in current if item != node_key]
        self.meta["disabled_nodes"] = updated
        self.version += 1
        self._record("node.set_disabled", node_key, before, list(updated))
        return {
            "node_key": node_key,
            "disabled": disabled,
            "disabled_nodes": list(updated),
            "version": self.version,
        }

    def list_snapshots(self) -> list[dict]:
        return [copy.deepcopy(row) for row in self.snapshots]

    def rollback(self, snapshot_id: int) -> dict:
        """Возвращает before, если текущее значение всё ещё равно after."""
        row = self._get(snapshot_id)
        current = self._current(row)
        if current != row["after"]:
            raise VersionConflict(
                "значение изменилось после правки: откат затёр бы более позднюю запись"
            )
        self._restore(row)
        self.version += 1
        return {"restored_snapshot_id": row["id"], "version": self.version}

    def _check(self, version: int) -> None:
        if version != self.version:
            raise VersionConflict(
                f"версия meta {version} устарела, текущая {self.version}"
            )

    def _record(self, action: str, target: Any, before: Any, after: Any) -> None:
        self.snapshots.append(
            {
                "id": len(self.snapshots) + 1,
                "action": action,
                "target": copy.deepcopy(target),
                "before": before,
                "after": after,
            }
        )

    def _get(self, snapshot_id: int) -> dict:
        for row in self.snapshots:
            if row["id"] == snapshot_id:
                return row
        raise KeyError(f"снимка {snapshot_id} нет")

    def _current(self, row: dict) -> Any:
        action = row["action"]
        target = row["target"]
        if action == "patch_meta":
            return copy.deepcopy(self.meta.get(target))
        if action == "node.set_prompt_variant":
            slots = self.meta.get("prompt_slot_variants") or {}
            node = slots.get(target["node_key"]) or {}
            return copy.deepcopy(node.get(target["slot_id"]))
        if action == "montage.set_assemble_params":
            params = self.meta.get("node_step_params") or {}
            return copy.deepcopy(params.get("assemble") or {})
        if action == "node.set_config":
            configs = self.meta.get("node_config") or {}
            return copy.deepcopy(configs.get(target) or {})
        if action == "node.set_disabled":
            return [str(item) for item in (self.meta.get("disabled_nodes") or [])]
        raise KeyError(action)

    def _restore(self, row: dict) -> None:
        action = row["action"]
        target = row["target"]
        before = copy.deepcopy(row["before"])
        if action == "patch_meta":
            if before is None:
                self.meta.pop(target, None)
            else:
                self.meta[target] = before
            return
        if action == "node.set_prompt_variant":
            slots = self.meta.setdefault("prompt_slot_variants", {})
            node = slots.setdefault(target["node_key"], {})
            if before is None:
                node.pop(target["slot_id"], None)
            else:
                node[target["slot_id"]] = before
            return
        if action == "montage.set_assemble_params":
            params = self.meta.setdefault("node_step_params", {})
            params["assemble"] = before
            return
        if action == "node.set_config":
            configs = self.meta.setdefault("node_config", {})
            configs[target] = before
            return
        if action == "node.set_disabled":
            self.meta["disabled_nodes"] = list(before or [])
            return
        raise KeyError(action)


def load_book(meta: dict | None) -> MetaBook:
    """Книга по текущему meta. Версия берётся из ключа meta_version."""
    source = meta if isinstance(meta, dict) else None
    book = MetaBook(source)
    raw = source.get("meta_version") if source else 0
    try:
        book.version = int(raw or 0)
    except (TypeError, ValueError):
        book.version = 0
    return book


def checked_meta_version(params: dict, book: MetaBook) -> int:
    """Версия из запроса для оптимистичной блокировки. Без expected_meta_version — VersionConflict.

    Подставлять book.version нельзя: книга только что прочитана, сравнение с собой ничего не ловит.
    """
    return expected_version(params, book)


def expected_version(params: dict, book: MetaBook) -> int:
    """Версия из запроса. Без ключа это не текущая книга: сравнение с собой — конфликт."""
    if "expected_meta_version" not in params:
        raise VersionConflict("нет expected_meta_version")
    try:
        return int(params["expected_meta_version"])
    except (TypeError, ValueError) as exc:
        raise VersionConflict("версия meta не число") from exc

