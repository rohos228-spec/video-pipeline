"""Чат-оркестратор: append-only аудит действий и флаг CHAT_CODE_ACTIONS_ENABLED (без сети)."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.services.action_layer.store import ActionHistory
from app.settings import Settings, settings
from tests.action_layer_support import env  # noqa: F401

SECRET = "sk-ABCDEFGH1234567890zz"


class _FakeGpt:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def ask_fresh(self, text: str, **kw) -> str:
        return self._reply


def _reply() -> str:
    return json.dumps({"actions": [{"edit_files": [
        {"path": "app/x.py", "content": f"token = '{SECRET}'"}]}]})


async def _rows(env):
    async with env.factory() as s:
        return (await s.execute(select(ActionHistory).where(
            ActionHistory.event == "chat_action"))).scalars().all()


async def _chat(env, monkeypatch, pid: int):
    monkeypatch.setattr("app.services.gpt_client.get_gpt_client", lambda: _FakeGpt(_reply()))
    return await env.client.post(f"/api/db/projects/{pid}/orchestrator/chat",
                                 json={"message": "поправь файл", "history": []})


async def test_executed_action_is_audited_without_secrets(env, monkeypatch) -> None:
    pid = await env.add_project()
    monkeypatch.setattr(settings, "chat_code_actions_enabled", True)
    monkeypatch.setattr("app.services.code_autofix.apply_edits",
                        lambda edits: {"count": 1, "changed": ["app/x.py"]})
    r = await _chat(env, monkeypatch, pid)
    assert r.status_code == 200, r.text
    rows = await _rows(env)
    assert len(rows) == 1
    row = rows[0]
    assert row.action_id == "edit_files" and row.outcome == "ok"
    assert row.actor.startswith("local_web_operator")
    assert row.params["project_id"] == pid and len(row.params["params_digest"]) == 64
    assert SECRET not in json.dumps(row.params) + row.error


def test_code_actions_are_off_by_default(monkeypatch) -> None:
    from app.settings import Settings

    monkeypatch.delenv("CHAT_CODE_ACTIONS_ENABLED", raising=False)
    assert Settings(_env_file=None).chat_code_actions_enabled is False
    monkeypatch.setenv("CHAT_CODE_ACTIONS_ENABLED", "true")
    assert Settings(_env_file=None).chat_code_actions_enabled is True


async def test_code_actions_default_refuses_without_explicit_opt_in(env, monkeypatch) -> None:
    pid = await env.add_project()
    called: list = []
    monkeypatch.setattr("app.services.code_autofix.apply_edits",
                        lambda edits: called.append(edits) or {"count": 1, "changed": []})
    monkeypatch.setattr(settings, "chat_code_actions_enabled", Settings(_env_file=None).chat_code_actions_enabled)
    r = await _chat(env, monkeypatch, pid)
    assert r.status_code == 200, r.text
    assert called == []
    assert "CHAT_CODE_ACTIONS_ENABLED=true" in (r.json()["error"] or "")
    assert [x.outcome for x in await _rows(env)] == ["refused"]


async def test_code_actions_flag_off_refuses_and_audits(env, monkeypatch) -> None:
    pid = await env.add_project()
    called: list = []
    monkeypatch.setattr("app.services.code_autofix.apply_edits",
                        lambda edits: called.append(edits) or {"count": 1, "changed": []})
    monkeypatch.setattr(settings, "chat_code_actions_enabled", False)
    r = await _chat(env, monkeypatch, pid)
    assert r.status_code == 200, r.text
    assert called == []
    assert "CHAT_CODE_ACTIONS_ENABLED" in (r.json()["error"] or "")
    rows = await _rows(env)
    assert [x.outcome for x in rows] == ["refused"]


async def test_confirm_git_push_refused_when_flag_off(env, monkeypatch) -> None:
    pid = await env.add_project()
    monkeypatch.setattr(settings, "chat_code_actions_enabled", False)
    r = await env.client.post(f"/api/db/projects/{pid}/orchestrator/confirm-git-push",
                              json={"message": "fix", "files": ["app/x.py"]})
    assert r.status_code == 403
    assert [x.outcome for x in await _rows(env)] == ["refused"]


async def test_chat_audit_history_is_append_only(env, monkeypatch) -> None:
    pid = await env.add_project()
    monkeypatch.setattr(settings, "chat_code_actions_enabled", True)
    monkeypatch.setattr("app.services.code_autofix.apply_edits",
                        lambda edits: {"count": 1, "changed": ["a"]})
    await _chat(env, monkeypatch, pid)
    async with env.factory() as s:
        with pytest.raises(Exception):
            await s.execute(ActionHistory.__table__.update().values(outcome="x"))
            await s.commit()
