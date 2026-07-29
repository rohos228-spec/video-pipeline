"""Редактируемый шаблон формата отчёта проверки (checkReportFormat)."""

from __future__ import annotations

from pathlib import Path

from app.models import Project
from app.services.check_analysis import (
    TXT_REPORT_FOOTER,
    append_txt_report_footer,
    default_check_report_format,
)
from app.services.gpt_operator import (
    assemble_check_agent_prompt,
    patch_operator_config,
    resolve_check_report_format,
    resolve_operator,
    save_check_agent_file,
)


def _project(tmp_path: Path, monkeypatch) -> Project:
    data = tmp_path / "data"
    monkeypatch.setattr("app.models.settings.data_dir", data)
    p = Project(id=1, topic="t", slug="fmt")
    monkeypatch.setattr(type(p), "data_dir", property(lambda self: data / "videos" / "fmt"))
    p.data_dir.mkdir(parents=True)
    (p.data_dir / "project.xlsx").write_bytes(b"PK" + b"\x00" * 100)
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_plan", "type": "plan"},
                {"id": "n_check", "type": "excel_gpt"},
            ],
            "edges": [{"id": "e1", "source": "n_plan", "target": "n_check"}],
        },
        "excel_gpt_nodes": {
            "n_check": {"checkMode": True, "checkPromptSource": "agent", "role": "review"},
        },
    }
    return p


def test_default_format_matches_footer() -> None:
    assert default_check_report_format() == TXT_REPORT_FOOTER
    assert "verdict:" in default_check_report_format()


def test_append_custom_format_overrides_default() -> None:
    custom = "# МОЙ ОТЧЁТ\nverdict: pass|fail\n## summary\nкратко"
    out = append_txt_report_footer("критерии агента", report_format=custom)
    assert "критерии агента" in out
    assert "# МОЙ ОТЧЁТ" in out
    assert "шаблон этой ноды" in out
    assert TXT_REPORT_FOOTER not in out or "# МОЙ ОТЧЁТ" in out


def test_patch_and_resolve_check_report_format(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    custom = "# ОТЧЁТ\nverdict: pass|fail\n## summary\nмой формат"
    resolve = patch_operator_config(p, "n_check", {"checkReportFormat": custom})
    assert resolve["checkReportFormatCustom"] is True
    assert "мой формат" in resolve["checkReportFormat"]
    text, is_custom = resolve_check_report_format(p, "n_check")
    assert is_custom is True
    assert "мой формат" in text

    resolve2 = patch_operator_config(p, "n_check", {"checkReportFormat": None})
    assert resolve2["checkReportFormatCustom"] is False
    assert resolve2["checkReportFormat"] == TXT_REPORT_FOOTER


def test_assemble_agent_uses_custom_format(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    save_check_agent_file(
        p,
        "n_check",
        original_name="a.txt",
        content="Ты проверяешь план. pass если есть хук.".encode("utf-8"),
    )
    patch_operator_config(
        p,
        "n_check",
        {
            "checkReportFormat": "# SHORT\nverdict: pass|fail\n## summary\nok",
            "checkPromptSource": "agent",
        },
    )
    text, label = assemble_check_agent_prompt(p, "n_check", check_fix=False)
    assert label and label.startswith("upload:")
    assert "# SHORT" in text
    assert "verdict: pass|fail" in text
    assert "Ты проверяешь план" in text
