"""Тесты сервиса `app.services.test_prompt` и меню
`app.telegram.test_prompt_menu`.

Покрываем то, что НЕ требует реального запуска браузера (ChatGPT /
outsee) — то есть: создание проекта, проверка локов (только один
тестовый цикл), генерацию kb'шек для разных статусов.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.models import TestPromptProject as TPProject  # avoid pytest auto-collect of Test* class
from app.services.test_prompt import (
    create_test_project,
    get_running_project,
    is_busy,
)

# Импортируем под другими именами, чтобы pytest не пытался гонять
# их как тесты (всё что начинается с `test_` подхватывается).
from app.telegram.test_prompt_menu import test_project_kb as build_project_kb
from app.telegram.test_prompt_menu import test_root_kb as build_root_kb


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_test_project_basic(session) -> None:
    p = await create_test_project(session, "My Test")
    assert p.id == 1
    assert p.slug == "my-test"
    assert p.name == "My Test"
    assert p.status == "idle"
    assert p.current_iter == 0
    assert p.visual_prompt is None
    assert p.system_prompt is None


@pytest.mark.asyncio
async def test_create_test_project_dedupe_slug(session) -> None:
    p1 = await create_test_project(session, "Same Name")
    p2 = await create_test_project(session, "Same Name")
    p3 = await create_test_project(session, "Same Name")
    assert p1.slug == "same-name"
    assert p2.slug == "same-name-2"
    assert p3.slug == "same-name-3"


@pytest.mark.asyncio
async def test_create_test_project_empty_name(session) -> None:
    with pytest.raises(ValueError):
        await create_test_project(session, "")
    with pytest.raises(ValueError):
        await create_test_project(session, "   ")


@pytest.mark.asyncio
async def test_create_test_project_cyrillic_slug(session) -> None:
    """Кириллица в имени — slug содержит её, т.к. regex `[а-я]` — это
    ожидаемое поведение (slug используется как имя папки на диске).
    """
    p = await create_test_project(session, "Тестик Кошки")
    # «тестик-кошки» (кириллица сохраняется).
    assert p.slug == "тестик-кошки"


@pytest.mark.asyncio
async def test_get_running_project_none(session) -> None:
    await create_test_project(session, "A")
    await create_test_project(session, "B")
    assert (await get_running_project(session)) is None


@pytest.mark.asyncio
async def test_get_running_project_picks_running(session) -> None:
    p_a = await create_test_project(session, "A")
    p_b = await create_test_project(session, "B")
    p_b.status = "running_gpt"
    await session.flush()
    running = await get_running_project(session)
    assert running is not None
    assert running.id == p_b.id

    # Симулируем что одновременно ещё один проект «в outsee» (по факту
    # лок должен предотвратить это, но get_running_project честно
    # вернёт любого).
    p_a.status = "running_outsee"
    await session.flush()
    running = await get_running_project(session)
    assert running is not None
    assert running.id in (p_a.id, p_b.id)


@pytest.mark.asyncio
async def test_is_busy(session) -> None:
    p = await create_test_project(session, "X")
    assert not is_busy(p)
    p.status = "running_gpt"
    assert is_busy(p)
    p.status = "running_outsee"
    assert is_busy(p)
    p.status = "waiting_critique"
    assert not is_busy(p)
    p.status = "idle"
    assert not is_busy(p)
    p.status = "stopped"
    assert not is_busy(p)


def test_data_dir_and_iter_dir() -> None:
    p = TPProject(
        id=7, slug="foo", name="Foo", status="idle", current_iter=0,
    )
    assert "test_prompts" in str(p.data_dir)
    assert str(p.data_dir).endswith("foo")
    assert p.iter_dir(1).name == "iter_001"
    assert p.iter_dir(42).name == "iter_042"


# ---- Меню ----------------------------------------------------------

def test_root_kb_empty_list() -> None:
    kb = build_root_kb([])
    btns = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Новый тестовый проект" in b for b in btns)
    # «⬅ Меню» — возврат в главное меню
    assert any(b.startswith("⬅") for b in btns)


def test_root_kb_with_projects() -> None:
    p1 = TPProject(
        id=1, slug="a", name="Alpha", status="idle", current_iter=3,
    )
    p2 = TPProject(
        id=2, slug="b", name="Beta", status="waiting_critique", current_iter=5,
    )
    kb = build_root_kb([p1, p2])
    btns = [b.text for row in kb.inline_keyboard for b in row]
    # каждый проект — отдельной кнопкой
    assert any("Alpha" in b and "iter=3" in b for b in btns)
    assert any("Beta" in b and "iter=5" in b for b in btns)


def test_project_kb_idle_without_prompts() -> None:
    """Без обоих промтов — нет кнопки «▶ Поехали», только подсказка."""
    p = TPProject(
        id=1, slug="x", name="X", status="idle", current_iter=0,
        visual_prompt=None, system_prompt=None,
    )
    kb = build_project_kb(p)
    btns = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Задай оба промта" in b for b in btns)
    assert all("Поехали" not in b for b in btns)


def test_project_kb_idle_with_prompts() -> None:
    """С обоими промтами — есть «▶ Поехали»."""
    p = TPProject(
        id=1, slug="x", name="X", status="idle", current_iter=0,
        visual_prompt="vp", system_prompt="sp",
    )
    kb = build_project_kb(p)
    btns = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Поехали" in b for b in btns)


def test_project_kb_running() -> None:
    """В running-статусе — только индикатор и «🛑 Стоп»."""
    p = TPProject(
        id=1, slug="x", name="X", status="running_gpt", current_iter=1,
        visual_prompt="vp", system_prompt="sp",
    )
    kb = build_project_kb(p)
    btns = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Идёт шаг" in b for b in btns)
    assert any("🛑" in b for b in btns)
    assert all("Поехали" not in b for b in btns)
    assert all("критику" not in b for b in btns)


def test_project_kb_waiting_critique() -> None:
    """waiting_critique — есть «✏ Добавить критику» и «🛑 Стоп»."""
    p = TPProject(
        id=1, slug="x", name="X", status="waiting_critique", current_iter=2,
        visual_prompt="vp", system_prompt="sp",
    )
    kb = build_project_kb(p)
    btns = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Добавить критику" in b for b in btns)
    assert any("🛑" in b for b in btns)


def test_project_kb_stopped_can_restart() -> None:
    """После stop — кнопка «▶ Повторить» если промты заданы."""
    p = TPProject(
        id=1, slug="x", name="X", status="stopped", current_iter=4,
        visual_prompt="vp", system_prompt="sp",
    )
    kb = build_project_kb(p)
    btns = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Повторить" in b for b in btns)


def test_project_kb_error_can_retry() -> None:
    """После error — то же «▶ Повторить»."""
    p = TPProject(
        id=1, slug="x", name="X", status="error", current_iter=4,
        visual_prompt="vp", system_prompt="sp",
    )
    kb = build_project_kb(p)
    btns = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Повторить" in b for b in btns)


def test_project_kb_always_has_delete_and_back() -> None:
    for status in (
        "idle", "running_gpt", "running_outsee",
        "waiting_critique", "stopped", "error",
    ):
        p = TPProject(
            id=1, slug="x", name="X", status=status, current_iter=0,
            visual_prompt="vp", system_prompt="sp",
        )
        kb = build_project_kb(p)
        btns = [b.text for row in kb.inline_keyboard for b in row]
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert any("Удалить" in b for b in btns), status
        assert any(c == "test:list" for c in cbs), status
