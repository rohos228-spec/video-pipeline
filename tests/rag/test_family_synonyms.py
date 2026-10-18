"""Расширение запроса синонимом семейства. Общие слова сильных семейств не трогаем."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.family_synonyms import expand_family_query, partner_table
from app.orchestrator.rag.types import SourceDoc


def test_only_exclusive_family_lemmas_expand() -> None:
    table = partner_table()
    assert table == {
        "освещение": "свет",
        "кастинг": "внешность",
        "частичный": "сборка",
        "слот": "бит",
    }
    assert expand_family_query("где слот") == "где слот бит"
    assert expand_family_query("слот и бит") == "слот и бит"
    assert expand_family_query("сколько битов") == "сколько битов"
    assert expand_family_query("полный кастинг") == "полный кастинг внешность"
    assert expand_family_query("пустая внешность") == "пустая внешность"
    assert expand_family_query("частичная укладка") == "частичная укладка сборка"
    assert expand_family_query("сборка кадров") == "сборка кадров"
    assert expand_family_query("какое освещение") == "какое освещение свет"
    assert expand_family_query("какая локация") == "какая локация"
    assert expand_family_query("крупность и ракурс") == "крупность и ракурс"
    assert expand_family_query("просто дверь") == "просто дверь"


def test_bm25_finds_the_partner_word_and_reranker_keeps_the_original(tmp_path: Path) -> None:
    docs = [
        SourceDoc(
            corpus="prompts",
            source_id="n_look",
            title="Внешность",
            text="Полная внешность человека на весь ролик.",
            part_key="s0",
        ),
        SourceDoc(
            corpus="prompts",
            source_id="n_light",
            title="Свет",
            text="Свет падает сверху на стены.",
            part_key="s0",
        ),
        SourceDoc(
            corpus="prompts",
            source_id="n_slot",
            title="Бит",
            text="Один бит на ячейку.",
            part_key="s0",
        ),
        SourceDoc(
            corpus="prompts",
            source_id="n_asm",
            title="Сборка",
            text="Сборка кадров этого куска.",
            part_key="s0",
        ),
        SourceDoc(
            corpus="prompts",
            source_id="n_other",
            title="Другое",
            text="Только коридор и дверь.",
            part_key="s0",
        ),
    ]

    class _Recording:
        reranker_id = "recording"

        def __init__(self) -> None:
            self.queries: list[str] = []

        def score(self, query: str, passages: list[str]) -> list[float]:
            self.queries.append(query)
            return [0.1] * len(passages)

    recorder = _Recording()
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"prompts": docs, "nodes": [], "rules": [], "history": []},
        scan_git=False,
        reranker=recorder,
    )
    try:
        engine.reindex("prompts")
        look = engine.retrieve("полный кастинг", ["prompts"], top_k=3, channel="bm25", rerank=False)
        light = engine.retrieve("какое освещение", ["prompts"], top_k=3, channel="bm25", rerank=False)
        slot = engine.retrieve("где слот", ["prompts"], top_k=3, channel="bm25", rerank=False)
        asm = engine.retrieve("частичная укладка", ["prompts"], top_k=3, channel="bm25", rerank=False)
        plain = engine.retrieve("какая локация", ["prompts"], top_k=3, channel="bm25", rerank=False)
        engine.retrieve("полный кастинг", ["prompts"], top_k=2, rerank=True, rerank_pool=4)
    finally:
        engine.close()

    assert [chunk.source_id for chunk in look][0] == "n_look"
    assert [chunk.source_id for chunk in light][0] == "n_light"
    assert [chunk.source_id for chunk in slot][0] == "n_slot"
    assert [chunk.source_id for chunk in asm][0] == "n_asm"
    assert "n_light" not in [chunk.source_id for chunk in plain]
    assert "n_other" not in [chunk.source_id for chunk in look[:2]]
    assert recorder.queries == ["полный кастинг"]
