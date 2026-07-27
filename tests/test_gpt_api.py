"""GPT API-клиент: успех, ретраи, фатальные ошибки, download, xlsx→text."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.services import gpt_api
from app.services.gpt_api import (
    GptApiError,
    build_messages,
    chat,
    collect_result_urls,
    download_content,
    xlsx_to_text,
)


def _enable(monkeypatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "gpt_api_key", "test-key")
    monkeypatch.setattr(settings, "gpt_base_url", "https://gw.test")
    monkeypatch.setattr(settings, "gpt_chat_path", "/v1/chat/completions")
    monkeypatch.setattr(settings, "gpt_api_mode", "chat")
    monkeypatch.setattr(settings, "gpt_max_retries", 3)


def _mock_httpx(monkeypatch, handler) -> None:
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(gpt_api.httpx, "AsyncClient", factory)

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(gpt_api.asyncio, "sleep", _no_sleep)


def _completion(text: str) -> dict:
    return {
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"total_tokens": 10},
    }


@pytest.mark.asyncio
async def test_chat_success(monkeypatch) -> None:
    _enable(monkeypatch)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json=_completion("привет из API"))

    _mock_httpx(monkeypatch, handler)
    res = await chat(prompt="скажи привет")
    assert res.text == "привет из API"
    assert res.finish_reason == "stop"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_chat_templated_path_per_model(monkeypatch) -> None:
    """kie.ai: путь зависит от модели — /{model}/v1/chat/completions."""
    _enable(monkeypatch)
    from app.settings import settings

    monkeypatch.setattr(settings, "gpt_base_url", "https://api.kie.ai")
    monkeypatch.setattr(settings, "gpt_chat_path", "/{model}/v1/chat/completions")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=_completion("ok"))

    _mock_httpx(monkeypatch, handler)
    await chat(prompt="x", model="gemini-2.5-pro")
    assert seen == ["/gemini-2.5-pro/v1/chat/completions"]


@pytest.mark.asyncio
async def test_chat_retries_on_429_then_success(monkeypatch) -> None:
    _enable(monkeypatch)
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] < 3:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json=_completion("ok после ретраев"))

    _mock_httpx(monkeypatch, handler)
    res = await chat(prompt="x")
    assert res.text == "ok после ретраев"
    assert state["n"] == 3


@pytest.mark.asyncio
async def test_chat_fatal_401_not_retried(monkeypatch) -> None:
    _enable(monkeypatch)
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(401, text="unauthorized")

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(GptApiError) as ei:
        await chat(prompt="x")
    assert ei.value.context.get("status_code") == 401
    assert ei.value.retryable is False
    assert state["n"] == 1  # без ретраев


@pytest.mark.asyncio
async def test_chat_exhausts_retries_on_500(monkeypatch) -> None:
    _enable(monkeypatch)
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(503, text="down")

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(GptApiError):
        await chat(prompt="x", max_retries=2)
    assert state["n"] == 3  # 1 + 2 ретрая


@pytest.mark.asyncio
async def test_chat_responses_mode(monkeypatch) -> None:
    """kie.ai Responses API: input/output вместо messages/choices."""
    _enable(monkeypatch)
    from app.settings import settings

    monkeypatch.setattr(settings, "gpt_base_url", "https://api.kie.ai")
    monkeypatch.setattr(settings, "gpt_chat_path", "/codex/v1/responses")
    monkeypatch.setattr(settings, "gpt_api_mode", "auto")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "role": "assistant",
                        "type": "message",
                        "content": [{"type": "output_text", "text": "работает"}],
                        "status": "completed",
                    }
                ],
            },
        )

    _mock_httpx(monkeypatch, handler)
    res = await chat(prompt="скажи", model="gpt-5-6-sol")
    assert res.text == "работает"
    assert res.finish_reason == "completed"
    assert captured["path"] == "/codex/v1/responses"
    assert "input" in captured["body"]  # responses-формат
    assert "messages" not in captured["body"]


@pytest.mark.asyncio
async def test_chat_provider_envelope_error(monkeypatch) -> None:
    """kie.ai отдаёт ошибку как HTTP 200 {code,msg} — ловим её понятно."""
    _enable(monkeypatch)
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(
            200,
            json={"code": 401, "msg": "The API key is not authorized to use this model.", "data": None},
        )

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(GptApiError) as ei:
        await chat(prompt="x", max_retries=3)
    assert ei.value.context.get("provider_code") == 401
    assert state["n"] == 1  # 401 не ретраится


@pytest.mark.asyncio
async def test_chat_empty_choices_raises(monkeypatch) -> None:
    _enable(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(GptApiError):
        await chat(prompt="x", max_retries=0)


@pytest.mark.asyncio
async def test_download_content(monkeypatch, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"BINARYDATA")

    _mock_httpx(monkeypatch, handler)
    out = tmp_path / "sub" / "img.png"
    got = await download_content("https://cdn.test/img.png", out)
    assert got.read_bytes() == b"BINARYDATA"


@pytest.mark.asyncio
async def test_download_content_404_raises(monkeypatch, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(GptApiError):
        await download_content("https://cdn.test/x", tmp_path / "x")


def test_collect_result_urls() -> None:
    urls = collect_result_urls("готово: https://a.io/x.png, ещё http://b/y.mp4).")
    assert urls == ["https://a.io/x.png", "http://b/y.mp4"]


def test_xlsx_to_text(tmp_path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "План"
    ws.append(["кадр", "текст"])
    ws.append([1, "хук"])
    p = tmp_path / "project.xlsx"
    wb.save(p)
    text = xlsx_to_text(p)
    assert "Лист: План" in text
    assert "хук" in text


def test_build_messages_with_file(tmp_path: Path) -> None:
    f = tmp_path / "voiceover.txt"
    f.write_text("закадровый текст", encoding="utf-8")
    msgs = build_messages(prompt="проверь", accompanying="важно", input_paths=[f], system="ты агент")
    assert msgs[0]["role"] == "system"
    user = msgs[-1]["content"]
    assert "проверь" in user
    assert "важно" in user
    assert "закадровый текст" in user


def _tiny_png(path: Path) -> Path:
    path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
    )
    return path


def test_build_messages_vision_parts(tmp_path: Path) -> None:
    img = _tiny_png(tmp_path / "hero.png")
    msgs = build_messages(prompt="проверь кадр", input_paths=[img])
    content = msgs[-1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_build_input_vision_responses(tmp_path: Path) -> None:
    from app.services.gpt_api import build_input

    img = _tiny_png(tmp_path / "scene.jpg")
    # .jpg with png bytes — mime by suffix; ok for structure test
    img.write_bytes(_tiny_png(tmp_path / "x.png").read_bytes())
    inp = build_input(prompt="aspect?", input_paths=[img])
    assert isinstance(inp, list)
    parts = inp[0]["content"]
    assert parts[0]["type"] == "input_text"
    assert parts[1]["type"] == "input_image"
    assert parts[1]["image_url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_run_operator_api_real_check(monkeypatch, tmp_path: Path) -> None:
    """Интеграция: включённый API + review-роль → analysis.json + verdict."""
    _enable(monkeypatch)
    verdict_json = json.dumps(
        {
            "schema": "vp.check.v1",
            "verdict": "pass",
            "summary": "ок",
            "checks": [{"id": "x", "ok": True}],
            "forward": {"mode": "inherit", "paths": []},
            "fix": {"target": "none"},
        },
        ensure_ascii=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(verdict_json))

    _mock_httpx(monkeypatch, handler)
    from app.services.gpt_operator_client import run_operator_api

    proj = tmp_path / "proj"
    proj.mkdir()
    res = await run_operator_api(
        project_dir=proj,
        node_key="n_check",
        role="review",
        output_mode="text",
        prompt="проверь план",
        accompanying="",
        input_paths=[],
    )
    assert res.gate_status == "pass"
    assert res.analysis is not None and res.analysis.verdict == "pass"
    assert (proj / "excel_gpt_uploads" / "n_check" / "analysis.json").is_file()
