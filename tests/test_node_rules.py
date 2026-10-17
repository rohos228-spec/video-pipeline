"""Реестр правил группы script_frames_qc: ссылки живые, документ свежий."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from app.services.node_rules import (
    FIELD_WRITERS,
    NODE_FLOW,
    PASS_RULES,
    PLAN_FIELD_RULES,
    PLAN_ISSUE_RULES,
    PROMPT_DIR,
    RULES,
    RULES_BY_ID,
    flow_mermaid,
    node_label,
    node_local_key,
    rules_markdown,
)

ROOT = Path(__file__).resolve().parents[1]


def test_rule_ids_unique_and_filled() -> None:
    assert len(RULES_BY_ID) == len(RULES)
    for r in RULES:
        assert r.id.startswith("R-")
        assert r.title and r.plain and r.action and r.where, r.id


def test_every_referenced_rule_exists() -> None:
    refs = list(PASS_RULES.values()) + list(PLAN_ISSUE_RULES.values())
    refs += list(PLAN_FIELD_RULES.values())
    for flow in NODE_FLOW:
        refs += list(flow.code_rules)
    for _field, writers in FIELD_WRITERS:
        refs += [w.split()[0] for w in writers if w.startswith("R-")]
    missing = sorted({r for r in refs if r not in RULES_BY_ID})
    assert not missing


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.id)
def test_where_points_to_real_code_or_prompt(rule) -> None:
    for where in rule.where:
        if where.startswith("prompt:"):
            assert (ROOT / where.split(":", 1)[1]).is_file(), where
            continue
        mod_name, _, attr_path = where.partition(":")
        obj = importlib.import_module(mod_name)
        for part in attr_path.split("."):
            obj = getattr(obj, part)
        assert callable(obj), where


def test_every_scene_plan_issue_kind_is_mapped() -> None:
    src = (ROOT / "app/services/scene_plan.py").read_text(encoding="utf-8")
    kinds = set(re.findall(r'_issue\(\s*[^,]+,\s*"([^"]+)"', src))
    assert kinds, "не нашли _issue(...) в scene_plan.py"
    assert kinds <= set(PLAN_ISSUE_RULES), kinds - set(PLAN_ISSUE_RULES)


def test_every_code_pass_has_rule() -> None:
    src = (ROOT / "app/services/apply_ops_batches.py").read_text(encoding="utf-8")
    passes = set(re.findall(r'_traced\(\s*"([a-z_]+)"', src))
    assert passes and passes <= set(PASS_RULES)


def test_flow_loop_and_labels() -> None:
    keys = [f.key for f in NODE_FLOW]
    assert keys == ["boundaries", "script", "action", "shots", "qc", "report"]
    assert "script --> action" in flow_mermaid()
    assert node_local_key("n_excel_gpt_fw_shots") == "shots"
    assert node_label("n_excel_gpt_fw_script") == "Каркас"
    assert node_label("n_excel_gpt_fw_action") == "Площадка"
    assert node_label("n_excel_gpt_fw_shots") == "Шоты"
    by_key = {f.key: f for f in NODE_FLOW}
    assert by_key["script"].prompt == "prompts/scene_design/scene_skeleton_agent.md"
    assert by_key["action"].prompt == "prompts/scene_design/action.md"
    assert by_key["shots"].prompt == "scenes_to_frames_ru.md"
    for flow in NODE_FLOW:
        if not flow.prompt:
            continue
        rel = flow.prompt if "/" in flow.prompt else f"{PROMPT_DIR}/{flow.prompt}"
        assert (ROOT / rel).is_file(), rel


def test_rules_doc_is_up_to_date() -> None:
    doc = ROOT / "docs" / "NODE_GROUP_RULES.md"
    assert doc.read_text(encoding="utf-8") == rules_markdown(), (
        "перегенерируйте: python3 scripts/node_rules_doc.py"
    )
