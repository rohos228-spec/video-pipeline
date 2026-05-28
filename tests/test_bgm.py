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


def test_find_bgm_in_music_folder(project: Project) -> None:
    music_dir = project.data_dir / "music"
    music_dir.mkdir()
    track = music_dir / "track.mp3"
    track.write_bytes(b"mp3")
    assert find_bgm_file(project) == track.resolve()


def test_resolve_bgm_from_music_folder(project: Project) -> None:
    music_dir = project.data_dir / "music"
    music_dir.mkdir()
    (music_dir / "fon.mp3").write_bytes(b"x")
    cfg = resolve_bgm(project)
    assert cfg is not None
    assert cfg.path.parent.name == "music"


def test_resolve_bgm_respects_explicit_disable(project: Project) -> None:
    music_dir = project.data_dir / "music"
    music_dir.mkdir()
    (music_dir / "a.mp3").write_bytes(b"x")
    project.meta = {"bgm_enabled": False}
    assert resolve_bgm(project) is None
