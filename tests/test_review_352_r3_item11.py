"""Ревью #352 п.11: curl 28 не вызывает повторный платный POST; ручной эндпоинт — одна попытка."""

import inspect
import shutil

import pytest

from app.bots.outsee_http import OutseeApiError, _post_generate_via_curl
from app.web.routers import outsee_http as router


class _Proc:
    def __init__(self, code: int, stdout: bytes = b""):
        self.returncode = code
        self._stdout = stdout

    async def communicate(self):
        return self._stdout, b"timeout"


@pytest.mark.asyncio
async def test_curl_timeout_does_not_return_none(monkeypatch, tmp_path) -> None:
    import app.bots.outsee_http as oh

    async def _spawn(*_a, **_k):
        return _Proc(28)

    monkeypatch.setattr(oh, "_headers", lambda: {"Authorization": "Bearer test"})
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/curl")
    monkeypatch.setattr(oh.asyncio, "create_subprocess_exec", _spawn)
    monkeypatch.setattr(oh, "write_curl_header_file", lambda _h: tmp_path / "h")
    (tmp_path / "h").write_text("h", encoding="utf-8")
    with pytest.raises(OutseeApiError, match="code 28"):
        await _post_generate_via_curl("/generate", {"prompt": "x"})


def test_manual_image_endpoint_is_one_attempt() -> None:
    src = inspect.getsource(router)
    assert "max_attempts_per_prompt=1" in src
    assert "gpt_rewrite=False" in src
