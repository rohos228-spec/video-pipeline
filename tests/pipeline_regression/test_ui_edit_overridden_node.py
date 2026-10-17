"""Правка промта из студии для ноды с активной версией в node_store.

Контракт после R2: каждый из четырёх эндпоинтов пишет новую строку
``node_prompt_versions`` (``created_via=studio_ui``) через
``node_store.writer.edit_prompt``, git-файл не меняется, и этот текст
уходит в вызов LLM. Сейчас эндпоинты пишут файл. Тест поэтому
``xfail(strict=True)``: когда проводка появится, xpass уронит прогон и
метку надо снять. Если ``node_store`` не импортируется (ещё не влит #349),
тест пропускается.

Сеть не вызывается: LLM перехватывается моком ``gpt_api.chat``.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip(
    "app.orchestrator.node_store",
    reason="node_store ещё не импортируется (PR #349 / R2): тест пропущен",
)

pytestmark = pytest.mark.xfail(
    strict=True,
    reason=(
        "R2 ещё не перевёл эндпоинты студии на node_store.writer.edit_prompt: "
        "правка пишет git-файл, а не строку node_prompt_versions с created_via=studio_ui"
    ),
)

_STEP = "img_pr"
_GIT = "GIT-BASELINE prompt that must stay on disk\n"
_ACTIVE = "ACTIVE-DB version before the studio edit\n"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS node_defs (
    id INTEGER PRIMARY KEY,
    node_key TEXT NOT NULL UNIQUE,
    group_key TEXT NOT NULL,
    title TEXT,
    origin TEXT NOT NULL CHECK (origin IN ('db', 'git_override')),
    is_new_group INTEGER NOT NULL DEFAULT 0 CHECK (is_new_group IN (0, 1)),
    after_node_key TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    active_version INTEGER,
    created_change_id TEXT,
    updated_change_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT,
    CHECK (title IS NULL OR length(title) <= 120),
    CHECK (node_key REGEXP '^n_[a-z0-9_]+$')
);
CREATE TABLE IF NOT EXISTS node_prompt_versions (
    id INTEGER PRIMARY KEY,
    node_key TEXT NOT NULL REFERENCES node_defs (node_key),
    version INTEGER NOT NULL,
    prompt_template TEXT NOT NULL,
    params_json TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (
        kind IN ('create', 'edit_prompt', 'edit_params', 'rollback', 'revert_to_git')
    ),
    patch_mode TEXT,
    section_anchor TEXT,
    reason TEXT,
    parent_version INTEGER,
    base_git_sha256 TEXT,
    change_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    created_via TEXT NOT NULL DEFAULT 'orchestrator'
        CHECK (created_via IN ('orchestrator', 'studio_ui')),
    UNIQUE (node_key, version)
);
CREATE TRIGGER IF NOT EXISTS node_prompt_versions_no_update
BEFORE UPDATE ON node_prompt_versions
BEGIN
    SELECT RAISE(ABORT, 'node_prompt_versions is immutable');
END;
CREATE TRIGGER IF NOT EXISTS node_prompt_versions_no_delete
BEFORE DELETE ON node_prompt_versions
BEGIN
    SELECT RAISE(ABORT, 'node_prompt_versions is immutable');
END;
"""


def _new_text(endpoint: str) -> str:
    return f"STUDIO-{endpoint} prompt that must reach the LLM\n"


def _open_store(path: Path) -> sqlite3.Connection:
    from app.orchestrator.node_store._db import ensure_conn

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # CHECK (node_key REGEXP ...) из миграции #346 требует функцию до CREATE.
    ensure_conn(conn)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _seed_node(conn: sqlite3.Connection, node_key: str, templates: Path) -> None:
    from app.orchestrator.node_store import writer

    writer.create_node(
        conn,
        node_key=node_key,
        group_key="img_pr",
        title="WS1 UI",
        prompt_template=_ACTIVE,
        change_id=f"chg-seed-{node_key}",
        created_via="orchestrator",
        templates_root=templates,
    )


async def _init_app_db(db_path: Path):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.models import Base, Project

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        project = Project(slug="ws1-ui-edit", topic="t", title="WS1")
        session.add(project)
        await session.commit()
        await session.refresh(project)
        project_id = int(project.id)
    return engine, maker, project_id


def _call(client, endpoint: str, node_key: str, project_id: int, version_id: str | None) -> None:
    new_text = _new_text(endpoint)
    if endpoint == "put":
        response = client.put(
            f"/api/prompt-files/{_STEP}/{node_key}",
            json={"content": new_text},
        )
    elif endpoint == "upload":
        response = client.post(
            f"/api/prompt-files/{_STEP}/upload",
            files={"file": (f"{node_key}.md", new_text.encode("utf-8"), "text/markdown")},
        )
    elif endpoint == "restore":
        response = client.post(
            f"/api/prompt-files/{_STEP}/{node_key}/history/{version_id}/restore",
        )
    elif endpoint == "save_template":
        response = client.post(
            f"/api/prompt-studio/projects/{project_id}/gpt-text/{_STEP}/save-template",
            json={"name": node_key, "text": new_text},
        )
    else:
        raise AssertionError(endpoint)
    assert response.status_code < 400, response.text


@pytest.mark.parametrize("endpoint", ["put", "upload", "restore", "save_template"])
def test_ui_edit_overridden_node_writes_version_not_git(
    endpoint: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from app.services import local_library as lib
    from app.services import prompt_library as pl
    from app.services.prompt_history import archive_prompt_version
    from app.web.api import create_app
    from app.web.deps import get_session

    node_key = f"n_ws1_{endpoint}"
    prompts = tmp_path / "prompts"
    prompt_file = prompts / "05_image_prompts" / f"{node_key}.md"
    prompt_file.parent.mkdir(parents=True)
    prompt_file.write_text(_GIT, encoding="utf-8")
    templates = tmp_path / "node_groups" / "img_pr"
    templates.mkdir(parents=True)
    template = templates / f"{node_key}.md"
    template.write_text(_GIT, encoding="utf-8")
    monkeypatch.setattr(pl, "PROMPTS_ROOT", prompts)
    library = tmp_path / "library"
    monkeypatch.setattr(lib, "LIBRARY_ROOT", library)
    monkeypatch.setattr(lib, "CURRENT_ROOT", library / "current")
    monkeypatch.setattr(lib, "OLD_ROOT", library / "old")
    monkeypatch.setattr(lib, "LOGS_ROOT", library / "logs")

    store_path = tmp_path / "nodes.sqlite"
    conn = _open_store(store_path)
    try:
        _seed_node(conn, node_key, templates.parent)
        version_id = None
        if endpoint == "restore":
            version_id = archive_prompt_version(_STEP, node_key, _new_text(endpoint))
            assert version_id
            assert prompt_file.read_text(encoding="utf-8") == _GIT

        engine, maker, project_id = asyncio.run(_init_app_db(tmp_path / "state.db"))

        async def override_session():
            async with maker() as session:
                yield session

        @asynccontextmanager
        async def _quiet(_app):
            yield

        app = create_app()
        app.router.lifespan_context = _quiet
        app.dependency_overrides[get_session] = override_session
        git_before = prompt_file.read_bytes()
        template_before = template.read_bytes()
        try:
            with TestClient(app) as client:
                _call(client, endpoint, node_key, project_id, version_id)
        finally:
            app.dependency_overrides.clear()
            asyncio.run(engine.dispose())

        assert prompt_file.read_bytes() == git_before, (
            "эндпоинт пишет в файл, а не в writer: "
            f"{endpoint} изменил prompts/{_STEP}/{node_key}.md"
        )
        assert template.read_bytes() == template_before, (
            "эндпоинт пишет в файл, а не в writer: изменён git-шаблон ноды"
        )
        rows = conn.execute(
            """
            SELECT version, prompt_template, created_via
            FROM node_prompt_versions
            WHERE node_key = ? AND created_via = 'studio_ui'
            ORDER BY version
            """,
            (node_key,),
        ).fetchall()
        assert rows, "нет новой строки node_prompt_versions с created_via=studio_ui"
        assert _new_text(endpoint) in rows[-1]["prompt_template"]

        from app.orchestrator.node_store import resolve_node

        from app.services import gpt_api
        from app.services.prompt_library import read_resolved_project_prompt

        captured: dict[str, str] = {}

        async def fake_chat(**kwargs):
            captured["prompt"] = str(kwargs.get("prompt") or "")
            return SimpleNamespace(text="ok")

        monkeypatch.setattr(gpt_api, "chat", fake_chat)

        def _no_network(*_args, **_kwargs):
            raise AssertionError("тест не должен ходить в сеть")

        monkeypatch.setattr(gpt_api, "_async_client", _no_network)
        resolved = resolve_node(node_key, conn, templates_root=templates.parent)
        project = SimpleNamespace(prompt_overrides={_STEP: node_key}, meta={}, topic="")
        _name, _path, loaded, _source = read_resolved_project_prompt(
            project, _STEP, node_key=node_key
        )
        asyncio.run(gpt_api.chat(prompt=loaded, accompanying="", auto_pack=False))
        assert _new_text(endpoint) in captured["prompt"]
        assert captured["prompt"] == resolved.prompt_template
    finally:
        conn.close()
