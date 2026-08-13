"""Установка фейков на все границы GPT/медиа/оператора.

Почему список точек: часть модулей делает
`from app.services.gpt_client import get_gpt_client` на топ-уровне —
патчить надо и источник, и ранние биндинги в шагах.

Работает и под pytest (monkeypatch), и в standalone-скрипте (context manager).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from tests.fakes.gpt import FakeChaos, FakeGptClient, FakeScenario, make_fake_operator_api
from tests.fakes.media import (
    fake_generate_image_with_retries,
    fake_generate_video_with_retries,
)

_GPT_CLIENT_TARGETS = [
    "app.services.gpt_client",
    "app.orchestrator.steps.make_animation_prompts",
    "app.orchestrator.steps.generate_videos",
    "app.orchestrator.steps.generate_items",
    "app.orchestrator.steps.generate_images",
    "app.orchestrator.steps.generate_hero",
    "app.services.xlsx_step_runners",
]

_IMAGE_TARGETS = [
    "app.services.outsee_retry",
    "app.orchestrator.steps.generate_images",
    "app.orchestrator.steps.generate_hero",
    "app.orchestrator.steps.generate_items",
    "app.services.montage_board_regen",
]

_VIDEO_TARGETS = [
    "app.services.outsee_retry",
    "app.orchestrator.steps.generate_videos",
    "app.services.montage_board_regen",
]

_OPERATOR_TARGETS = [
    "app.services.gpt_operator_client",
]


def _iter_attrs(module_path: str, name: str):
    import importlib

    try:
        mod = importlib.import_module(module_path)
    except ImportError:
        return
    if hasattr(mod, name):
        yield mod, name


def _make_session_scope(session_factory: Any):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return _scope


def collect_patches(
    fake_gpt: FakeGptClient,
    fake_operator: Any,
    session_factory: Any | None = None,
) -> list[tuple[Any, str, Any]]:
    patches: list[tuple[Any, str, Any]] = []
    for mod_path in _GPT_CLIENT_TARGETS:
        for mod, name in _iter_attrs(mod_path, "get_gpt_client"):
            patches.append((mod, name, lambda: fake_gpt))
    for mod_path in _IMAGE_TARGETS:
        for mod, name in _iter_attrs(mod_path, "generate_image_with_retries"):
            patches.append((mod, name, fake_generate_image_with_retries))
    for mod_path in _VIDEO_TARGETS:
        for mod, name in _iter_attrs(mod_path, "generate_video_with_retries"):
            patches.append((mod, name, fake_generate_video_with_retries))
    for mod_path in _OPERATOR_TARGETS:
        for mod, name in _iter_attrs(mod_path, "run_operator_api"):
            patches.append((mod, name, fake_operator))
    if session_factory is not None:
        # app.db.engine биндится к settings.sqlite_path на импорте — шаги,
        # открывающие SessionLocal посреди run() (parallel img и пр.), иначе
        # пишут в РЕАЛЬНУЮ dev-базу. Подменяем фабрику на тестовую.
        import app.db as _app_db

        patches.append((_app_db, "SessionLocal", session_factory))
        patches.append(
            (_app_db, "session_scope", _make_session_scope(session_factory))
        )
    return patches


@contextmanager
def fakes_installed(
    scenario: FakeScenario | None = None,
    chaos: FakeChaos | None = None,
    session_factory: Any | None = None,
) -> Iterator[FakeGptClient]:
    """Standalone-вариант (скрипты): ставим setattr, откатываем на выходе."""
    fake_gpt = FakeGptClient(scenario=scenario, chaos=chaos)
    fake_operator = make_fake_operator_api(scenario=scenario, chaos=chaos)
    patches = collect_patches(fake_gpt, fake_operator, session_factory)
    saved: list[tuple[Any, str, Any]] = [(obj, name, getattr(obj, name)) for obj, name, _ in patches]
    for obj, name, value in patches:
        setattr(obj, name, value)
    try:
        yield fake_gpt
    finally:
        for obj, name, old in saved:
            setattr(obj, name, old)


def install_fakes_monkeypatch(
    monkeypatch: Any,
    scenario: FakeScenario | None = None,
    chaos: FakeChaos | None = None,
    session_factory: Any | None = None,
) -> FakeGptClient:
    """Pytest-вариант: через monkeypatch.setattr (авто-откат)."""
    fake_gpt = FakeGptClient(scenario=scenario, chaos=chaos)
    fake_operator = make_fake_operator_api(scenario=scenario, chaos=chaos)
    for obj, name, value in collect_patches(fake_gpt, fake_operator, session_factory):
        monkeypatch.setattr(obj, name, value)
    return fake_gpt
