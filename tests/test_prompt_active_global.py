"""Тесты глобально активных промтов."""

from __future__ import annotations

import pytest

from app.services.prompt_active_global import (
    get_global_active,
    load_global_active,
    set_global_active,
)
from app.services.prompt_library import resolve_project_prompt_name, write_prompt
from tests.conftest import patch_prompt_roots


@pytest.fixture
def plan_step(tmp_path, monkeypatch):
    patch_prompt_roots(monkeypatch, tmp_path, folders=("01_plan",))
    return "plan"


def test_set_and_get_global_active(plan_step):
    write_prompt(plan_step, "shared", "text")
    set_global_active(plan_step, "shared")
    assert get_global_active(plan_step) == "shared"
    assert load_global_active()[plan_step] == "shared"


def test_resolve_uses_global_when_no_project_override(plan_step):
    write_prompt(plan_step, "global_v", "x")
    set_global_active(plan_step, "global_v")
    name = resolve_project_prompt_name({}, plan_step, meta={})
    assert name == "global_v"
