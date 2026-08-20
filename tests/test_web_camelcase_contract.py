"""Тесты нормализации контрактов camelCase <-> snake_case для параметров веб-студии."""

import pytest
from app.web.schemas import CreateProjectRequest


def test_create_project_request_normalizes_camel_case():
    """Проверяет, что CreateProjectRequest принимает camelCase параметры от UI и нормализует их."""
    raw = {
        "title": "Тестовый проект",
        "aspectRatio": "9:16",
        "imageResolution": "2K",
        "videoResolution": "1080p",
        "imageGenerator": "seedream-4.5",
        "videoGenerator": "kling-1.5",
        "autoMode": True,
    }
    req = CreateProjectRequest.model_validate(raw)
    assert req.title == "Тестовый проект"
    assert req.aspect_ratio == "9:16"
    assert req.image_resolution == "2K"
    assert req.video_resolution == "1080p"
    assert req.image_generator == "seedream-4.5"
    assert req.video_generator == "kling-1.5"
    assert req.auto_mode is True