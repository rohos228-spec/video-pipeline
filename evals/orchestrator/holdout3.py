"""Holdout-3: 24 запроса из данных проекта 65.

Отбор делает ``random.Random(SEED)`` (CPython). Тексты не пишутся вручную:
у фрагмента это сохранённое поле ``text``, у кадра — имя набора и id.
Семейство назначает квота после перемешивания, без retrieve и без метрик.
Разметка та же, что у прежних наборов: семейство — все реальные чанки двух нод,
правило — один ``part_key``.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from evals.orchestrator.holdout import HOLDOUT3_NAME, relevant_chunks
from evals.orchestrator.project65 import load_dataset

# Зафиксирован до сборки файла. Перебора сидов не было.
SEED = 65
QUERY_COUNT = 24
DATASETS = ("65-clean", "65-seeded")
# Минимум: assemble 5, world 5, camera 3, action 3, script 3.
# Остаток 5 — незакрытые семейства FAMILY_NODES (characters, shots) по 2 и одно правило.
QUOTA: tuple[tuple[str, int], ...] = (
    ("assemble", 5),
    ("world", 5),
    ("camera", 3),
    ("action", 3),
    ("script", 3),
    ("characters", 2),
    ("shots", 2),
    ("rules", 1),
)

HOLDOUT3_PATH = Path(__file__).resolve().parent / "datasets" / HOLDOUT3_NAME

SOURCE = (
    "Пул — load_dataset('65-clean') и load_dataset('65-seeded'): каждый fragment "
    "и каждый id из frames_before. 65-canary не входит: канарейка не смешивается "
    "с эталоном. Каталога datasets/ws1 нет, загрузчик отдаёт закоммиченные заглушки. "
    "Текст запроса — поле text фрагмента или «dataset record_id» кадра. "
    "Формулировки не дописываются. Семейство назначает квота после "
    "random.Random(seed).shuffle записей и слотов квоты. "
    "Ранжировщик, retrieve и метрики в отборе не участвуют."
)
LABEL_RULE = (
    "Семейство: все реальные чанки каталога и промпта двух нод. "
    "Правило: один part_key. Его выбирает тот же seed из part_key корпуса rules, "
    "отсортированных по номеру. Grade 1. chunk_id = chunk_id_for. "
    "Подгонки под текущую выдачу нет."
)


def project65_records() -> list[dict[str, Any]]:
    """Стабильный пул записей проекта 65. Канарейка в пул не входит."""
    records: list[dict[str, Any]] = []
    for name in DATASETS:
        data = load_dataset(name)
        if int(data.get("project_id") or 0) != 65:
            raise RuntimeError(f"{name} не проект 65")
        registry = {str(item["id"]): str(item["kind"]) for item in data.get("registry") or []}
        slug = str(data.get("slug") or "")
        for fragment in data.get("fragments") or []:
            record_id = str(fragment["id"])
            records.append(
                {
                    "dataset": name,
                    "record_type": "fragment",
                    "record_id": record_id,
                    "scene_id": str(fragment.get("scene_id") or ""),
                    "kind": registry.get(record_id, "vo_cell"),
                    "text": str(fragment.get("text") or ""),
                    "slug": slug,
                    "project_id": 65,
                }
            )
        for frame_id in data.get("frames_before") or []:
            record_id = str(frame_id)
            records.append(
                {
                    "dataset": name,
                    "record_type": "frame",
                    "record_id": record_id,
                    "scene_id": "",
                    "kind": registry.get(record_id, "frame"),
                    "text": "",
                    "slug": slug,
                    "project_id": 65,
                }
            )
    records.sort(key=lambda row: (row["dataset"], row["record_type"], row["record_id"]))
    return records


def query_text(record: dict[str, Any]) -> str:
    """Текст только из полей записи. Вопросов поверх данных нет."""
    stored = str(record.get("text") or "")
    if stored:
        return stored
    return f"{record['dataset']} {record['record_id']}"


def quota_slots() -> list[str]:
    slots: list[str] = []
    for family, count in QUOTA:
        slots.extend([family] * count)
    if len(slots) != QUERY_COUNT:
        raise RuntimeError(f"квота {len(slots)} != {QUERY_COUNT}")
    return slots


def _rule_part_keys(docs: list[Any]) -> list[str]:
    keys = {str(doc.part_key) for doc in docs if doc.corpus == "rules" and doc.part_key}
    if not keys:
        raise RuntimeError("в корпусе rules нет part_key")

    def sort_key(part_key: str) -> tuple[int, str]:
        number = part_key.removeprefix("rule-")
        return (int(number) if number.isdigit() else 10**9, part_key)

    return sorted(keys, key=sort_key)


def draw(seed: int, records: list[dict[str, Any]], rule_part_keys: list[str]) -> list[dict[str, Any]]:
    """Перемешать пул и слоты одним Random(seed). Вернуть 24 назначения без текстов вопросов."""
    if len(records) < QUERY_COUNT:
        raise RuntimeError(f"в пуле проекта 65 {len(records)} записей, нужно {QUERY_COUNT}")
    if not rule_part_keys:
        raise RuntimeError("нет part_key правил")
    rng = random.Random(seed)
    ordered = list(records)
    rng.shuffle(ordered)
    chosen = ordered[:QUERY_COUNT]
    slots = quota_slots()
    rng.shuffle(slots)
    part_key = rule_part_keys[rng.randrange(len(rule_part_keys))]
    drawn: list[dict[str, Any]] = []
    for index, (record, family) in enumerate(zip(chosen, slots, strict=True), start=1):
        drawn.append(
            {
                "id": f"h3-{index:02d}",
                "family": family,
                "part_key": part_key if family == "rules" else "",
                "text": query_text(record),
                "source": {
                    "dataset": record["dataset"],
                    "record_type": record["record_type"],
                    "record_id": record["record_id"],
                    "scene_id": record["scene_id"],
                    "kind": record["kind"],
                    "project_id": record["project_id"],
                    "slug": record["slug"],
                },
            }
        )
    return drawn


def build_holdout3_document(docs: list[Any] | None = None, *, seed: int = SEED) -> dict[str, Any]:
    from evals.orchestrator.holdout import _index_docs

    catalog = list(docs) if docs is not None else _index_docs()
    drawn = draw(seed, project65_records(), _rule_part_keys(catalog))
    if len(drawn) != QUERY_COUNT:
        raise RuntimeError(f"ожидалось {QUERY_COUNT} запросов, получилось {len(drawn)}")
    queries = []
    for item in drawn:
        question = {"id": item["id"], "family": item["family"], "part_key": item["part_key"]}
        queries.append(
            {
                "id": item["id"],
                "family": item["family"],
                "text": item["text"],
                "source": item["source"],
                "relevant": relevant_chunks(question, catalog),
            }
        )
    return {
        "role": "holdout-3",
        "seed": seed,
        "sampler": "evals.orchestrator.holdout3.draw",
        "dev": "live-01..live-50 + hold-01..hold-20",
        "source": SOURCE,
        "label_rule": LABEL_RULE,
        "quota": {family: count for family, count in QUOTA},
        "queries": queries,
    }


def load_holdout3_queries() -> list[dict[str, Any]]:
    """Финальный замер. Тюнинг сюда не ходит: ``read_for_tuning`` этот путь отвергает."""
    from evals.orchestrator.frozen_datasets import assert_frozen

    assert_frozen(HOLDOUT3_PATH)
    document = json.loads(HOLDOUT3_PATH.read_text(encoding="utf-8"))
    queries = list(document["queries"])
    if len(queries) != QUERY_COUNT:
        raise RuntimeError(f"в holdout-3 {len(queries)} запросов, ожидалось {QUERY_COUNT}")
    if int(document["seed"]) != SEED:
        raise RuntimeError(f"seed файла {document['seed']} != {SEED}")
    return queries


def write_holdout3_file(*, rewrite: bool = False) -> dict[str, Any]:
    """Существующий файл только сверяется. Перезапись запрещена."""
    if HOLDOUT3_PATH.exists():
        from evals.orchestrator.frozen_datasets import FrozenDatasetError, assert_frozen

        assert_frozen(HOLDOUT3_PATH)
        if rewrite:
            raise FrozenDatasetError(f"{HOLDOUT3_NAME} read-only")
        return json.loads(HOLDOUT3_PATH.read_text(encoding="utf-8"))
    document = build_holdout3_document()
    HOLDOUT3_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document
