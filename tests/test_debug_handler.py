"""Smoke-тесты на /debug handler (Phase G)."""

from __future__ import annotations


def test_debug_router_importable() -> None:
    from app.telegram.handlers import debug

    assert debug.router is not None
    assert debug.router.name == "debug"


def test_debug_handlers_registered() -> None:
    from app.telegram.handlers.debug import router

    msg_handlers = router.message.handlers
    # один главный диспетчер /debug
    assert len(msg_handlers) >= 1


def test_debug_router_attached_to_dp() -> None:
    import app.telegram.bot

    routers = app.telegram.bot.dp.sub_routers
    names = {r.name for r in routers}
    assert "debug" in names
    assert "ai_agent" in names  # должны оба быть


def test_debug_subcommands_exist() -> None:
    """Все подкоманды объявлены в handler-словаре."""
    import inspect

    from app.telegram.handlers import debug

    source = inspect.getsource(debug.cmd_debug)
    for sub in ("status", "project", "locks", "logs", "ai", "selftest", "api"):
        assert f'"{sub}"' in source, f"subcommand {sub} missing"


def test_project_dump_cli_importable() -> None:
    from scripts import project_dump

    assert callable(project_dump.main)
    # Должны быть две основные async функции
    assert callable(project_dump._dump_project)
    assert callable(project_dump._list_projects)


def test_project_dump_list_runs_on_empty_db() -> None:
    """Если БД пустая — должен возвращать пустой список без ошибок."""
    import asyncio

    from scripts.project_dump import _list_projects

    rows = asyncio.run(_list_projects(limit=10))
    assert isinstance(rows, list)


def test_project_dump_404() -> None:
    """Запрос несуществующего проекта возвращает error."""
    import asyncio

    from scripts.project_dump import _dump_project

    result = asyncio.run(_dump_project(project_id=999999))
    assert "error" in result
