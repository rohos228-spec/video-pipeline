"""Tests for background music resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import Project
from app.services.bgm import find_bgm_file, resolve_bgm
from app.settings import settings


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "bgm_path", None)
    monkeypatch.setattr(settings, "bgm_default_level", 35)

    p = Project(id=1, slug="test", topic="t", meta={})
    p.batch_slug = None
    p.batch_id = None
    data_dir = tmp_path / "data" / "videos" / "test"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(type(p), "data_dir", property(lambda self: data_dir))
    return p


def test_find_bgm_music_mp3_in_project_dir(project: Project) -> None:
    music = project.data_dir / "music.mp3"
    music.write_bytes(b"mp3")
    assert find_bgm_file(project) == music.resolve()


def test_resolve_bgm_auto_when_file_present(project: Project) -> None:
    (project.data_dir / "bgm.mp3").write_bytes(b"x")
    cfg = resolve_bgm(project)
    assert cfg is not None
    assert cfg.path.name == "bgm.mp3"


def test_resolve_bgm_respects_explicit_disable(project: Project) -> None:
    (project.data_dir / "bgm.mp3").write_bytes(b"x")
    project.meta = {"bgm_enabled": False}
    assert resolve_bgm(project) is None


def test_resolve_bgm_missing_file(project: Project) -> None:
    assert resolve_bgm(project) is None
