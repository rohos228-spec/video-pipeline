"""DB v2 API: fail-closed apply-ops (GPT) и экспорт DB → project.xlsx."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus, PromptVersion
from app.services import db_apply, db_v2
from app.services.xlsx_v8_import import (
    ROW_IMAGE_PROMPT_V8,
    ROW_VIDEO_PROMPT_V8,
    ROW_VOICEOVER_V8,
)
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session

app = create_app()


@pytest_asyncio.fixture
async def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dbv2api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _gen

    async with factory() as session:
        p = Project(topic="dbv2", slug="dbv2-api", status=ProjectStatus.new)
        session.add(p)
        await session.flush()
        for i in (1, 2):
            session.add(
                Frame(
                    project_id=p.id,
                    number=i,
                    voiceover_text=f"реплика {i}",
                    image_prompt=f"img {i}",
                    animation_prompt=f"vid {i}",
                    attrs={},
                )
            )
        await session.commit()
        await session.refresh(p)
        await db_v2.backfill_project_v2(session, p)
        await session.commit()
        project_id = p.id

        # Минимальный v8-файл: лист «план» должен существовать для экспорта.
        data_dir = p.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = "план"
        wb.create_sheet("Общий план")
        wb.save(data_dir / "project.xlsx")
        wb.close()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, project_id, factory

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_ops_unknown_uuid_fail_closed(api_client) -> None:
    client, project_id, factory = api_client
    r = await client.post(
        f"/api/db/projects/{project_id}/apply-ops",
        json={"ops": [{"frame_uuid": "deadbeef", "fields": {"voiceover_text": "x"}}]},
    )
    assert r.status_code == 400
    assert "неизвестные frame_uuid" in r.json()["detail"]
    async with factory() as session:
        fr = (
            await session.execute(
                select(Frame).where(Frame.project_id == project_id, Frame.number == 1)
            )
        ).scalar_one()
        assert fr.voiceover_text == "реплика 1"  # не тронут


@pytest.mark.asyncio
async def test_apply_ops_empty_and_empty_fields_rejected(api_client) -> None:
    client, project_id, factory = api_client
    async with factory() as session:
        fr = (
            await session.execute(
                select(Frame).where(Frame.project_id == project_id, Frame.number == 1)
            )
        ).scalar_one()
        uuid = fr.uuid
    r_empty = await client.post(f"/api/db/projects/{project_id}/apply-ops", json={"ops": []})
    assert r_empty.status_code == 400
    r_fields = await client.post(
        f"/api/db/projects/{project_id}/apply-ops",
        json={"ops": [{"frame_uuid": uuid, "fields": {}}]},
    )
    assert r_fields.status_code == 400


@pytest.mark.asyncio
async def test_apply_ops_updates_db_versions_and_exports_xlsx(api_client, tmp_path) -> None:
    client, project_id, factory = api_client
    async with factory() as session:
        fr = (
            await session.execute(
                select(Frame).where(Frame.project_id == project_id, Frame.number == 1)
            )
        ).scalar_one()
        uuid = fr.uuid

    r = await client.post(
        f"/api/db/projects/{project_id}/apply-ops",
        json={
            "ops": [
                {
                    "frame_uuid": uuid,
                    "fields": {
                        "voiceover_text": "новый закадр",
                        "image_prompt": "новый img промт",
                        "animation_prompt": "новый vid промт",
                        "duration_seconds": 3.5,
                    },
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["updated"] == 1
    assert data["exported"] and data["exported"]["cells"] > 0

    async with factory() as session:
        fr = (
            await session.execute(
                select(Frame).where(Frame.project_id == project_id, Frame.number == 1)
            )
        ).scalar_one()
        assert fr.voiceover_text == "новый закадр"
        assert fr.image_prompt == "новый img промт"
        assert fr.animation_prompt == "новый vid промт"
        assert fr.duration_seconds == 3.5
        active_img = (
            await session.execute(
                select(PromptVersion).where(
                    PromptVersion.frame_id == fr.id,
                    PromptVersion.kind == "img",
                    PromptVersion.is_active.is_(True),
                )
            )
        ).scalar_one()
        # активная версия совпадает с legacy-полем — нет двух правд
        assert active_img.text == fr.image_prompt

    # Excel — производный view: те же значения лежат в R45/R48/R49 колонки кадра.
    wb = load_workbook(tmp_path / "videos" / "dbv2-api" / "project.xlsx")
    try:
        ws = wb["план"]
        col = 1 + 2
        assert ws.cell(row=ROW_IMAGE_PROMPT_V8, column=col).value == "новый img промт"
        assert ws.cell(row=ROW_VIDEO_PROMPT_V8, column=col).value == "новый vid промт"
        assert ws.cell(row=ROW_VOICEOVER_V8, column=col).value == "новый закадр"
    finally:
        wb.close()


@pytest.mark.asyncio
async def test_apply_ops_human_aliases_and_unknown_field(api_client) -> None:
    """Агент пишет по-человечески — сервер переводит в канонические ключи."""
    client, project_id, factory = api_client
    async with factory() as session:
        fr = (
            await session.execute(
                select(Frame).where(Frame.project_id == project_id, Frame.number == 1)
            )
        ).scalar_one()
        uuid = fr.uuid

    r = await client.post(
        f"/api/db/projects/{project_id}/apply-ops",
        json={
            "ops": [
                {
                    "frame_uuid": uuid,
                    "fields": {
                        "закадр": "закадр по-человечески",
                        "промт_картинки": "img по-человечески",
                        "Промт Видео": "vid по-человечески",
                        "длительность": 4,
                    },
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    async with factory() as session:
        fr = (
            await session.execute(
                select(Frame).where(Frame.project_id == project_id, Frame.number == 1)
            )
        ).scalar_one()
        assert fr.voiceover_text == "закадр по-человечески"
        assert fr.image_prompt == "img по-человечески"
        assert fr.animation_prompt == "vid по-человечески"
        assert fr.duration_seconds == 4.0

    r_bad = await client.post(
        f"/api/db/projects/{project_id}/apply-ops",
        json={"ops": [{"frame_uuid": uuid, "fields": {"неизвестное_поле": "x"}}]},
    )
    assert r_bad.status_code == 400
    assert "неизвестные поля" in r_bad.json()["detail"]


@pytest.mark.asyncio
async def test_apply_ops_project_target_general_plan_exports(api_client, tmp_path) -> None:
    """target=project — поля уровня проекта (нода плана), экспорт в «Общий план»."""
    client, project_id, factory = api_client
    r = await client.post(
        f"/api/db/projects/{project_id}/apply-ops",
        json={"ops": [{"target": "project", "fields": {"общий_план": "Эпизод 1 …"}}]},
    )
    assert r.status_code == 200, r.text
    async with factory() as session:
        p = await session.get(Project, project_id)
        assert (p.meta or {}).get("general_plan") == "Эпизод 1 …"

    wb = load_workbook(tmp_path / "videos" / "dbv2-api" / "project.xlsx")
    try:
        assert wb["Общий план"]["B2"].value == "Эпизод 1 …"
    finally:
        wb.close()

    r_target = await client.post(
        f"/api/db/projects/{project_id}/apply-ops",
        json={"ops": [{"target": "галактика", "fields": {"план": "x"}}]},
    )
    assert r_target.status_code == 400


class _FakeGpt:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def ask_fresh(self, text: str, **kw) -> str:
        return self._reply


@pytest.mark.asyncio
async def test_orchestrator_chat_applies_ops(api_client, monkeypatch) -> None:
    """Диалог с оркестратором: ответ с {ops} применяется через db_apply."""
    client, project_id, factory = api_client
    async with factory() as session:
        fr = (
            await session.execute(
                select(Frame).where(Frame.project_id == project_id, Frame.number == 1)
            )
        ).scalar_one()
        uuid = fr.uuid

    reply = f'{{"ops":[{{"frame_uuid":"{uuid}","fields":{{"закадр":"закадр из чата"}}}}]}}'
    monkeypatch.setattr(
        "app.services.gpt_client.get_gpt_client", lambda: _FakeGpt(reply)
    )
    r = await client.post(
        f"/api/db/projects/{project_id}/orchestrator/chat",
        json={"message": "перепиши закадр в 1 кадре", "history": []},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["error"] is None
    assert data["applied"] and data["applied"]["updated"] == 1
    async with factory() as session:
        fr = (
            await session.execute(
                select(Frame).where(Frame.project_id == project_id, Frame.number == 1)
            )
        ).scalar_one()
        assert fr.voiceover_text == "закадр из чата"


@pytest.mark.asyncio
async def test_orchestrator_chat_plain_text_no_write(api_client, monkeypatch) -> None:
    """Обычный текстовый ответ (вопрос/обсуждение) — без записи в DB."""
    client, project_id, factory = api_client
    monkeypatch.setattr(
        "app.services.gpt_client.get_gpt_client",
        lambda: _FakeGpt("В проекте 2 кадра, всё заполнено."),
    )
    r = await client.post(
        f"/api/db/projects/{project_id}/orchestrator/chat",
        json={"message": "сколько кадров?", "history": []},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["applied"] is None and data["error"] is None
    async with factory() as session:
        fr = (
            await session.execute(
                select(Frame).where(Frame.project_id == project_id, Frame.number == 1)
            )
        ).scalar_one()
        assert fr.voiceover_text == "реплика 1"  # не тронут


@pytest.mark.asyncio
async def test_orchestrator_chat_runs_step_action(api_client, monkeypatch) -> None:
    """Оркестратор может запустить шаг пайплайна: {"actions":[{"run_step":...}]}."""
    client, project_id, factory = api_client
    monkeypatch.setattr(
        "app.services.gpt_client.get_gpt_client",
        lambda: _FakeGpt('{"actions":[{"run_step":"anim_pr"}]}'),
    )
    r = await client.post(
        f"/api/db/projects/{project_id}/orchestrator/chat",
        json={"message": "запусти промты видео", "history": []},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["error"] is None
    # Шаг запущен по пути UI-кнопки (start_step); гард anim_pr честно ответил
    # «нечего генерировать» (нет PNG на диске) — статус не сменился, и это ок.
    assert data["actions_run"] and data["actions_run"][0]["run_step"] == "anim_pr"
    async with factory() as session:
        p = await session.get(Project, project_id)
        assert p.status is ProjectStatus.new

    monkeypatch.setattr(
        "app.services.gpt_client.get_gpt_client",
        lambda: _FakeGpt('{"actions":[{"run_step":"hack"}]}'),
    )
    r_bad = await client.post(
        f"/api/db/projects/{project_id}/orchestrator/chat",
        json={"message": "запусти hack", "history": []},
    )
    assert r_bad.status_code == 200
    assert "неизвестный шаг" in (r_bad.json()["error"] or "")


@pytest.mark.asyncio
async def test_orchestrator_chat_settings_actions(api_client, monkeypatch, tmp_path) -> None:
    """Оркестратор управляет настройками: опции генерации, вариант промта, LLM, стоп."""
    import app.services.prompt_library as pl

    (tmp_path / "prompts" / "01_plan").mkdir(parents=True)
    (tmp_path / "prompts" / "01_plan" / "horror.md").write_text("ужас", encoding="utf-8")
    monkeypatch.setattr(pl, "PROMPTS_ROOT", tmp_path / "prompts")

    client, project_id, factory = api_client
    reply = (
        '{"actions":['
        '{"set_option":{"key":"image_generator","value":"gpt_image_2_vip"}},'
        '{"set_option":{"key":"image_resolution","value":"4k"}},'
        '{"set_option":{"key":"hero_mode","value":"no_hero"}},'
        '{"set_option":{"key":"auto_mode","value":"вкл"}},'
        '{"set_prompt":{"step":"plan","variant":"horror"}},'
        '{"set_text_llm":{"provider":"tokenrouter"}},'
        '{"stop_step":true}'
        "]}"
    )
    monkeypatch.setattr(
        "app.services.gpt_client.get_gpt_client", lambda: _FakeGpt(reply)
    )
    r = await client.post(
        f"/api/db/projects/{project_id}/orchestrator/chat",
        json={"message": "настрой всё и останови", "history": []},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["error"] is None
    kinds = [next(iter(a)) for a in data["actions_run"]]
    assert kinds == [
        "set_option",
        "set_option",
        "set_option",
        "set_option",
        "set_prompt",
        "set_text_llm",
        "stop_step",
    ]
    async with factory() as session:
        p = await session.get(Project, project_id)
        assert p.image_generator == "gpt_image_2_vip"
        assert p.image_resolution == "4k"
        assert p.hero_mode == "no_hero"
        assert p.auto_mode is True
        assert (p.prompt_overrides or {}).get("plan") == "horror"
        assert (p.meta or {}).get("user_stop") is True


@pytest.mark.asyncio
async def test_orchestrator_chat_invalid_setting_rejected(api_client, monkeypatch) -> None:
    client, project_id, factory = api_client
    monkeypatch.setattr(
        "app.services.gpt_client.get_gpt_client",
        lambda: _FakeGpt('{"actions":[{"set_option":{"key":"image_resolution","value":"16k"}}]}'),
    )
    r = await client.post(
        f"/api/db/projects/{project_id}/orchestrator/chat",
        json={"message": "поставь 16k", "history": []},
    )
    assert r.status_code == 200
    assert "неизвестное значение" in (r.json()["error"] or "")
    async with factory() as session:
        p = await session.get(Project, project_id)
        assert p.image_resolution is None  # не тронут


@pytest.mark.asyncio
async def test_checks_context_includes_node_runs_and_harness(api_client) -> None:
    """Контекст оркестратора: итоги harness-проверок + статусы нод (hitl тоже)."""
    from app.models import NodeRun, NodeRunStatus, WorkflowRun, WorkflowRunStatus
    from app.web.routers.db_browser import _checks_context

    client, project_id, factory = api_client
    async with factory() as session:
        p = await session.get(Project, project_id)
        wr = WorkflowRun(
            workflow_id=1, project_id=project_id, status=WorkflowRunStatus.running
        )
        session.add(wr)
        await session.flush()
        session.add(
            NodeRun(workflow_run_id=wr.id, node_key="n1", node_type="plan", status=NodeRunStatus.done)
        )
        session.add(
            NodeRun(
                workflow_run_id=wr.id,
                node_key="n2",
                node_type="img",
                status=NodeRunStatus.failed,
                error="boom",
            )
        )
        session.add(
            NodeRun(
                workflow_run_id=wr.id,
                node_key="n3",
                node_type="hitl_videos",
                status=NodeRunStatus.waiting_hitl,
            )
        )
        meta = dict(p.meta or {})
        meta["ops_telemetry"] = {
            "updated_at": "t",
            "last_outcome": "verify_fail",
            "checks": [{"name": "scenes_png", "ok": False, "detail": "n=0"}],
            "next_action": "repair:img",
        }
        p.meta = meta
        await session.commit()
        lines = await _checks_context(session, p)
    text = "\n".join(lines)
    assert "ПРОВЕРКИ HARNESS" in text
    assert "scenes_png: НЕ ОК" in text
    assert "next_action: repair:img" in text
    assert "СТАТУСЫ НОД" in text
    assert "plan: готово" in text
    assert "img: ОШИБКА — boom" in text
    assert "hitl_videos: ждёт аппрува человека" in text


@pytest.mark.asyncio
async def test_orchestrator_chat_open_ui_action(api_client, monkeypatch) -> None:
    """open_ui step_prompts → ui_actions с node_type для фронта; мусор отклонён."""
    client, project_id, factory = api_client
    monkeypatch.setattr(
        "app.services.gpt_client.get_gpt_client",
        lambda: _FakeGpt('{"actions":[{"open_ui":{"kind":"step_prompts","step":"anim_pr"}}]}'),
    )
    r = await client.post(
        f"/api/db/projects/{project_id}/orchestrator/chat",
        json={"message": "покажи варианты промтов видео", "history": []},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["error"] is None
    assert data["ui_actions"] == [
        {"kind": "step_prompts", "step": "anim_pr", "node_type": "animation_prompts"}
    ]

    monkeypatch.setattr(
        "app.services.gpt_client.get_gpt_client",
        lambda: _FakeGpt('{"actions":[{"open_ui":{"kind":"hack","step":"anim_pr"}}]}'),
    )
    r_bad = await client.post(
        f"/api/db/projects/{project_id}/orchestrator/chat",
        json={"message": "x", "history": []},
    )
    assert "неизвестный kind" in (r_bad.json()["error"] or "")


@pytest.mark.asyncio
async def test_orchestrator_chat_run_harness_action(api_client, monkeypatch) -> None:
    """run_harness → свежий прогон проверок, итог в actions_run."""
    client, project_id, factory = api_client
    monkeypatch.setattr(
        "app.services.gpt_client.get_gpt_client",
        lambda: _FakeGpt('{"actions":[{"run_harness":true}]}'),
    )
    r = await client.post(
        f"/api/db/projects/{project_id}/orchestrator/chat",
        json={"message": "прогони проверки", "history": []},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["error"] is None
    assert data["actions_run"] and "run_harness" in data["actions_run"][0]
    async with factory() as session:
        p = await session.get(Project, project_id)
        tel = (p.meta or {}).get("ops_telemetry") or {}
        assert tel.get("checks")  # телеметрия обновлена — контекст увидит итоги


@pytest.mark.asyncio
async def test_orchestrator_chat_without_gpt_key_503(api_client, monkeypatch) -> None:
    """Без GPT-ключа — вежливая 503 с причиной, не 500/404."""
    client, project_id, factory = api_client
    for var in ("GPT_API_KEY", "TOKENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    r = await client.post(
        f"/api/db/projects/{project_id}/orchestrator/chat",
        json={"message": "привет", "history": []},
    )
    assert r.status_code == 503
    assert "не настроен" in r.json()["detail"]


def test_extract_apply_ops_json_variants() -> None:
    raw = '{"ops":[{"frame_uuid":"u1","fields":{"закадр":"x"}}]}'
    assert db_apply.extract_apply_ops_json(raw)["ops"][0]["frame_uuid"] == "u1"
    fenced = 'Вот результат:\n```json\n{"ops":[{"target":"project","fields":{"план":"y"}}]}\n```\nГотово.'
    assert db_apply.extract_apply_ops_json(fenced)["ops"][0]["target"] == "project"
    assert db_apply.extract_apply_ops_json("просто проза без джейсона") is None
    assert db_apply.extract_apply_ops_json('{"not_ops": true}') is None
    # сревис и endpoint используют одни алиасы
    assert db_apply.FIELD_ALIASES["промт_картинки"] == "image_prompt"
    assert db_apply.FIELD_ALIASES["закадр"] == "voiceover_text"


@pytest.mark.asyncio
async def test_export_xlsx_button_writes_plan_rows(api_client, tmp_path) -> None:
    client, project_id, factory = api_client
    r = await client.post(f"/api/db/projects/{project_id}/export-xlsx")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["frames"] == 2
    assert data["cells"] > 0

    wb = load_workbook(tmp_path / "videos" / "dbv2-api" / "project.xlsx")
    try:
        ws = wb["план"]
        assert ws.cell(row=ROW_IMAGE_PROMPT_V8, column=3).value == "img 1"
        assert ws.cell(row=ROW_VOICEOVER_V8, column=4).value == "реплика 2"
    finally:
        wb.close()
