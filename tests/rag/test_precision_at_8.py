"""precision@8 на 50 размеченных запросах. Эмбеддер — детерминированный фейк."""

from __future__ import annotations

import json
from pathlib import Path

from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.types import CORPORA, SourceDoc
from app.project_root import find_project_root
from evals.orchestrator.precision_dataset import build_precision_dataset

ROOT = find_project_root()
DATASET_PATH = ROOT / "evals" / "orchestrator" / "datasets" / "precision_at_8.json"
REPORT_PATH = ROOT / "evals" / "orchestrator" / "datasets" / "precision_at_8_report.md"
RULES_PATH = ROOT / "docs" / "fable" / "user_rules.md"


def _engine(tmp_path: Path, dataset: dict) -> RagEngine:
    grouped: dict[str, list[SourceDoc]] = {name: [] for name in CORPORA}
    for raw in dataset["documents"]:
        grouped[raw["corpus"]].append(
            SourceDoc(
                corpus=raw["corpus"],
                source_id=raw["source_id"],
                title=raw["title"],
                text=raw["text"],
                metadata=dict(raw["metadata"]),
            )
        )
    static = {name: docs for name, docs in grouped.items() if name != "rules"}
    return RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=RULES_PATH,
        static_docs=static,
        scan_git=False,
    )


def _score(dataset: dict, engine: RagEngine) -> tuple[float, float, list[dict]]:
    rows = []
    for query in dataset["queries"]:
        hits = engine.retrieve(query["query"], query["corpora"], top_k=8)
        relevant = set(query["relevant_source_ids"])
        top_ids = [hit.source_id for hit in hits]
        matched = sum(1 for source_id in top_ids if source_id in relevant)
        precision = matched / 8
        rows.append(
            {
                "id": query["id"],
                "precision_at_8": precision,
                "hit": bool(matched),
                "matched": matched,
            }
        )
    macro = sum(row["precision_at_8"] for row in rows) / len(rows)
    hit_rate = sum(1 for row in rows if row["hit"]) / len(rows)
    return macro, hit_rate, rows


def render_report(dataset: dict, macro: float, hit_rate: float, rows: list[dict]) -> str:
    by_theme: dict[str, list[float]] = {}
    for row in rows:
        theme = row["id"].rsplit("-", 1)[0]
        by_theme.setdefault(theme, []).append(row["precision_at_8"])
    lines = [
        "# Отчёт precision@8",
        "",
        "Корпуса запроса: `rules`, `history`, `nodes`, `prompts`. "
        "В индекс вместе с датасетом попали живые правила из `docs/fable/user_rules.md` "
        "(отвлекающий корпус, не размеченный как релевантный).",
        "",
        "Определение: macro precision@8 = среднее по 50 запросам величины "
        "`|релевантные source_id ∩ top-8| / 8`. Hit@8 — доля запросов, где в top-8 есть хотя бы один релевантный.",
        "",
        "Порог релиза precision@8 ≥ 0.8 снят. Приёмка 50 живых перефраз — контур B: "
        "hit@5 ≥ 0.9 и nDCG@8 ≥ 0.7. Число ниже — регрессия закрытого набора на hashing-фейке с явными якорями.",
        "",
        "Эмбеддер прогона: детерминированный hashing-фейк с префиксами `query:` / `passage:`. "
        "Веса multilingual-e5-small не скачивались. Лексический канал — FTS5 BM25. Слияние — RRF, k=60.",
        "",
        f"- Запросов: **{len(dataset['queries'])}**",
        f"- Документов датасета: **{len(dataset['documents'])}**",
        "- Релевантных на запрос: **8**",
        f"- **precision@8 = {macro:.4f}**",
        f"- hit@8 = {hit_rate:.4f}",
        "",
        "| Тема | Средний precision@8 |",
        "|---|---|",
    ]
    for theme, values in by_theme.items():
        lines.append(f"| {theme} | {sum(values) / len(values):.4f} |")
    lines.extend(
        [
            "",
            "Разметка: у темы общий редкий якорь, он есть в восьми документах и в каждой формулировке запроса. "
            "Отвлекающие заметки используют слова «кадр», «сцена», «промпт», «закадр» без якоря. "
            "Это измерение ретривера на закрытом наборе, а не ручная разметка Фейбла по живым логам.",
            "",
        ]
    )
    return "\n".join(lines)


def test_precision_at_8_dataset_and_score(tmp_path: Path) -> None:
    dataset = build_precision_dataset()
    committed = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    assert committed["queries"] == dataset["queries"]
    assert committed["documents"] == dataset["documents"]
    assert len(dataset["queries"]) == 50
    assert all(len(query["relevant_source_ids"]) == 8 for query in dataset["queries"])
    engine = _engine(tmp_path, committed)
    try:
        engine.reindex()
        macro, hit_rate, rows = _score(dataset, engine)
    finally:
        engine.close()
    # Регрессия закрытого набора с якорями, не порог релиза контура B.
    assert macro >= 0.8
    assert hit_rate == 1.0
    expected = float(committed["expected_macro_precision_at_8"])
    assert macro == expected
