"""Тесты защиты от сброса статуса в new при отмене/ошибке шага (Cancel Rollback Guard)."""

import pytest
from app.models import ProjectStatus
from app.telegram.menu import step_by_running_status, step_by_code
from app.services.step_registry import running_statuses


def test_step_by_running_status_never_returns_none_for_active_statuses():
    """Проверяет, что для каждого активного статуса step_by_running_status находит StepDef.
    Если функция возвращает None, откат сбрасывает статус проекта в ProjectStatus.new,
    что является критическим багом."""
    for st in running_statuses():
        sd = step_by_running_status(st)
        assert sd is not None, f"step_by_running_status returned None for active status {st!r}!"
        if sd.code != "plan":
            assert sd.requires is not None, f"requires is None for StepDef {sd.code}!"
            assert sd.requires != ProjectStatus.new, f"requires is ProjectStatus.new for {sd.code}!"


def test_sfx_substeps_rollback_targets():
    """Проверяет точные цели отката для шагов SFX."""
    sd_plan = step_by_running_status(ProjectStatus.sfx_planning)
    assert sd_plan is not None
    assert sd_plan.code == "sfx_plan"
    assert sd_plan.requires == ProjectStatus.music_ready

    sd_gen = step_by_running_status(ProjectStatus.generating_sfx)
    assert sd_gen is not None
    assert sd_gen.code == "sfx_gen"
    assert sd_gen.requires == ProjectStatus.sfx_plan_ready


def test_scene_design_substeps_lookup():
    """Проверяет регистрацию и поиск агентов scene_design."""
    sd_skel = step_by_code("sd_skel")
    assert sd_skel is not None
    assert sd_skel.running_status == ProjectStatus.scene_designing
    assert sd_skel.requires == ProjectStatus.frames_ready