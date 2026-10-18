"""Ревью #352: успешный платный curl сам пишет трату, без бюджета из теста."""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_paid_curl_increases_spent_without_manual_budget(monkeypatch) -> None:
    from app.bots import outsee_http
    from app.services.spend_guard import pipeline_spend_budget

    monkeypatch.setattr(
        "app.services.api_tracker_hook.is_killswitch_active",
        lambda *a, **k: (False, {}),
    )
    monkeypatch.setattr(
        outsee_http,
        "_headers",
        lambda: {"Authorization": "Bearer test", "Accept": "application/json"},
    )

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b'{"id": 7, "status": "ok"}', b""

    async def _fake_curl(*_args, **_kwargs):
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_curl)

    before = pipeline_spend_budget().spent
    data = await outsee_http._post_generate_via_curl(
        "/api/v1/images/generate", {"prompt": "x"}
    )
    assert isinstance(data, dict)
    assert data.get("id") == 7
    assert pipeline_spend_budget().spent == before + 1
