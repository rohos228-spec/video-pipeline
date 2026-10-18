"""П.8: kill switch останавливает curl, nostream, in_flight и разбор стиля."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.api_tracker_hook import ApiEmergencyBlockedError


@pytest.mark.asyncio
async def test_outsee_curl_respects_killswitch(monkeypatch) -> None:
    from app.bots import outsee_http
    from app.services import api_tracker_hook as hook

    monkeypatch.setattr(hook, "is_killswitch_active", lambda force_refresh=False: (True, {"updated_by": "test"}))

    def _no_curl(*_a, **_k):
        raise AssertionError("curl не должен стартовать")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _no_curl)
    with pytest.raises(ApiEmergencyBlockedError):
        await outsee_http._post_generate_via_curl("/v1/generate", {"prompt": "x"})


@pytest.mark.asyncio
async def test_nostream_salvage_respects_killswitch(monkeypatch) -> None:
    from app.services import api_tracker_hook as hook
    from app.services.gpt_api import _chat_completions_nostream

    monkeypatch.setattr(hook, "is_killswitch_active", lambda force_refresh=False: (True, {}))

    def _no_client(*_a, **_k):
        raise AssertionError("POST не должен уйти")

    monkeypatch.setattr("app.services.gpt_api._async_client", _no_client)
    with pytest.raises(ApiEmergencyBlockedError):
        await _chat_completions_nostream(
            url="https://example.invalid/v1/chat/completions",
            headers={},
            body={"model": "gpt-5.6-sol"},
            timeout=5,
            use_model="gpt-5.6-sol",
        )


@pytest.mark.asyncio
async def test_img_pr_cancels_in_flight() -> None:
    from app.services.xlsx_step_runners import cancel_img_pr_in_flight

    started = asyncio.Event()

    async def _hang() -> None:
        started.set()
        await asyncio.sleep(30)

    task = asyncio.create_task(_hang())
    await started.wait()
    in_flight = {task: ([], 0, 1)}
    await cancel_img_pr_in_flight(in_flight)
    assert task.cancelled() or task.done()
    assert in_flight == {}


@pytest.mark.asyncio
async def test_style_paid_ask_stops_when_killswitch(monkeypatch, tmp_path: Path) -> None:
    from app.services import style_analyzer
    from app.services import api_tracker_hook as hook
    from app.services.style_agent.budget import LlmBudget

    monkeypatch.setattr(hook, "is_killswitch_active", lambda force_refresh=False: (True, {}))

    async def _ask(*_a, **_k):
        raise AssertionError("LLM не должна вызываться")

    monkeypatch.setattr("app.services.style_agent.llm.ask", _ask)
    image = tmp_path / "a.png"
    image.write_bytes(b"\x89PNG")
    with pytest.raises(ApiEmergencyBlockedError):
        await style_analyzer._paid_ask(
            LlmBudget(max_calls=3),
            "текст",
            [image],
            system="sys",
            timeout=5,
            label="style_notes",
            project_id=13,
        )
