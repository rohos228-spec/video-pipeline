"""№28: у рельса и сайдбара снова есть точки входа."""

from __future__ import annotations

from pathlib import Path


def test_topbar_restores_logs_chat_base_fleet_and_bug() -> None:
    text = Path("web/src/components/shell/topbar.tsx").read_text(encoding="utf-8")
    assert "setLogsOpen(true)" in text
    assert "studio-open-gpt" in text
    assert "studio-open-baza" in text
    assert "studio-open-fleet" in text
    assert "<BugReportButton" in text
    assert 'label="Логи"' in text
    assert 'label="Чат"' in text
    assert 'label="База"' in text
    assert 'label="Сеть"' in text


def test_sidebar_renders_new_project_wizard() -> None:
    text = Path("web/src/components/sidebar/project-sidebar.tsx").read_text(encoding="utf-8")
    assert "<NewProjectWizard" in text
    assert 'aria-label="Новый проект"' in text
    assert "folderId={activeFolderId}" in text
