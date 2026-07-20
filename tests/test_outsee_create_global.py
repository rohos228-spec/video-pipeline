"""Глобальные settings Outsee Create (не project-scoped)."""

from __future__ import annotations

from pathlib import Path

from app.web.routers import outsee_create as oc


def test_default_settings_keys(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(oc.settings, "data_dir", tmp_path)
    s = oc._load_settings()
    assert s["media_type"] == "image"
    assert s["image_slug"] == "gpt-image-2"
    assert s["video_slug"] == "kling-3-0"
    assert s["audio_slug"] == "suno-5-5"
    assert "prompt" in s


def test_settings_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(oc.settings, "data_dir", tmp_path)
    saved = oc._save_settings(
        {
            "media_type": "video",
            "video_slug": "kling-2-6",
            "aspect": "9:16",
            "duration": "10",
        }
    )
    assert saved["media_type"] == "video"
    assert saved["video_slug"] == "kling-2-6"
    assert saved["aspect"] == "9:16"
    again = oc._load_settings()
    assert again["video_slug"] == "kling-2-6"
    assert again["duration"] == "10"
    assert (tmp_path / "outsee_create_settings.json").is_file()
