"""Recover job всегда force_replace — кнопка должна заменять кадры."""

from __future__ import annotations

import inspect

from app.services import montage_outsee_recover_job as job_mod


def test_recover_job_always_force_replace() -> None:
    src = inspect.getsource(job_mod.spawn_recover_job)
    assert "force_replace=True" in src
    assert "recover_montage_images_from_outsee" in src
    # Вызов с False не должен быть (комментарии про старый False — ок).
    assert "force_replace=False," not in src
    assert "force_replace=False)" not in src
