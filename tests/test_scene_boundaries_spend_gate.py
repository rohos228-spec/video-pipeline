"""Поведение шлюза spend на живом пути call_opus → chat. Только моки."""

from __future__ import annotations

import asyncio
import json

import pytest


class _Route:
    api_model = "gpt-5.6-sol"
    url = "http://127.0.0.1:9/v1/chat/completions"

    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer test"}


class _Resp:
    def __init__(self, status: int = 200, text: str = "ok-text") -> None:
        self.status_code = status
        if status >= 400:
            self.text = text
            self._payload = {"error": text}
        else:
            self._payload = {
                "choices": [
                    {
                        "message": {"content": text},
                        "finish_reason": "stop",
                    }
                ]
            }
            self.text = json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


def _install_llm(monkeypatch, client) -> None:
    async def _resolve(_url: str) -> None:
        return None

    monkeypatch.setattr(
        "app.services.pay52_routes.resolve_text", lambda _model=None: _Route()
    )
    monkeypatch.setattr(
        "app.services.api_tracker_hook.check_api_allowed", lambda **_k: None
    )
    monkeypatch.setattr("app.services.gpt_api._ensure_url_host_resolves", _resolve)
    monkeypatch.setattr("app.services.gpt_api._node_vibecode_override", lambda: False)
    monkeypatch.setattr("app.services.gpt_api._async_client", lambda **_k: client)


class _HoldClient:
    """Один POST. На время запроса снаружи читают inflight."""

    def __init__(self, hold: float = 0.05) -> None:
        self.hold = hold
        self.posts = 0
        self.seen: list[float] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, _url, headers=None, json=None):  # noqa: A002
        from app.services import spend_guard

        self.posts += 1
        self.seen.append(spend_guard.inflight())
        await asyncio.sleep(self.hold)
        return _Resp()


@pytest.mark.asyncio
async def test_one_live_call_reserves_once(tmp_path, monkeypatch) -> None:
    """Один call_opus: inflight=1 на время HTTP и успех при cap 1.5."""
    from app.services import spend_guard
    from app.services.scene_boundaries import call_opus

    spend_guard.reset()
    spend_guard.set_cap(1.5)
    client = _HoldClient()
    _install_llm(monkeypatch, client)

    text = await call_opus("привет", "система", "once", tmp_path)

    assert text.strip()
    assert client.posts == 1
    assert client.seen == [1.0]
    assert spend_guard.reservations() == 1
    assert spend_guard.peak() == 1.0
    assert spend_guard.inflight() == 0.0
