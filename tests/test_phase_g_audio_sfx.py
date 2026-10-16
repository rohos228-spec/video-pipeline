
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.services.elevenlabs_api import (
    ElevenLabsApiError,
    ElevenLabsUncertainError,
    synthesize_speech,
    tts_run_budget,
)
from app.settings import settings

_REAL_CLIENT = httpx.AsyncClient
_MP3 = b"ID3" + b"\x00" * 2000


class _Net:

    def __init__(self):
        self.script: list = []
        self.posts = 0
        self.proxies: list[str | None] = []


@pytest.fixture
def net(monkeypatch):
    holder = _Net()
    monkeypatch.setattr(settings, "elevenlabs_api_key", "stub-key")
    monkeypatch.setattr(settings, "elevenlabs_proxy_url", None)
    monkeypatch.delenv("ELEVENLABS_PROXY_URL", raising=False)

    real_sleep = asyncio.sleep

    async def _nosleep(_delay, *a, **k):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _nosleep)

    def factory(**kw):
        proxy = kw.pop("proxy", None)

        def handler(request: httpx.Request) -> httpx.Response:
            holder.posts += 1
            holder.proxies.append(proxy)
            step = holder.script.pop(0) if holder.script else 200
            if isinstance(step, Exception):
                raise step
            return httpx.Response(step, content=_MP3 if step == 200 else b'{"detail":"x"}')

        kw["transport"] = httpx.MockTransport(handler)
        return _REAL_CLIENT(**kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return holder


def _run(coro):
    return asyncio.run(coro)


def test_happy_path_one_post(tmp_path, net):
    out = tmp_path / "v.mp3"
    _run(synthesize_speech("Один текст для озвучки.", out))
    assert net.posts == 1
    assert out.read_bytes() == _MP3
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("proxy", [None, "http://proxy.invalid:1"])
def test_post_send_timeout_never_reposted(tmp_path, net, monkeypatch, proxy):
    monkeypatch.setattr(settings, "elevenlabs_proxy_url", proxy)
    net.script = [httpx.ReadTimeout("slow"), 200, 200]
    out = tmp_path / "v.mp3"
    with pytest.raises(ElevenLabsUncertainError):
        _run(synthesize_speech("Текст с таймаутом.", out))
    assert net.posts == 1
    with pytest.raises(ElevenLabsUncertainError):
        _run(synthesize_speech("Текст с таймаутом.", tmp_path / "v2.mp3"))
    assert net.posts == 1
    assert not out.exists()


def test_protocol_error_is_uncertain(tmp_path, net):
    net.script = [httpx.RemoteProtocolError("cut")]
    with pytest.raises(ElevenLabsUncertainError):
        _run(synthesize_speech("Обрыв протокола.", tmp_path / "v.mp3"))
    assert net.posts == 1


def test_connect_error_via_proxy_retries_direct_once(tmp_path, net, monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_proxy_url", "http://proxy.invalid:1")
    net.script = [httpx.ConnectError("dns"), 200]
    _run(synthesize_speech("Прокси недоступен.", tmp_path / "v.mp3"))
    assert net.posts == 2
    assert net.proxies == ["http://proxy.invalid:1", None]


def test_connect_errors_capped_at_two_posts(tmp_path, net, monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_proxy_url", "http://proxy.invalid:1")
    net.script = [httpx.ConnectError("a"), httpx.ConnectError("b"), 200]
    with pytest.raises(ElevenLabsApiError) as ei:
        _run(synthesize_speech("Сеть лежит.", tmp_path / "v.mp3"))
    assert not isinstance(ei.value, ElevenLabsUncertainError)
    assert net.posts == 2


@pytest.mark.parametrize("code", [429, 502, 503, 504])
def test_http_answer_retried_once(tmp_path, net, code):
    net.script = [code, 200]
    _run(synthesize_speech(f"Ответ {code}.", tmp_path / "v.mp3"))
    assert net.posts == 2


def test_http_answer_cap_two_posts(tmp_path, net):
    net.script = [429, 429, 200]
    with pytest.raises(ElevenLabsApiError, match="429"):
        _run(synthesize_speech("Лимит.", tmp_path / "v.mp3"))
    assert net.posts == 2


def test_run_cap_reached_clear_error(tmp_path, net):
    async def go():
        with tts_run_budget(2):
            await synthesize_speech("Первый сегмент.", tmp_path / "a.mp3")
            await synthesize_speech("Второй сегмент.", tmp_path / "b.mp3")
            await synthesize_speech("Третий сегмент.", tmp_path / "c.mp3")

    with pytest.raises(ElevenLabsApiError, match="AUDIO_MAX_TTS_POSTS_PER_RUN"):
        _run(go())
    assert net.posts == 2


def test_run_cap_default_setting(monkeypatch):
    assert settings.audio_max_tts_posts_per_run == 60
    monkeypatch.setattr(settings, "audio_max_tts_posts_per_run", 1)
    with tts_run_budget() as b:
        assert b == [0, 1]


def test_same_text_hash_skips_post(tmp_path, net):
    _run(synthesize_speech("Одинаковый текст.", tmp_path / "a.mp3"))
    res = _run(synthesize_speech("Одинаковый текст.", tmp_path / "b.mp3"))
    assert net.posts == 1
    assert res.read_bytes() == _MP3
    _run(synthesize_speech("Другой текст.", tmp_path / "c.mp3"))
    _run(synthesize_speech("Одинаковый текст.", tmp_path / "d.mp3", voice_id="other"))
    assert net.posts == 3


def test_generate_audio_wraps_tts_in_run_budget():
    src = Path("app/orchestrator/steps/generate_audio.py").read_text(encoding="utf-8")
    assert "with tts_run_budget():" in src




def _sfx_setup(monkeypatch, tmp_path, errors):
    from app.models import Project
    from app.services import sfx_gen
    from app.services.sfx_plan import SfxEvent

    monkeypatch.setattr(settings, "elevenlabs_api_key", "stub")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(sfx_gen, "verify_audio_file", lambda *a, **k: [])
    calls = []

    async def fake_sfx(prompt, duration, out_path):
        i = len(calls)
        calls.append(prompt)
        if errors[i] is not None:
            raise errors[i]
        out_path.write_bytes(b"x" * 2000)
        return out_path

    monkeypatch.setattr(sfx_gen, "_elevenlabs_sfx", fake_sfx)
    facts = []
    import app.services.observation as obs

    monkeypatch.setattr(obs, "emit", lambda kind, payload=None, **k: facts.append((kind, payload)))
    project = Project(slug="g3", title="t", topic="t")
    project.meta = {}
    events = [SfxEvent(1, 0.0, 1.0, "hit", f"p{i}", 0.5, False) for i in range(len(errors))]
    return sfx_gen, project, events, calls, facts


class _Sess:
    async def flush(self):
        return None


def test_sfx_fallback_recorded_with_charge_uncertainty(monkeypatch, tmp_path):
    errors = [httpx.ReadTimeout("t"), RuntimeError("11labs sfx 500: x"), None]
    sfx_gen, project, events, calls, facts = _sfx_setup(monkeypatch, tmp_path, errors)
    files = _run(sfx_gen.generate_sfx_files(_Sess(), project, events))
    assert len(calls) == 3
    assert [f["provider"] for f in files] == ["local_synth", "local_synth", "elevenlabs"]
    assert files[0]["provider_fallback"]["error_class"] == "ReadTimeout"
    assert files[0]["provider_fallback"]["may_have_charged"] == "uncertain"
    assert files[1]["provider_fallback"]["may_have_charged"] == "no"
    assert "provider_fallback" not in files[2]
    assert [k for k, _ in facts] == ["provider_fallback", "provider_fallback"]
    assert project.meta["ai_jobs"]["sfx_files"]["provider_fallbacks"] == 2


def test_sfx_no_fallback_no_record(monkeypatch, tmp_path):
    sfx_gen, project, events, calls, facts = _sfx_setup(monkeypatch, tmp_path, [None, None])
    files = _run(sfx_gen.generate_sfx_files(_Sess(), project, events))
    assert facts == []
    assert all("provider_fallback" not in f for f in files)
