"""Ревью #352 п.9: script_frames_qc и разбор стиля идут в 52pay, не в тихий vibecode."""

import pytest

from app.services.gpt_api import GptApiError
from app.services.llm_override import NodeLlmOverride, use_override
from app.services.scene_boundaries import _kie_once


def test_bind_group_provider_is_52pay() -> None:
    from app.models import ProjectStatus
    from app.services.llm_override import bind_project_llm, current_override
    from tests.test_llm_override import _P, _SFQC_GRAPH

    meta = {**_SFQC_GRAPH, "active_excel_gpt_node_key": "n_excel_gpt_fw_script"}
    with bind_project_llm(_P(meta), ProjectStatus.enriching_1):
        ov = current_override()
        assert ov is not None
        assert ov.provider == "52pay"
        assert ov.model_id == "gpt-5.6-sol"


@pytest.mark.asyncio
async def test_chat_without_52pay_route_raises(monkeypatch) -> None:
    import app.services.gpt_api as gpt_api
    from app.settings import settings

    monkeypatch.setattr(settings, "pay52_gpt_api_key", "")
    monkeypatch.setattr(gpt_api.settings, "pay52_gpt_api_key", "")
    ov = NodeLlmOverride(
        model_id="gpt-5.6-sol",
        channel="stable",
        kind="text",
        provider="52pay",
        label="52pay",
        reasoning_effort="high",
    )
    with use_override(ov):
        with pytest.raises(GptApiError, match="нет маршрута 52pay"):
            await gpt_api.chat(prompt="ping", auto_pack=False, max_retries=0)


@pytest.mark.asyncio
async def test_scene_boundaries_without_route_uses_regular_gateway(monkeypatch) -> None:
    """Границы сцен без маршрута 52pay идут в обычный шлюз (WS2-r0b, коллега c319f3d2).

    Жёсткий отказ остался только у разбора стиля (style_agent/job.py), границы сцен
    на vibecode-фолбэк не блокируются.
    """
    from types import SimpleNamespace

    from app.settings import settings
    import app.services.gpt_api as gpt_api

    monkeypatch.setattr(settings, "pay52_gpt_api_key", "")
    calls: list[str] = []

    async def fake_chat(**kwargs):
        calls.append(str(kwargs.get("model") or ""))
        return SimpleNamespace(text="[x]", finish_reason="stop")

    monkeypatch.setattr(gpt_api, "chat", fake_chat)
    text, meta = await _kie_once("u", "s")
    assert calls == ["gpt-5.6-sol"], calls
    assert text == "[x]"
    assert meta["model"] == "gpt-5.6-sol"
