"""Наборы проекта 65: 65-clean / 65-seeded / 65-canary.

Если WS1 положит реальные файлы в ``datasets/ws1/{name}.json`` без ``stub: true``,
загрузчик возьмёт их. Пока файлов нет — детерминированные заглушки с явной пометкой.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATASETS = Path(__file__).resolve().parent / "datasets"
WS1_DIR = DATASETS / "ws1"

STUB_REASON = (
    "WS1 ещё не выложил данные проекта 65 (tkach-finalochka). "
    "Это заглушка WS6: длины и удаления заданы явно, id стабильны, "
    "эталон и канарейка не смешиваются."
)

LONG_LENGTHS = (320, 340, 360, 401, 450, 512)
SHORT_LENGTHS = (20, 40, 80, 120, 160, 199)


def load_dataset(name: str) -> dict[str, Any]:
    if name not in {"65-clean", "65-seeded", "65-canary"}:
        raise KeyError(name)
    override = WS1_DIR / f"{name}.json"
    if override.is_file():
        data = json.loads(override.read_text(encoding="utf-8"))
        if not data.get("stub", False):
            data["stub"] = False
            data["source"] = "ws1"
            return data
        logger.warning(
            "WS1 override %s помечен stub=true, берём закоммиченный набор %s",
            override,
            name,
        )
    committed = DATASETS / f"{name}.json"
    if committed.is_file():
        return json.loads(committed.read_text(encoding="utf-8"))
    return build_stub(name)


def build_stub(name: str) -> dict[str, Any]:
    if name == "65-seeded":
        return _seeded()
    if name == "65-clean":
        return _clean(dataset="65-clean", id_prefix="vo-65-clean")
    if name == "65-canary":
        data = _clean(dataset="65-canary", id_prefix="vo-65-canary")
        data["note"] = "Канарейка R6. В E1 не оценивается, чтобы не контаминировать 65-clean и 65-seeded."
        return data
    raise KeyError(name)


def registry_ids(dataset: dict[str, Any]) -> set[str]:
    return {str(item["id"]) for item in dataset.get("registry") or []}


def _text(length: int, label: str) -> str:
    prefix = f"[{label}] "
    if len(prefix) >= length:
        return (prefix + "а" * length)[:length]
    return prefix + ("а" * (length - len(prefix)))


def _fragment(fragment_id: str, length: int) -> dict[str, Any]:
    return {"id": fragment_id, "text": _text(length, fragment_id), "scene_id": "sc-65"}


def _base(dataset: str) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "project_id": 65,
        "slug": "tkach-finalochka",
        "stub": True,
        "stub_reason": STUB_REASON,
        "source": "ws6-placeholder",
    }


def _clean(*, dataset: str, id_prefix: str) -> dict[str, Any]:
    fragments = [_fragment(f"{id_prefix}-{index:02d}", 250) for index in range(1, 13)]
    frames = ["fr-65-keep-01", "fr-65-keep-02", "fr-65-keep-03", "fr-65-keep-04"]
    data = _base(dataset)
    data.update(
        {
            "fragments": fragments,
            "frames_before": list(frames),
            "frames_after": list(frames),
            "registry": [],
        }
    )
    return data


def _seeded() -> dict[str, Any]:
    fragments: list[dict[str, Any]] = []
    registry: list[dict[str, str]] = []
    for index, length in enumerate(LONG_LENGTHS, start=1):
        fragment_id = f"vo-65-long-{index:02d}"
        fragments.append(_fragment(fragment_id, length))
        registry.append({"id": fragment_id, "kind": "vo_too_long"})
    for index, length in enumerate(SHORT_LENGTHS, start=1):
        fragment_id = f"vo-65-short-{index:02d}"
        fragments.append(_fragment(fragment_id, length))
        registry.append({"id": fragment_id, "kind": "vo_too_short"})
    deleted = ["fr-65-del-01", "fr-65-del-02"]
    for frame_id in deleted:
        registry.append({"id": frame_id, "kind": "frame_deleted"})
    data = _base("65-seeded")
    data.update(
        {
            "fragments": fragments,
            "frames_before": ["fr-65-keep-01", *deleted, "fr-65-keep-02"],
            "frames_after": ["fr-65-keep-01", "fr-65-keep-02"],
            "registry": registry,
        }
    )
    return data
