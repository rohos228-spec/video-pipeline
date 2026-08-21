"""Тесты создания багрепортов в каталоге docs/bug-reports/."""

import pytest
from pathlib import Path
from app.services.bug_report import bugs_dir, write_bug_report


def test_bugs_dir_uses_docs_bug_reports():
    """Проверяет, что bugs_dir() возвращает путь внутри docs/bug-reports."""
    d = bugs_dir()
    assert "docs" in str(d) or "bug-reports" in str(d)
    assert d.is_dir()


def test_write_bug_report_writes_markdown_file():
    """Проверяет корректность формирования и сохранения файла багрепорта."""
    res = write_bug_report(
        description="Тестовое описание ошибки от оператора",
        minutes=5,
        project_id=42,
        project_slug="test-slug",
        studio_version="1.6.0",
    )
    assert res.get("ok") is True
    assert "filename" in res
    assert "rel" in res
    assert "docs/bug-reports" in res["rel"] or "bug-reports" in res["rel"]

    path = Path(res["path"])
    assert path.is_file()
    content = path.read_text(encoding="utf-8")
    assert "Тестовое описание ошибки" in content
    assert "project_id: 42" in content
    assert "project_slug: test-slug" in content
    path.unlink(missing_ok=True)