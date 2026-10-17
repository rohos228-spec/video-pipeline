"""Детерминированный сплит 50 запросов контура B.

Зафиксирован до подбора весов, N реранка и агрегации.
Нечётный номер в ``query_id`` — dev, чётный — test.
Seed не используется: номер уже записан в id.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

QUERY_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_queries.json"
SPLIT_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_split.json"


def query_number(query_id: str) -> int:
    return int(str(query_id).rsplit("-", 1)[-1])


def half_of(query_id: str) -> str:
    """``dev`` для нечётного номера, ``test`` для чётного."""
    return "dev" if query_number(query_id) % 2 else "test"


def split_queries(queries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {"dev": [], "test": []}
    for query in queries:
        groups[half_of(str(query["id"]))].append(query)
    return groups


def write_split_file() -> dict[str, Any]:
    queries = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    groups = split_queries(queries)
    document = {
        "rule": "нечётный номер query_id — dev, чётный — test",
        "dev": [query["id"] for query in groups["dev"]],
        "test": [query["id"] for query in groups["test"]],
    }
    SPLIT_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document


def main() -> None:
    document = write_split_file()
    print(f"dev={len(document['dev'])} test={len(document['test'])}")


if __name__ == "__main__":
    main()
