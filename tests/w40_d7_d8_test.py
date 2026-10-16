
from __future__ import annotations

import sqlite3

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus, Workflow
from app.services.project_state import apply_meta_diff, meta_diff
from app.services.prompt_composer import (
    UnresolvedPlaceholdersError,
    assert_prompt_resolved,
    find_unresolved_placeholders,
)
from app.services.prompt_library import _render_enrich_template, read_resolved_project_prompt
from app.web.api import create_app
from app.web.deps import get_session



def _project(**kw) -> Project:
    return Project(topic="Тема ролика", slug="d7", prompt_overrides=kw.get("prompt_overrides", {}), meta={})


def test_real_excel_gpt_template_is_fully_substituted():
    _name, _path, text, _src = read_resolved_project_prompt(_project(), "excel_gpt")
    assert "{{" not in text and "<!--" not in text
    assert "лист «план»" in text
    assert find_unresolved_placeholders(text) == []


def test_project_vars_reach_the_prompt():
    po = {"vars": {"EXCEL_GPT_SHEET": "Кадры", "EXCEL_GPT_TASK": "Добавь колонку «Свет»"}}
    _n, _p, text, _s = read_resolved_project_prompt(_project(prompt_overrides=po), "excel_gpt")
    assert "лист «Кадры»" in text and "Добавь колонку «Свет»" in text


def test_unknown_var_stops_before_the_paid_call():
    with pytest.raises(UnresolvedPlaceholdersError) as e:
        _render_enrich_template(_project(), "excel_gpt", "Задача {{VAR:NO_SUCH_VAR}} и {{BLOCK:enrich_role}}", "")
    msg = str(e.value)
    assert "excel_gpt" in msg and "деньги не потрачены" in msg and "NO_SUCH_VAR" in msg


def test_guard_catches_composer_diagnostics_and_ignores_other_braces():
    assert find_unresolved_placeholders("x <!-- missing var: A --> y")
    assert find_unresolved_placeholders("{{ VAR:A }}")
    assert not find_unresolved_placeholders('{"ops":[{"frame_uuid":"{x}"}]} {{BRIEF}}')
    assert_prompt_resolved("чистый текст", label="excel_gpt")



@pytest_asyncio.fixture
async def stand(tmp_path):
    db_path = tmp_path / "d8.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()

    async def _gen():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _gen
    async with factory() as s:
        s.add(Workflow(name="default", is_default=True, nodes=[{"id": "plan_1", "type": "plan", "x": 0, "y": 0}], edges=[]))
        p = Project(topic="D8", slug="d8", status=ProjectStatus.new, hero_mode="no_hero",
                    script_text="Текст закадра.", general_plan="План", meta={"keep": 1})
        s.add(p)
        await s.flush()
        for n in (1, 2):
            s.add(Frame(project_id=p.id, number=n, voiceover_text="v", image_prompt="ip",
                        animation_prompt="ap", status=FrameStatus.image_generated))
        await s.commit()
        pid = p.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, pid, factory, db_path
    app.dependency_overrides.clear()
    await engine.dispose()


def _row(db_path, pid):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("select status, meta, updated_at from projects where id=?", (pid,)).fetchone()
    finally:
        con.close()


@pytest.mark.asyncio
async def test_get_project_does_not_write(stand):
    c, pid, _f, db_path = stand
    before = _row(db_path, pid)
    assert before[0] == "new"
    for _ in range(3):
        r = await c.get(f"/api/projects/{pid}")
        assert r.status_code == 200
        assert r.json()["status"] == "script_ready"
    assert _row(db_path, pid) == before


@pytest.mark.asyncio
async def test_sync_endpoint_is_the_explicit_writer(stand):
    c, pid, _f, db_path = stand
    shown = (await c.get(f"/api/projects/{pid}")).json()["status"]
    assert shown == "script_ready" and _row(db_path, pid)[0] == "new"
    r = await c.post(f"/api/projects/{pid}/sync")
    assert r.status_code == 200
    assert r.json()["status"] == shown
    assert _row(db_path, pid)[0] == shown


@pytest.mark.asyncio
async def test_meta_is_merged_by_keys_not_overwritten(stand):
    _c, pid, factory, db_path = stand
    async with factory() as stale:
        p = await stale.get(Project, pid)
        before = dict(p.meta)
        async with factory() as other:
            q = await other.get(Project, pid)
            q.meta = {**q.meta, "user_stop": True}
            await other.commit()
        mine = {**before, "step_failure": {"x": 1}}
        set_keys, del_keys = meta_diff(before, mine)
        assert set_keys == {"step_failure": {"x": 1}} and del_keys == []
        await apply_meta_diff(stale, p, set_keys, del_keys)
        await stale.commit()
    import json
    meta = json.loads(_row(db_path, pid)[1])
    assert meta == {"keep": 1, "user_stop": True, "step_failure": {"x": 1}}
