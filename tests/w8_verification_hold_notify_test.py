from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.services import diagnosis
from app.services import notification_log as nl
from app.settings import settings
from tests.w4_4_incident_outcomes_test import SNAPSHOT, _Observer


class FakeBot:
    def __init__(self):
        self.calls = []
        self.session = SimpleNamespace(close=self._close)

    async def _close(self):
        pass

    async def send_message(self, *a, **kw):
        self.calls.append((a, kw))
        return SimpleNamespace(message_id=5)


@pytest.fixture
async def world(monkeypatch, tmp_path):
    from app.services import actions_v1

    monkeypatch.delenv(diagnosis.NOTIFY_ENV, raising=False)
    monkeypatch.delenv("VP_NOTIFICATION_LOG", raising=False)
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "log.db"))
    nl.reset_stats()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    observer = _Observer(factory)
    yield SimpleNamespace(factory=factory, diagnoser=diagnosis.Diagnoser(observer))
    diagnosis.set_escalation_notifier(None)
    await engine.dispose()


async def _seed(world, node_type, status):
    from app.services.observation import ExecutionControl, ObservationEvent

    proof = {"node_type": node_type, "status": status, "errors": ["missing_animation_prompt"],
             "reason": "missing_animation_prompt"}
    async with world.factory() as s:
        s.add(ExecutionControl(execution_id="execution", project_id=2, workflow_run_id=11, node_keys=["n1"],
                               graph_version="g", mode="observe", observer_state="active", cursors={}, gaps=[]))
        for idx, (kind, payload) in enumerate([("node_start", {}), ("node_result_verification", proof),
                                               ("node_end", {})], 1):
            s.add(ObservationEvent(id=idx, event_id=f"v-{idx}", execution_id="execution", project_id=2,
                                   workflow_run_id=11, node_key="n1", attempt_id="a1", request_id=None,
                                   db_scope="project", kind=kind, payload=payload, created_at=datetime.utcnow()))
        await s.commit()


@pytest.mark.parametrize("status", ["failed", "unknown"])
async def test_animation_prompts_hold_writes_one_notification_log_row(world, monkeypatch, status):
    bot = FakeBot()

    async def build_bot():
        return bot, None

    monkeypatch.setattr("app.telegram.bot.build_bot", build_bot)
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "123:fake")
    monkeypatch.setattr(settings, "telegram_owner_chat_id", 4242)
    await _seed(world, "animation_prompts", status)
    for _ in range(3):
        await world.diagnoser.update("execution", snapshot=dict(SNAPSHOT, gaps=[]))
    async with world.factory() as s:
        rows = (await s.scalars(select(diagnosis.Incident))).all()
    assert [r.type for r in rows if r.type == "verification_hold"] == ["verification_hold"]
    assert len(bot.calls) == 1 and bot.calls[0][0][0] == 4242
    (row,) = nl.list_rows(10, project_id=2)
    assert row["ok"] == 1 and row["channel"] == "telegram"
    assert "Проверка промтов анимации" in row["text_redacted"] and "Нужно решение человека" in row["text_redacted"]


async def test_without_telegram_the_hold_is_logged_once_as_not_delivered(world, monkeypatch):
    monkeypatch.setattr(settings, "telegram_enabled", False)
    diagnosis.set_escalation_notifier(None)
    await _seed(world, "animation_prompts", "failed")
    for _ in range(3):
        await world.diagnoser.update("execution", snapshot=dict(SNAPSHOT, gaps=[]))
    (row,) = nl.list_rows(10, project_id=2)
    assert (row["kind"], row["channel"], row["ok"], row["run_id"]) == ("verification_hold", nl.CHANNEL_OFF, 0, "execution")
    assert "не отправлено" in row["error"] and "Проверка промтов анимации" in row["text_redacted"]


async def test_without_telegram_other_incident_types_still_write_nothing(world, monkeypatch):
    from tests.w4_4_incident_outcomes_test import _add_incident

    monkeypatch.setattr(settings, "telegram_enabled", False)
    diagnosis.set_escalation_notifier(None)
    await _add_incident(world)
    assert await world.diagnoser.escalate("execution") == 0
    assert nl.list_rows(10) == []


async def test_telegram_turned_on_later_still_announces_the_logged_hold(world, monkeypatch):
    monkeypatch.setattr(settings, "telegram_enabled", False)
    diagnosis.set_escalation_notifier(None)
    await _seed(world, "animation_prompts", "failed")
    await world.diagnoser.update("execution", snapshot=dict(SNAPSHOT, gaps=[]))
    sent = []

    async def notifier(project_id, text, url):
        sent.append(text)
        return True

    diagnosis.set_escalation_notifier(notifier)
    assert await world.diagnoser.escalate("execution") == 1 and len(sent) == 1
    assert await world.diagnoser.escalate("execution") == 0
