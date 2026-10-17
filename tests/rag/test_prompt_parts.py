"""Промпт режется на 8 частей, не на 3."""

from __future__ import annotations

from app.orchestrator.rag.chunking import split_prompt
from app.orchestrator.rag.loaders import _prompt_docs
from app.orchestrator.rag.node_store import ResolvedNode


def _node(template: str) -> ResolvedNode:
    return ResolvedNode(
        node_key="n_excel_gpt_sd_camera",
        group_key="scene_design_fanout",
        title="GPT: камера",
        status="active",
        prompt_template=template,
        source="git",
        version=1,
        description="постановка кадра",
    )


def test_split_prompt_default_is_eight_parts() -> None:
    text = "\n".join(f"## Секция {index}\nтекст {index}" for index in range(10))
    parts = split_prompt(text)
    assert [key for key, _body in parts] == [f"s{index}" for index in range(8)]
    assert len(split_prompt(text, max_parts=3)) == 3


def test_prompt_loader_emits_eight_parts() -> None:
    text = "\n".join(f"## Секция {index}\nтекст {index}" for index in range(10))
    docs = _prompt_docs(_node(text))
    assert [doc.part_key for doc in docs] == [f"s{index}" for index in range(8)]
