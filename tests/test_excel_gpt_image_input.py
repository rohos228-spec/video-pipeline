"""Работа с GPT: изображение / рефы / режимы без ожидания xlsx."""

from __future__ import annotations

from pathlib import Path

from app.models import Project, ProjectStatus
from app.services.excel_gpt_node import (
    attachment_paths,
    display_attachment_name,
    expects_xlsx_result,
    input_source,
    is_allowed_upload_filename,
    save_gpt_reply_text,
    work_mode,
)


def _project(tmp_path: Path, monkeypatch) -> Project:
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(
        slug="gpt-img",
        topic="t",
        status=ProjectStatus.new,
        hero_mode="no_hero",
        meta={},
    )
    p.data_dir.mkdir(parents=True)
    return p


def test_allowed_upload_includes_images() -> None:
    assert is_allowed_upload_filename("a.png")
    assert is_allowed_upload_filename("b.JPG")
    assert is_allowed_upload_filename("c.xlsx")
    assert not is_allowed_upload_filename("d.exe")


def test_image_source_attachments_and_no_xlsx_expectation(
    tmp_path: Path, monkeypatch
) -> None:
    p = _project(tmp_path, monkeypatch)
    key = "n_excel_gpt_1"
    up = p.data_dir / "excel_gpt_uploads" / key
    up.mkdir(parents=True)
    img = up / "face.png"
    img.write_bytes(b"\x89PNG" + b"0" * 400)
    p.meta = {
        "excel_gpt_nodes": {
            key: {
                "inputSource": "image",
                "uploadedFileName": "face.png",
                "workMode": "transform",
            }
        }
    }
    assert input_source(p, key) == "image"
    assert work_mode(p, key) == "transform"
    assert expects_xlsx_result(p, key) is False
    paths = attachment_paths(p, key)
    assert paths == [img]
    assert "face.png" in display_attachment_name(p, key)


def test_hero_refs_collect_characters(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    chars = p.data_dir / "characters"
    chars.mkdir(parents=True)
    (chars / "c01.png").write_bytes(b"\x89PNG" + b"1" * 300)
    (chars / "c02.jpg").write_bytes(b"\xff\xd8" + b"2" * 300)
    key = "n_excel_gpt_2"
    p.meta = {
        "excel_gpt_nodes": {
            key: {"inputSource": "hero_refs", "workMode": "review"}
        }
    }
    assert work_mode(p, key) == "review"
    assert expects_xlsx_result(p, key) is False
    names = {x.name for x in attachment_paths(p, key)}
    assert names == {"c01.png", "c02.jpg"}


def test_project_xlsx_still_expects_xlsx(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    (p.data_dir / "project.xlsx").write_bytes(b"PK" + b"0" * 200)
    key = "n_excel_gpt_1"
    p.meta = {
        "excel_gpt_nodes": {
            key: {"inputSource": "project_xlsx", "workMode": "assist"}
        }
    }
    assert expects_xlsx_result(p, key) is True
    assert attachment_paths(p, key)[0].name == "project.xlsx"


def test_save_gpt_reply_text(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    key = "n_excel_gpt_1"
    p.meta = {"excel_gpt_nodes": {key: {"inputSource": "image"}}}
    path = save_gpt_reply_text(p, key, "Вердикт: ок")
    assert path is not None and path.is_file()
    assert "Вердикт" in path.read_text(encoding="utf-8")
    cfg = (p.meta or {}).get("excel_gpt_nodes", {}).get(key) or {}
    assert cfg.get("lastReplyPath", "").endswith("gpt_reply.txt")
