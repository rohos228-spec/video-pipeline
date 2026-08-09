"""Просмотр агента проверки: upload или builtin."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.gpt_operator import load_check_agent_view


def test_load_check_agent_view_upload(tmp_path: Path) -> None:
    node_key = "n_check"
    upload = tmp_path / "excel_gpt_uploads" / node_key
    upload.mkdir(parents=True)
    (upload / "check_agent.txt").write_text("CUSTOM AGENT BODY", encoding="utf-8")
    project = SimpleNamespace(
        id=1,
        data_dir=tmp_path,
        meta={
            "gpt_operator": {
                node_key: {
                    "checkMode": True,
                    "checkPromptSource": "agent",
                    "checkAgentFileName": "check_agent.txt",
                }
            }
        },
    )
    view = load_check_agent_view(project, node_key)  # type: ignore[arg-type]
    assert view is not None
    assert view["source"] == "upload"
    assert "CUSTOM AGENT BODY" in view["text"]


def test_load_check_agent_view_builtin(tmp_path: Path) -> None:
    project = SimpleNamespace(
        id=1,
        data_dir=tmp_path,
        meta={
            "gpt_operator": {
                "n_check": {
                    "checkMode": True,
                    "checkPromptSource": "agent",
                }
            },
            "workflow_graph": {
                "nodes": [
                    {"id": "n_img", "type": "images"},
                    {"id": "n_check", "type": "excel_gpt"},
                ],
                "edges": [{"source": "n_img", "target": "n_check"}],
            },
        },
    )
    with patch(
        "app.services.gpt_operator.upstream_node_type_for_check",
        return_value="images",
    ), patch(
        "app.services.check_analysis.load_check_operator_prompt_body",
        return_value="BUILTIN CHECK BODY",
    ), patch(
        "app.services.check_analysis.resolve_check_operator_step",
        return_value="img",
    ):
        view = load_check_agent_view(project, "n_check")  # type: ignore[arg-type]
    assert view is not None
    assert view["source"] == "builtin"
    assert view["fileName"] == "builtin:img"
    assert "BUILTIN CHECK BODY" in view["text"]
