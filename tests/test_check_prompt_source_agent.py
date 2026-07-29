"""checkPromptSource=agent: готовый агент без промтов прошлой ноды."""

from __future__ import annotations

from pathlib import Path

from app.models import Project, ProjectStatus
from app.services.gpt_operator import (
    assemble_check_agent_prompt,
    patch_operator_config,
    resolve_operator,
)


def _project(tmp_path: Path, monkeypatch) -> Project:
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    xlsx = tmp_path / "data" / "project.xlsx"
    xlsx.parent.mkdir(parents=True)
    xlsx.write_bytes(b"PK" + b"0" * 80)
    p = Project(
        slug="chk-agent",
        topic="t",
        status=ProjectStatus.new,
        hero_mode="no_hero",
        meta={
            "canvas_graph": {
                "nodes": [
                    {"id": "n_plan", "type": "plan", "position": {"x": 0, "y": 0}},
                    {"id": "n_check", "type": "excel_gpt", "position": {"x": 1, "y": 0}},
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "n_plan",
                        "target": "n_check",
                        "data": {"kind": "after"},
                    },
                ],
            },
            "excel_gpt_nodes": {
                "n_check": {
                    "role": "assist",
                    "transport": "api",
                    "checkMode": True,
                    "checkPromptSource": "agent",
                    "takeFromEdges": True,
                }
            },
        },
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    (p.data_dir / "project.xlsx").write_bytes(xlsx.read_bytes())
    return p


def test_resolve_agent_mode_no_source_prompt_error(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    res = resolve_operator(p, "n_check")
    assert res["checkMode"] is True
    assert res["checkPromptSource"] == "agent"
    assert res["checkAgentStep"] == "plan"
    assert res["canRun"] is True
    assert not any("исходного промта" in e for e in (res.get("errors") or []))


def test_assemble_agent_prompt_uses_check_operator(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    text, step = assemble_check_agent_prompt(p, "n_check", check_fix=False)
    assert step == "plan"
    assert "Агент проверки" in text or "агент" in text.lower()
    assert "mode: report_only" in text
    assert "# ОТЧЁТ ПРОВЕРКИ" in text or "verdict:" in text.lower()
    assert "Исходные промты работы" not in text


def test_patch_switches_to_upstream(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    res = patch_operator_config(
        p, "n_check", {"checkPromptSource": "upstream", "transport": "api"}
    )
    assert res["checkPromptSource"] == "upstream"
