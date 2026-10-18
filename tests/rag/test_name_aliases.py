"""Алиасы имён людей и мест дописываются в FTS, не в текст запроса."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.rag.aliases import alias_groups, alias_suffix
from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.types import SourceDoc


def test_identity_lines_link_character_and_place_names() -> None:
    table = alias_groups(
        [
            "Если тот же человек назван по-разному («Павел» / «механик») — это одна сущность, не две.",
            "Кореференция («механик»=«Павел»=«он») — один id на весь ролик.",
            "Одно место («кухня» / «столовая») — не два номера.",
            "Не чередуй «сок» / «жидкость» / «раствор» для одной вещи.",
            "Возрастная вилка (например «около 45» / «подросток 14–16»).",
        ]
    )
    assert table["павел"] == frozenset({"механик"})
    assert table["механик"] == frozenset({"павел"})
    assert "он" not in table
    assert table["кухня"] == frozenset({"столовая"})
    assert "сок" not in table
    assert "подросток" not in table


def test_suffix_adds_only_the_missing_alias() -> None:
    table = alias_groups(
        ["(«Павел» / «механик») — это одна сущность, не две."]
    )
    assert alias_suffix("В кадре Павел открывает дверь.", table) == "механик"
    assert alias_suffix("Павел, он же механик.", table) == ""
    assert alias_suffix("Пустая комната.", table) == ""


def test_bm25_finds_a_chunk_by_the_other_name(tmp_path: Path) -> None:
    docs = [
        SourceDoc(
            corpus="prompts",
            source_id="n_names",
            title="Имена",
            text="(«Павел» / «механик») — это одна сущность, не две.",
            part_key="s0",
        ),
        SourceDoc(
            corpus="prompts",
            source_id="n_scene",
            title="Сцена",
            text="В кадре Павел открывает дверь.",
            part_key="s1",
        ),
        SourceDoc(
            corpus="prompts",
            source_id="n_other",
            title="Другое",
            text="В кадре только дверь и коридор.",
            part_key="s0",
        ),
    ]
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"prompts": docs, "nodes": [], "rules": [], "history": []},
        scan_git=False,
    )
    try:
        engine.reindex("prompts")
        found = engine.retrieve("где механик", ["prompts"], top_k=3, channel="bm25", rerank=False)
    finally:
        engine.close()
    ids = [chunk.source_id for chunk in found]
    assert "n_scene" in ids
    assert "n_other" not in ids[:2]


def test_corpus_aliases_are_character_and_place_names_not_things() -> None:
    from app.orchestrator.rag.loaders import load_nodes_and_prompts
    from app.project_root import find_project_root

    _nodes, prompts, _skipped = load_nodes_and_prompts(
        repo_root=find_project_root(),
        node_store=None,
        scan_git=True,
    )
    table = alias_groups([doc.text for doc in prompts])
    assert "павел" in table["механик"]
    assert "сок" not in table
    assert "жидкость" not in table
