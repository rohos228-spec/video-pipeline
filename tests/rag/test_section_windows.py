"""Длинные секции сборки и скелета режутся на окна, родительский part_key остаётся."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.rag.chunking import section_windows
from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.loaders import _prompt_docs
from app.orchestrator.rag.node_store import ResolvedNode
from app.orchestrator.rag.store import chunk_id_for
from app.orchestrator.rag.types import SourceDoc


def _node(key: str, template: str) -> ResolvedNode:
    return ResolvedNode(
        node_key=key,
        group_key="scene_design",
        title="Сборка",
        status="active",
        prompt_template=template,
        source="git",
        version=1,
        description="сборка",
    )


def test_real_assemble_and_skeleton_prompts_grow_windows_without_losing_parents() -> None:
    from app.orchestrator.rag.loaders import load_nodes_and_prompts
    from app.project_root import find_project_root

    _nodes, prompts, _skipped = load_nodes_and_prompts(
        repo_root=find_project_root(),
        node_store=None,
        scan_git=True,
    )
    watched = {
        "n_excel_gpt_sd_asm",
        "n_excel_gpt_sd_cd_asm",
        "n_excel_gpt_sd_cd_skeleton",
    }
    for key in watched:
        parents = [doc for doc in prompts if doc.source_id == key and "~" not in doc.part_key]
        assert [doc.part_key for doc in parents] == [f"s{index}" for index in range(len(parents))]
    asm_windows = {
        doc.part_key.split("~", 1)[0]
        for doc in prompts
        if doc.source_id == "n_excel_gpt_sd_asm" and "~" in doc.part_key
    }
    skeleton_windows = {
        doc.part_key.split("~", 1)[0]
        for doc in prompts
        if doc.source_id == "n_excel_gpt_sd_cd_skeleton" and "~" in doc.part_key
    }
    assert asm_windows == {"s5"}
    assert skeleton_windows == {"s6"}
    assert not any(doc.source_id == "n_excel_gpt_sd_cd_asm" and "~" in doc.part_key for doc in prompts)
    assert not any(doc.source_id == "n_excel_gpt_sd_camera" and "~" in doc.part_key for doc in prompts)


def test_short_section_has_no_windows() -> None:
    assert section_windows("короткий абзац") == []


def test_medium_section_has_no_windows() -> None:
    body = "абзац текста\n\n" * 80
    assert 900 < len(body) < 2000
    assert section_windows(body) == []


def test_assemble_and_skeleton_keep_parent_keys_and_add_windows() -> None:
    body = "\n\n".join(f"Абзац {index} " + ("слово " * 40) for index in range(12))
    template = f"## Закон\n{body}"
    assemble = _prompt_docs(_node("n_excel_gpt_sd_asm", template))
    camera = _prompt_docs(_node("n_excel_gpt_sd_camera", template))
    skeleton = _prompt_docs(_node("n_excel_gpt_sd_cd_skeleton", template))
    assert [doc.part_key for doc in camera] == ["s0"]
    assert [doc.part_key for doc in assemble if "~" not in doc.part_key] == ["s0"]
    windows = [doc for doc in assemble if doc.part_key.startswith("s0~")]
    assert len(windows) >= 2
    assert {doc.metadata["parent_part_key"] for doc in windows} == {"s0"}
    assert any(doc.part_key.startswith("s0~") for doc in skeleton)
    parent = next(doc for doc in assemble if doc.part_key == "s0")
    assert parent.metadata.get("parent_part_key") in (None, "")
    assert chunk_id_for("prompts", "n_excel_gpt_sd_asm", "s0")


def test_window_hit_returns_the_parent_chunk_id(tmp_path: Path) -> None:
    parent = SourceDoc(
        corpus="prompts",
        source_id="n_excel_gpt_sd_asm",
        title="Сборка",
        text="Общий закон без редкого слова.",
        part_key="s5",
    )
    child = SourceDoc(
        corpus="prompts",
        source_id="n_excel_gpt_sd_asm",
        title="Сборка",
        text="Здесь лежит уникальное слово крючок.",
        part_key="s5~0",
        metadata={"parent_part_key": "s5"},
    )
    other = SourceDoc(
        corpus="prompts",
        source_id="n_other",
        title="Другое",
        text="Совсем другой текст про двор.",
        part_key="s0",
    )
    engine = RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"prompts": [parent, child, other], "nodes": [], "rules": [], "history": []},
        scan_git=False,
    )
    try:
        engine.reindex("prompts")
        found = engine.retrieve(
            "крючок",
            ["prompts"],
            top_k=3,
            channel="bm25",
            rerank=False,
            aggregate="expand_tail",
        )
    finally:
        engine.close()
    assert found
    assert found[0].chunk_id == chunk_id_for("prompts", "n_excel_gpt_sd_asm", "s5")
    assert found[0].metadata.get("part_key") == "s5"
    assert all("~" not in str(chunk.metadata.get("part_key")) for chunk in found)
