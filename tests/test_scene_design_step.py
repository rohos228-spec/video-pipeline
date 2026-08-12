"""Нода scene_design end-to-end на in-memory SQLite (GPT замокан)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus

_VO = "Альфа начало истории. Бета середина пути. Гамма финал рассказа."

_AGENT_MARKERS = {
    "CHARACTERS V2": {"characters": [{"id": "c01", "имя": "Альфа", "внешность": "высокий"}]},
    "WORLD V1": {"locations": [{"id": "loc01", "name": "лес"}]},
    "STYLE V1": {"style_arc": [{"scene_hint": "мрачно"}]},
    "CAMERA V1": {"shot_plan": [{"hint": "крупный план"}]},
    "ACTION V1": {"scenes": [{"hint": "три части"}]},
}


def _assembler_reply(prompt_text: str) -> str:
    """Собрать валидный payload: uuid кадров вытаскиваем из контекста.

    uuid встречаются и в КАДРЫ, и в ячейках scenes_chrono — дедуплицируем,
    порядок сохраняем. Хронометраж сцены = сумма время_сек её кадров.
    """
    pairs = re.findall(
        r'"uuid":\s*"([^"]+)"[^{}]*?"время_сек":\s*([\d.]+)', prompt_text
    )
    frame_times = dict(pairs)
    uuids = list(dict.fromkeys(re.findall(r'"uuid":\s*"([^"]+)"', prompt_text)))
    assert uuids, "в контексте сборщика нет uuid кадров"
    scene_time = round(sum(float(frame_times.get(u) or 0.0) for u in uuids), 1)
    payload = {
        "characters": [{"id": "c01", "имя": "Альфа", "внешность": "высокий"}],
        "scenes": [
            {
                "id_scene": "sc01",
                "start_words": "Альфа начало",
                "end_words": "финал рассказа",
                "время_сек": scene_time,
            }
        ],
        "ops": [
            {
                "frame_uuid": u,
                "fields": {"id_scene": "sc01", "место": "лес", "действие": "идёт"},
            }
            for u in uuids
        ],
        "report": "1 сцена, 3 кадра",
    }
    return json.dumps(payload, ensure_ascii=False)


@pytest_asyncio.fixture
async def sd_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from openpyxl import Workbook

    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    data_dir = tmp_path / "videos" / "sd-test"
    data_dir.mkdir(parents=True)
    wb = Workbook()
    wb.active.title = "план"
    wb.save(data_dir / "project.xlsx")

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'sd.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        p = Project(
            topic="t",
            slug="sd-test",
            status=ProjectStatus.scene_designing,
            script_text=_VO,
            meta={"scene_design_enabled": True},
        )
        session.add(p)
        await session.flush()
        for i, part in enumerate(
            [
                "Альфа начало истории.",
                "Бета середина пути.",
                "Гамма финал рассказа.",
            ],
            start=1,
        ):
            session.add(
                Frame(project_id=p.id, number=i, voiceover_text=part, duration_seconds=3.0)
            )
        await session.commit()
        yield session, p
    await engine.dispose()


def _mock_gpt(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    from app.services import gpt_client

    async def fake_ask(text: str, *, timeout: float = 600, project_id=None, **_kw) -> str:
        for marker, payload in _AGENT_MARKERS.items():
            if marker in text:
                calls.append(marker)
                return json.dumps(payload, ensure_ascii=False)
        assert "ASSEMBLER V2" in text, "неизвестный промпт GPT"
        calls.append("ASSEMBLER V2")
        return _assembler_reply(text)

    monkeypatch.setattr(gpt_client, "gpt_ask_fresh", fake_ask)


def _mock_harness(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import agent_harness

    async def _gate(session, project, *, step: str):
        return None

    monkeypatch.setattr(agent_harness, "harness_gate_or_raise", _gate)


@pytest.mark.asyncio
async def test_scene_design_step_end_to_end(sd_session, monkeypatch) -> None:
    """Две фазы: run (агенты) → scene_agents_ready, run_assemble → scene_design_ready."""
    session, project = sd_session
    calls: list[str] = []
    _mock_gpt(monkeypatch, calls)
    _mock_harness(monkeypatch)

    from app.orchestrator.steps import scene_design

    await scene_design.run(session, project)

    assert project.status is ProjectStatus.scene_agents_ready
    # Фаза 1 — только категорийные агенты, сборщик ещё не вызывался.
    assert sorted(calls) == sorted(_AGENT_MARKERS.keys())
    meta = project.meta or {}
    sd = meta.get("scene_design") or {}
    assert sd.get("status") == "agents_done"
    assert set((sd.get("agents") or {}).keys()) == {
        "characters", "world", "style", "camera", "action",
    }

    # Фаза 2 — сборщик.
    project.status = ProjectStatus.scene_assembling
    await session.commit()
    await scene_design.run_assemble(session, project)

    assert project.status is ProjectStatus.scene_design_ready
    assert calls[-1] == "ASSEMBLER V2"

    meta = project.meta or {}
    sd = meta.get("scene_design") or {}
    assert sd.get("status") == "done"
    registry = meta.get("scene_registry")
    assert isinstance(registry, list) and registry[0]["id_scene"] == "sc01"
    # Хронометраж сцены доехал до реестра (3 кадра × 3.0 сек из БД).
    assert registry[0]["время_сек"] == 9.0

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    for fr in frames:
        attrs = fr.attrs or {}
        assert attrs.get("shot01_id_scene") == "sc01"
        assert attrs.get("place") == "лес"
        assert attrs.get("shot01_action") == "идёт"

    # Чекпоинты агентов на диске.
    sd_dir = project.data_dir / "scene_design"
    for name in ("characters", "world", "style", "camera", "action"):
        assert (sd_dir / f"{name}.json").is_file(), name


@pytest.mark.asyncio
async def test_scene_design_pass_through_when_disabled(sd_session, monkeypatch) -> None:
    session, project = sd_session
    project.meta = {}  # нет per-project override; env-флаг по умолчанию False
    await session.commit()

    from app.services import gpt_client

    async def _boom(*a, **k):  # GPT не должен дёргаться вообще
        raise AssertionError("GPT вызван при выключенном scene_design")

    monkeypatch.setattr(gpt_client, "gpt_ask_fresh", _boom)

    from app.orchestrator.steps import scene_design

    await scene_design.run(session, project)
    assert project.status is ProjectStatus.scene_agents_ready
    project.status = ProjectStatus.scene_assembling
    await session.commit()
    await scene_design.run_assemble(session, project)
    assert project.status is ProjectStatus.scene_design_ready


@pytest.mark.asyncio
async def test_scene_design_checkpoints_skip_gpt_on_retry(sd_session, monkeypatch) -> None:
    """Повторная фаза агентов: все из чекпоинтов, GPT не дёргается вообще."""
    session, project = sd_session
    from app.services.scene_design import runner

    for name, payload in (
        ("characters", {"characters": [{"id": "c01"}]}),
        ("world", {"locations": [{"id": "loc01"}]}),
        ("style", {"style_arc": [{"x": 1}]}),
        ("camera", {"shot_plan": [{"x": 1}]}),
        ("action", {"scenes": [{"x": 1}]}),
    ):
        runner.save_checkpoint(project, name, payload)
    await session.commit()

    calls: list[str] = []
    _mock_gpt(monkeypatch, calls)
    _mock_harness(monkeypatch)

    from app.orchestrator.steps import scene_design

    await scene_design.run(session, project)

    assert project.status is ProjectStatus.scene_agents_ready
    assert calls == []

    # Сборка из чекпоинтов: GPT — только сборщик.
    project.status = ProjectStatus.scene_assembling
    await session.commit()
    await scene_design.run_assemble(session, project)
    assert project.status is ProjectStatus.scene_design_ready
    assert calls == ["ASSEMBLER V2"]


@pytest.mark.asyncio
async def test_scene_design_per_agent_rerun(sd_session, monkeypatch) -> None:
    """invalidate_agent: повторная фаза гоняет GPT только для одного агента."""
    session, project = sd_session
    calls: list[str] = []
    _mock_gpt(monkeypatch, calls)
    _mock_harness(monkeypatch)

    from app.orchestrator.steps import scene_design
    from app.services.scene_design import invalidate_agent

    await scene_design.run(session, project)
    assert project.status is ProjectStatus.scene_agents_ready
    assert len(calls) == 5

    # Перезапуск одного агента (камера): сборка стала stale.
    calls.clear()
    assert invalidate_agent(project, "camera") is True
    await session.commit()
    assert (project.meta or {}).get("scene_design", {}).get("status") == "agents_done"

    project.status = ProjectStatus.scene_designing
    await session.commit()
    await scene_design.run(session, project)

    assert project.status is ProjectStatus.scene_agents_ready
    assert calls == ["CAMERA V1"]


@pytest.mark.asyncio
async def test_scene_design_only_agent_skips_rest(sd_session, monkeypatch) -> None:
    """▶ одной ноды (only_agent) не поднимает GPT для всего веера."""
    session, project = sd_session
    calls: list[str] = []
    _mock_gpt(monkeypatch, calls)
    _mock_harness(monkeypatch)

    from app.orchestrator.steps import scene_design
    from app.services.scene_design import runner as sd_runner

    # Первый ▶ «как скелет»: only_agent=action, остальных чекпоинтов нет.
    sd_runner.set_only_agent(project, "action")
    project.status = ProjectStatus.scene_designing
    await session.commit()
    await scene_design.run(session, project)

    assert calls == ["ACTION V1"]
    assert project.status is ProjectStatus.frames_ready
    assert sd_runner.get_only_agent(project) == "action"
    assert sd_runner.load_checkpoint(project, "action") is not None
    assert sd_runner.load_checkpoint(project, "camera") is None


@pytest.mark.asyncio
async def test_scene_assemble_requires_agents(sd_session, monkeypatch) -> None:
    """Сборщик без чекпоинтов агентов — понятная ошибка, GPT не дёргается."""
    session, project = sd_session
    calls: list[str] = []
    _mock_gpt(monkeypatch, calls)
    _mock_harness(monkeypatch)

    from app.orchestrator.steps import scene_design

    project.status = ProjectStatus.scene_assembling
    await session.commit()
    with pytest.raises(RuntimeError, match="агенты ещё не отработали"):
        await scene_design.run_assemble(session, project)
    assert calls == []


@pytest.mark.asyncio
async def test_reset_scene_asm_keeps_agent_checkpoints(sd_session, monkeypatch) -> None:
    """Каскад reset: scene_asm не сносит чекпоинты агентов (они upstream),
    reset одного агента не трогает соседей."""
    session, project = sd_session
    calls: list[str] = []
    _mock_gpt(monkeypatch, calls)
    _mock_harness(monkeypatch)

    from app.orchestrator.steps import scene_design
    from app.services.reset_step import reset_step
    from app.services.scene_design.runner import _agent_file

    await scene_design.run(session, project)
    assert project.status is ProjectStatus.scene_agents_ready
    for name in ("characters", "world", "style", "camera", "action"):
        assert _agent_file(project, name).is_file(), name

    # Сброс сборщика: все 5 чекпоинтов агентов на месте.
    summary = await reset_step(session, project, "scene_asm")
    wiped = summary.get("__steps_wiped") or []
    assert "scene_asm" in wiped
    assert not (set(wiped) & {"sd_char", "sd_world", "sd_style", "sd_cam", "sd_act"})
    for name in ("characters", "world", "style", "camera", "action"):
        assert _agent_file(project, name).is_file(), name

    # Сброс одного агента (камера): только его чекпоинт удалён.
    summary = await reset_step(session, project, "sd_cam")
    wiped = summary.get("__steps_wiped") or []
    assert "sd_cam" in wiped
    assert not (set(wiped) & {"sd_char", "sd_world", "sd_style", "sd_act"})
    assert not _agent_file(project, "camera").is_file()
    for name in ("characters", "world", "style", "action"):
        assert _agent_file(project, name).is_file(), name
