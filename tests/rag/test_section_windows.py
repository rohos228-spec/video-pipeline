"""Окна секций сборки и скелета в индекс не попадают.

Прогоны dev-v2 с окнами снижали nDCG сильных семейств (камера, действие,
шоты, сценарий) или hit@5 целиком. Родительские part_key s0–s7 остаются.
"""

from __future__ import annotations

from app.orchestrator.rag.loaders import load_nodes_and_prompts
from app.project_root import find_project_root


def test_prompt_index_has_no_section_windows() -> None:
    _nodes, prompts, _skipped = load_nodes_and_prompts(
        repo_root=find_project_root(),
        node_store=None,
        scan_git=True,
    )
    assert prompts
    assert all("~" not in doc.part_key for doc in prompts)
    for key in ("n_excel_gpt_sd_asm", "n_excel_gpt_sd_cd_skeleton", "n_excel_gpt_sd_camera"):
        parents = [doc.part_key for doc in prompts if doc.source_id == key]
        assert parents
        assert all(part.startswith("s") and "~" not in part for part in parents)
