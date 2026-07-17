"""Каждая нода excel_gpt шлёт свой промт из prompt_slot_variants."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.prompt_library import (
    resolve_project_prompt_with_source,
    read_resolved_project_prompt,
)


def test_multi_excel_gpt_nodes_resolve_distinct_slot_prompts(tmp_path: Path) -> None:
    """Три ноды на канвасе → три разных .md, не один prompt_overrides['excel_gpt']."""
    meta = {
        "prompt_slot_variants": {
            "n_excel_gpt_1": {"main": "prompt_a"},
            "n_excel_gpt_2": {"main": "prompt_b"},
            "n_excel_gpt_3": {"main": "prompt_c"},
        }
    }
    # Глобальный override как у первой ноды — раньше все слоты брали его.
    overrides = {"excel_gpt": "prompt_a"}

    with patch(
        "app.services.prompt_library.excel_gpt_prompt_exists",
        side_effect=lambda name: name in {"prompt_a", "prompt_b", "prompt_c"},
    ):
        a, sa = resolve_project_prompt_with_source(
            overrides, "excel_gpt", meta=meta, node_key="n_excel_gpt_1", slot_id="main"
        )
        b, sb = resolve_project_prompt_with_source(
            overrides, "excel_gpt", meta=meta, node_key="n_excel_gpt_2", slot_id="main"
        )
        c, sc = resolve_project_prompt_with_source(
            overrides, "excel_gpt", meta=meta, node_key="n_excel_gpt_3", slot_id="main"
        )

    assert (a, sa) == ("prompt_a", "slot")
    assert (b, sb) == ("prompt_b", "slot")
    assert (c, sc) == ("prompt_c", "slot")


def test_excel_gpt_without_node_key_falls_back_to_override() -> None:
    meta = {
        "prompt_slot_variants": {
            "n_excel_gpt_1": {"main": "prompt_a"},
            "n_excel_gpt_2": {"main": "prompt_b"},
        }
    }
    overrides = {"excel_gpt": "prompt_a"}
    with patch(
        "app.services.prompt_library.excel_gpt_prompt_exists",
        side_effect=lambda name: name in {"prompt_a", "prompt_b"},
    ):
        name, source = resolve_project_prompt_with_source(
            overrides, "excel_gpt", meta=meta
        )
    assert name == "prompt_a"
    assert source == "override"


def test_excel_gpt_node_key_defaults_slot_to_main() -> None:
    meta = {"prompt_slot_variants": {"n_excel_gpt_2": {"main": "only_slot2"}}}
    with patch(
        "app.services.prompt_library.excel_gpt_prompt_exists",
        side_effect=lambda name: name == "only_slot2",
    ):
        name, source = resolve_project_prompt_with_source(
            {"excel_gpt": "prompt_a"},
            "excel_gpt",
            meta=meta,
            node_key="n_excel_gpt_2",
        )
    assert (name, source) == ("only_slot2", "slot")


def test_read_resolved_passes_node_key(tmp_path: Path, monkeypatch) -> None:
    prompts = tmp_path / "prompts" / "05_excel_gpt"
    prompts.mkdir(parents=True)
    (prompts / "alpha.md").write_text("ALPHA", encoding="utf-8")
    (prompts / "beta.md").write_text("BETA", encoding="utf-8")
    monkeypatch.setattr("app.services.prompt_library.PROMPTS_ROOT", tmp_path / "prompts")

    project = SimpleNamespace(
        topic="t",
        prompt_overrides={"excel_gpt": "alpha"},
        meta={
            "prompt_slot_variants": {
                "n_excel_gpt_1": {"main": "alpha"},
                "n_excel_gpt_2": {"main": "beta"},
            }
        },
    )
    name1, _p1, text1, src1 = read_resolved_project_prompt(
        project, "excel_gpt", node_key="n_excel_gpt_1", slot_id="main"
    )
    name2, _p2, text2, src2 = read_resolved_project_prompt(
        project, "excel_gpt", node_key="n_excel_gpt_2", slot_id="main"
    )
    assert name1 == "alpha" and "ALPHA" in text1 and src1 == "slot"
    assert name2 == "beta" and "BETA" in text2 and src2 == "slot"
