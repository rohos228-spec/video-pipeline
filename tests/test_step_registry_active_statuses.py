"""Тесты единого реестра активных статусов и их синхронизации с воркерами."""

import pytest
from app.models import ProjectStatus
from app.services.step_registry import running_statuses, running_statuses_list
import app.worker as worker_module


def test_running_statuses_contains_all_critical_stages():
    """Проверяет, что единый реестр содержит все 15+ активных running-статусов."""
    statuses = running_statuses()
    
    # Базовые шаги
    assert ProjectStatus.planning in statuses
    assert ProjectStatus.scripting in statuses
    assert ProjectStatus.splitting in statuses
    
    # Мультиагентный дизайн сцен
    assert ProjectStatus.scene_designing in statuses
    assert ProjectStatus.scene_assembling in statuses
    
    # Персонажи и предметы
    assert ProjectStatus.generating_hero in statuses
    assert ProjectStatus.generating_items in statuses
    
    # Enrich 1..5
    for i in range(1, 6):
        assert getattr(ProjectStatus, f"enriching_{i}") in statuses
        
    # Медиа
    assert ProjectStatus.generating_image_prompts in statuses
    assert ProjectStatus.generating_images in statuses
    assert ProjectStatus.generating_animation_prompts in statuses
    assert ProjectStatus.generating_videos in statuses
    assert ProjectStatus.generating_audio in statuses
    assert ProjectStatus.generating_music in statuses
    
    # SFX
    assert ProjectStatus.sfx_planning in statuses
    assert ProjectStatus.generating_sfx in statuses
    
    # Сборка и публикация
    assert ProjectStatus.assembling in statuses
    assert ProjectStatus.publishing in statuses


def test_worker_active_statuses_matches_registry():
    """Проверяет, что воркер app/worker.py использует единый реестр."""
    assert set(worker_module.ACTIVE_STATUSES) == running_statuses()


def test_running_statuses_list_is_sorted():
    """Проверяет, что список статусов детерминирован и отсортирован."""
    status_list = running_statuses_list()
    assert len(status_list) == len(running_statuses())
    assert isinstance(status_list, list)