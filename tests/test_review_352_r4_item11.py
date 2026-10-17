"""Ревью #352 r4 п.11: curl 28 не повторяет платный POST.

Недозакрыто после 3e5688b0: внутренний повтор сняли, но ошибка стала
обычным OutseeApiError, и generate_image_with_retries слал её снова.
"""

import shutil

import httpx
import pytest

from app.bots.outsee_http import OutseeSubmitUncertain, _post_generate_via_curl
from app.services import outsee_retry as mod


class _Proc:
    def __init__(self, code: int, stdout: bytes = b""):
        self.returncode = code
        self._stdout = stdout

    async def communicate(self):
        return self._stdout, b"timeout"


class _Client:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        raise httpx.ConnectError("no route")


@pytest.mark.asyncio
async def test_curl_28_is_one_paid_post(monkeypatch, tmp_path) -> None:
    import app.bots.outsee_http as oh

    posts: list[str] = []

    async def _spawn(*_a, **_k):
        posts.append("curl")
        return _Proc(28)

    monkeypatch.setattr(oh, "_headers", lambda: {"Authorization": "Bearer test"})
    monkeypatch.setattr(oh.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/curl")
    monkeypatch.setattr(oh.asyncio, "create_subprocess_exec", _spawn)
    monkeypatch.setattr(oh, "write_curl_header_file", lambda _h: tmp_path / "h")
    (tmp_path / "h").write_text("h", encoding="utf-8")
    monkeypatch.setattr(oh, "outsee_api_configured", lambda: True)

    async def _prep(*_a, **_k):
        return _a[1]

    monkeypatch.setattr(mod, "_prepare_prompt_for_outsee", _prep)

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(mod, "sleep_cancellable", _no_sleep)
    monkeypatch.setattr(
        "app.services.api_tracker_hook.check_api_allowed",
        lambda **_k: None,
    )

    out = tmp_path / "frame.png"
    with pytest.raises(OutseeSubmitUncertain, match="code 28"):
        await mod.generate_image_with_retries(
            None,
            None,
            prompt="тихий двор",
            out_path=out,
            max_attempts_per_prompt=3,
            gpt_rewrite=False,
            model_slug="gpt-image-2",
            aspect_ratio="9:16",
            resolution="2K",
            project_id=13,
        )
    assert posts == ["curl"]


@pytest.mark.asyncio
async def test_curl_non_json_is_not_retried(monkeypatch, tmp_path) -> None:
    import app.bots.outsee_http as oh

    async def _spawn(*_a, **_k):
        return _Proc(0, b"not-json")

    monkeypatch.setattr(oh, "_headers", lambda: {"Authorization": "Bearer test"})
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/curl")
    monkeypatch.setattr(oh.asyncio, "create_subprocess_exec", _spawn)
    monkeypatch.setattr(oh, "write_curl_header_file", lambda _h: tmp_path / "h")
    (tmp_path / "h").write_text("h", encoding="utf-8")
    with pytest.raises(OutseeSubmitUncertain, match="не JSON"):
        await _post_generate_via_curl("/api/v1/images/generate", {"prompt": "x"})


class _VideoClient:
    posts: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        type(self).posts.append("httpx")
        raise httpx.ReadTimeout("read timed out")


@pytest.mark.asyncio
async def test_video_read_timeout_is_one_paid_post(monkeypatch, tmp_path) -> None:
    """Видео-путь: таймаут после POST не уходит в лестницу повторных оплат."""
    import app.bots.outsee_http as oh

    _VideoClient.posts = []
    monkeypatch.setattr(oh, "_headers", lambda: {"Authorization": "Bearer test"})
    monkeypatch.setattr(oh.httpx, "AsyncClient", _VideoClient)
    monkeypatch.setattr(oh, "outsee_api_configured", lambda: True)

    async def _prep(*_a, **_k):
        return _a[1]

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(mod, "_prepare_prompt_for_outsee", _prep)
    monkeypatch.setattr(mod, "sleep_cancellable", _no_sleep)
    monkeypatch.setattr(
        "app.services.api_tracker_hook.check_api_allowed",
        lambda **_k: None,
    )

    with pytest.raises(OutseeSubmitUncertain):
        await mod.generate_video_with_retries(
            None,
            None,
            prompt="тихий двор, камера медленно едет",
            out_path=tmp_path / "clip.mp4",
            max_attempts_per_prompt=3,
            gpt_rewrite=False,
            model_slug="veo-3-1-lite",
            aspect_ratio="9:16",
            resolution="720p",
            project_id=13,
        )
    assert _VideoClient.posts == ["httpx"]
