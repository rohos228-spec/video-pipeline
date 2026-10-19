"""Запрос с именем ноды поднимает её семейство. Общее слово — нет."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.family_boost import explicit_families
from app.orchestrator.rag.types import SourceDoc


class _PreferWorld:
    reranker_id = "prefer-world"

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [1.2 if "МИР" in passage else 1.0 for passage in passages]


def test_camera_name_is_one_family_and_a_shared_word_is_not() -> None:
    assert explicit_families("где стоит камера") == {"camera"}
    assert explicit_families("план кадра") == set()
    assert "shots" not in explicit_families("план кадра")
    assert "frames" not in explicit_families("план кадра")


def test_assemble_and_world_descriptions_name_one_family() -> None:
    """Описание из реестра нод, не из текста запроса. Общее слово по-прежнему ничьё."""
    assert explicit_families("где правила про локации") == {"world"}
    assert explicit_families("финальный сборщик") == {"assemble"}
    assert "world" not in explicit_families("обычный вариант хроно")
    assert explicit_families("план кадра") == set()
    assert explicit_families("ячейка сцены") == set()


def test_named_family_rises_only_when_the_boost_is_on(tmp_path: Path) -> None:
    docs = [
        SourceDoc(
            corpus="nodes",
            source_id="n_excel_gpt_sd_camera",
            title="Камера",
            text="общий якорь постановки",
            metadata={"node_key": "n_excel_gpt_sd_camera"},
        ),
        SourceDoc(
            corpus="nodes",
            source_id="n_excel_gpt_sd_world",
            title="Мир",
            text="МИР общий якорь места",
            metadata={"node_key": "n_excel_gpt_sd_world"},
        ),
    ]
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"nodes": docs, "prompts": [], "rules": [], "history": []},
        scan_git=False,
        reranker=_PreferWorld(),
    )
    try:
        engine.reindex("nodes")
        plain = engine.retrieve("камера якорь", ["nodes"], top_k=2, rerank=True, family_boost=1)
        boosted = engine.retrieve("камера якорь", ["nodes"], top_k=2, rerank=True, family_boost=1.5)
        unnamed = engine.retrieve("якорь", ["nodes"], top_k=2, rerank=True, family_boost=1.5)
        with pytest.raises(ValueError, match="family_boost"):
            engine.retrieve("камера", ["nodes"], top_k=1, family_boost=0.5)
    finally:
        engine.close()
    assert plain[0].source_id == "n_excel_gpt_sd_world"
    assert boosted[0].source_id == "n_excel_gpt_sd_camera"
    assert unnamed[0].source_id == "n_excel_gpt_sd_world"
