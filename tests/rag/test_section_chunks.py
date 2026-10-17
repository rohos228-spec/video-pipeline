"""Секции промпта с префиксом и агрегация по source_id. Веса не качаются."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.rag.chunking import estimate_tokens, section_prefix, split_section_windows
from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.loaders import _node_section_docs, _prompt_docs
from app.orchestrator.rag.types import SourceDoc
from tests.rag.test_group_role_rerank import _git


def test_long_section_is_windowed_with_overlap_and_prefix() -> None:
    paragraph = "Камера держит общий план и не телепортирует героя. " * 80
    body = "## Крупность\n\n" + paragraph
    prefix = section_prefix(
        node_key="n_excel_gpt_sd_camera",
        title="GPT: камера",
        role="постановка кадра",
        heading="Крупность",
    )
    windows = split_section_windows(body, prefix=prefix)
    assert len(windows) >= 2
    for window in windows:
        assert window.startswith("Нода n_excel_gpt_sd_camera.")
        assert "Название: GPT: камера." in window
        assert "Роль: постановка кадра." in window
        assert "Секция: Крупность." in window
        assert estimate_tokens(window) <= 512
    assert windows[0][-80:] in windows[1] or windows[0].split("\n\n")[-1][:40] in windows[1]


def test_prompt_sections_keep_role_and_do_not_merge_headings() -> None:
    template = "\n".join(
        [
            "## Роль",
            "",
            "Собираешь чужие срезы и не придумываешь сцену заново.",
            "",
            "## Формат",
            "",
            "Только JSON.",
        ]
    )
    node = _git("n_excel_gpt_sd_asm", "GPT: сборка сцен", "сборщик сцен")
    node.prompt_template = template
    prompts = _prompt_docs(node)
    headings = [doc.metadata["section"] for doc in prompts]
    assert headings == ["Роль", "Формат"]
    assert all(doc.part_key.startswith("s") for doc in prompts)
    assert "n_excel_gpt_sd_asm" in prompts[0].text
    assert "GPT: сборка сцен" in prompts[0].text
    assert "позиция" in prompts[0].text
    assert "Собираешь чужие срезы" in prompts[0].text
    cards = _node_section_docs(node)
    assert [doc.part_key for doc in cards] == ["n0", "n1"]
    assert cards[0].corpus == "nodes"
    assert "Секция: Роль." in cards[0].text


def test_source_aggregate_caps_one_document(tmp_path: Path) -> None:
    docs = []
    for index in range(6):
        docs.append(
            SourceDoc(
                corpus="prompts",
                source_id="n_alpha",
                title="альфа",
                text=f"общий якорь альфа секция {index}",
                part_key=f"s{index}",
                metadata={"node_key": "n_alpha", "part_key": f"s{index}"},
            )
        )
    docs.append(
        SourceDoc(
            corpus="prompts",
            source_id="n_beta",
            title="бета",
            text="общий якорь бета другой документ",
            part_key="s0",
            metadata={"node_key": "n_beta", "part_key": "s0"},
        )
    )
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"prompts": docs, "nodes": [], "rules": [], "history": []},
        scan_git=False,
    )
    try:
        engine.reindex("prompts")
        plain = engine.retrieve("общий якорь альфа", ["prompts"], top_k=4, rerank=False)
        capped = engine.retrieve(
            "общий якорь альфа",
            ["prompts"],
            top_k=4,
            rerank=False,
            source_aggregate="sum2",
            source_cap=2,
        )
    finally:
        engine.close()
    assert sum(1 for chunk in plain if chunk.source_id == "n_alpha") >= 3
    assert sum(1 for chunk in capped if chunk.source_id == "n_alpha") <= 2
    assert any(chunk.source_id == "n_beta" for chunk in capped)
