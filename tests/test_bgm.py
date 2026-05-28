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
    monkeypatch.setattr(settings, "bgm_path", tmp_path / "assets" / "default.mp3")
    monkeypatch.setattr(settings, "bgm_default_enabled", True)
    monkeypatch.setattr(settings, "bgm_default_level", 25)

    p = Project(id=1, slug="test", topic="t", meta={})
    p.batch_slug = None
    p.batch_id = None
    data_dir = tmp_path / "data" / "videos" / "test"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(type(p), "data_dir", property(lambda self: data_dir))
    return p


def test_resolve_bgm_uses_project_local_file(project: Project) -> None:
    local = project.data_dir / "bgm.mp3"
    local.write_bytes(b"mp3")
    cfg = resolve_bgm(project)
    assert cfg is not None
    assert cfg.path == local
    assert cfg.level == 0.25


def test_resolve_bgm_respects_disabled_flag(project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default = tmp_path / "assets" / "default.mp3"
    default.parent.mkdir(parents=True, exist_ok=True)
    default.write_bytes(b"mp3")
    monkeypatch.setattr(settings, "bgm_path", default)
    project.meta = {"bgm_enabled": False}
    assert resolve_bgm(project) is None


def test_resolve_bgm_mass_level(project: Project) -> None:
    (project.data_dir / "bgm.mp3").write_bytes(b"x")
    project.meta = {"mass_bgm_enabled": True, "mass_bgm_level": 50}
    cfg = resolve_bgm(project)
    assert cfg is not None
    assert cfg.level == 0.5


def test_resolve_bgm_ignores_mass_disabled_on_single_project(project: Project, tmp_path: Path) -> None:
    """Одиночный проект: mass_bgm_enabled=false не должен глушить BGM."""
    settings.bgm_path.parent.mkdir(parents=True, exist_ok=True)
    settings.bgm_path.write_bytes(b"default")
    project.meta = {"mass_bgm_enabled": False}
    project.batch_id = None
    cfg = resolve_bgm(project)
    assert cfg is not None


def test_resolve_bgm_falls_back_to_default_asset(project: Project, tmp_path: Path) -> None:
    settings.bgm_path.parent.mkdir(parents=True, exist_ok=True)
    settings.bgm_path.write_bytes(b"default")
    cfg = resolve_bgm(project)
    assert cfg is not None
    assert cfg.path == settings.bgm_path
