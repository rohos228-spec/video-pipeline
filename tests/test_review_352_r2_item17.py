"""Повторное ревью #352 №17: параллель scene_design как на main."""

from __future__ import annotations

from pathlib import Path


def test_scene_design_semaphores_use_settings_limits() -> None:
    text = Path("app/services/scene_design/runner.py").read_text(encoding="utf-8")
    assert "Semaphore(max(1, len(batches)))" not in text
    assert "Semaphore(64)" not in text
    assert "asyncio.Semaphore(parallel)" in text
    assert (
        "asyncio.Semaphore(max(1, int(settings.scene_design_max_parallel)))" in text
    )
    assert "await asyncio.sleep((i - 1) * 5.0)  # 2026-10-11: 5s stagger" not in text
