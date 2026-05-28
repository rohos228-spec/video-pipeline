"""Tests for background music resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import Project
from app.services.bgm import resolve_bgm
from app.settings import settings


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "bgm_path", None)
    monkeypatch.setattr(settings, "bgm_default_enabled", False)
    monkeypatch.setattr(settings, "bgm_default_level", 30)

    p = Project(id=1, slug="test", topic="t", meta={})
    p.batch_slug = None
    p.batch_id = None
    data_dir = tmp_path / "data" / "videos" / "test"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(type(p), "data_dir", property(lambda self: data_dir))
    return p


def test_resolve_bgm_uses_project_local_file(project: Project) -> None:
    project.meta = {"bgm_enabled": True}
    local = project.data_dir / "bgm.mp3"
    local.write_bytes(b"mp3")
    cfg = resolve_bgm(project)
    assert cfg is not None
    assert cfg.path == local


def test_resolve_bgm_off_by_default_without_file(project: Project) -> None:
    assert resolve_bgm(project) is None


def test_resolve_bgm_mass_level(project: Project) -> None:
    (project.data_dir / "bgm.mp3").write_bytes(b"x")
    project.meta = {"mass_bgm_enabled": True, "mass_bgm_level": 50}
    project.batch_id = 99
    cfg = resolve_bgm(project)
    assert cfg is not None
    assert cfg.level == 0.5
