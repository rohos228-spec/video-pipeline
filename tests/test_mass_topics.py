"""Тесты парсера тем из Excel."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from app.storage.mass_topics import parse_topics_xlsx


def test_parse_flexible_first_column(tmp_path: Path) -> None:
    path = tmp_path / "topics.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Тема A"])
    ws.append(["Тема B"])
    ws.append([""])
    ws.append(["Тема C"])
    wb.save(path)
    assert parse_topics_xlsx(path) == ["Тема A", "Тема B", "Тема C"]


def test_parse_header_named_topic_column(tmp_path: Path) -> None:
    path = tmp_path / "topics.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["id", "Название", "note"])
    ws.append([1, "Видео один", "x"])
    ws.append([2, "Видео два", "y"])
    wb.save(path)
    assert parse_topics_xlsx(path) == ["Видео один", "Видео два"]
