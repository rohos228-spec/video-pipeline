"""Общие фикстуры для тестов промтов (overlay data/prompts/)."""

from __future__ import annotations

from pathlib import Path

import pytest


def patch_prompt_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    *,
    folders: tuple[str, ...] = ("01_plan",),
) -> tuple[Path, Path]:
    """Подменить bundled + user roots; создать каталоги шагов."""
    bundled = tmp_path / "bundled_prompts"
    user = tmp_path / "user_prompts"
    bundled.mkdir()
    user.mkdir()
    for folder in folders:
        (user / folder).mkdir(parents=True, exist_ok=True)
        (bundled / folder).mkdir(parents=True, exist_ok=True)

    def _user_root() -> Path:
        return user

    for mod in (
        "app.services.prompt_paths",
        "app.services.prompt_library",
        "app.services.prompt_active_global",
        "app.services.gpt_verdict_review",
    ):
        monkeypatch.setattr(f"{mod}.user_prompts_root", _user_root)

    monkeypatch.setattr("app.services.prompt_paths.BUNDLED_PROMPTS_ROOT", bundled)
    monkeypatch.setattr("app.services.prompt_library.PROMPTS_ROOT", bundled)
    monkeypatch.setattr("app.services.prompt_composer.PROMPTS_ROOT", bundled)
    monkeypatch.setattr("app.services.prompt_composer.BUNDLED_PROMPTS_ROOT", bundled)
    return bundled, user


def patch_user_prompts_root_only(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Path:
    """Изолировать только user overlay; bundled остаётся из репозитория."""
    user = tmp_path / "user_prompts"
    user.mkdir()

    def _user_root() -> Path:
        return user

    for mod in (
        "app.services.prompt_paths",
        "app.services.prompt_library",
        "app.services.prompt_active_global",
        "app.services.gpt_verdict_review",
    ):
        monkeypatch.setattr(f"{mod}.user_prompts_root", _user_root)
    return user
