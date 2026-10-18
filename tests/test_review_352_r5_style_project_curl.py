"""Ревью #352 п.10: роутер передаёт project_id, пустой бюджет не зовёт curl."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.style_agent.budget import BudgetExceeded, LlmBudget
from app.web.api import create_app


@pytest.mark.asyncio
async def test_router_forwards_project_id_and_exhausted_budget_skips_curl(
    tmp_path: Path, monkeypatch
) -> None:
    seen: dict[str, int | None] = {}

    async def _analyze(paths, name_hint=None, project_id=None):
        seen["project_id"] = project_id
        return {"name": "стиль", "desc": "", "category": "", "prompt_core": ""}

    monkeypatch.setattr(
        "app.web.routers.gen_assistant.analyze_style_images", _analyze
    )
    image = tmp_path / "ref.png"
    image.write_bytes(b"\x89PNG\r\n")
    client = TestClient(create_app())
    response = client.post(
        "/api/gen-assistant/analyze-style",
        json={"paths": [str(image)], "name_hint": "noir", "project_id": 13},
    )
    assert response.status_code == 200
    assert seen["project_id"] == 13

    from app.bots import outsee_http
    from app.services.spend_guard import use_spend_budget

    monkeypatch.setattr(
        "app.services.api_tracker_hook.is_killswitch_active",
        lambda *a, **k: (False, {}),
    )

    def _no_curl(*_a, **_k):
        raise AssertionError("curl не должен стартовать")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _no_curl)
    with use_spend_budget(LlmBudget(max_calls=0)):
        with pytest.raises(BudgetExceeded):
            await outsee_http._post_generate_via_curl("/v1/generate", {"prompt": "x"})
