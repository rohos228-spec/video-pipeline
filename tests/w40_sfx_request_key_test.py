"""N-12: у платного SFX есть ключ запроса (тот же запрос не оплачивается дважды), mp3 провайдера принимается как звук.

Реально: файловая система, ffmpeg/ffprobe для настоящего mp3, независимая проверка audio_verification.
Подменён только платный POST (счётчик вызовов). Сети и платных вызовов нет.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import elevenlabs_api as el
from app.services.ai_result_io import drop_ai_job_checkpoint
from app.services.sfx_gen import _PROGRESS, generate_sfx_files, verify_audio_file
from app.services.sfx_plan import SfxEvent
from app.settings import settings

FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe are required for the mp3 fixture")


def _ev(**kw) -> SfxEvent:
    base = {"frame_number": 1, "t_start": 1.0, "duration": 1.0, "kind": "whoosh", "prompt": "deep whoosh",
            "gain": 0.5, "duck": False}
    base.update(kw)
    return SfxEvent(**base)


def _mp3(path: Path, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                    str(path)], check=True, timeout=60)


class _Session:
    async def flush(self):
        return None

    async def commit(self):
        return None


class _Provider:
    """Подмена платного POST: пишет настоящий mp3 нужной длины и считает вызовы."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, prompt: str, duration: float, out_path: Path) -> Path:
        self.calls += 1
        _mp3(out_path, duration)
        return out_path


def test_request_key_depends_on_text_and_duration_only():
    base = el.sfx_request_key("deep whoosh", 1.0)
    assert base == el.sfx_request_key("deep whoosh", 1.0)
    assert base != el.sfx_request_key("deep whoosh!", 1.0)
    assert base != el.sfx_request_key("deep whoosh", 1.5)
    # длительность провайдеру уходит в его границах: 0.1 и 0.5 - один и тот же запрос
    assert el.sfx_request_key("x", 0.1) == el.sfx_request_key("x", 0.5)
    # провайдер видит только первые 450 знаков
    assert el.sfx_request_key("a" * 500, 1.0) == el.sfx_request_key("a" * 450 + "b" * 50, 1.0)


@needs_ffmpeg
def test_provider_mp3_is_accepted_not_read_as_wav(tmp_path):
    path = tmp_path / "sfx_00_whoosh.mp3"
    _mp3(path, 1.0)
    assert verify_audio_file(path, expect_duration=1.0, duration_tol=0.6) == []


@needs_ffmpeg
@pytest.mark.asyncio
async def test_same_paid_request_is_not_sent_twice(tmp_path):
    project = SimpleNamespace(id=1, meta={}, data_dir=tmp_path)
    provider = _Provider()
    with patch.object(settings, "elevenlabs_api_key", "test-key"), \
            patch("app.services.sfx_gen._elevenlabs_sfx", provider):
        first = await generate_sfx_files(_Session(), project, [_ev()])
        assert provider.calls == 1 and first[0]["provider"] == "elevenlabs"
        assert first[0]["path"].endswith(".mp3")

        # чекпоинт потерян (рестарт, ручная правка), файл с меткой запроса остался: второй POST не уходит
        project.meta = {}
        again = await generate_sfx_files(_Session(), project, [_ev()])
        assert provider.calls == 1, "тот же запрос оплачен повторно"
        assert again[0]["provider"] == "elevenlabs" and Path(again[0]["path"]).is_file()

        # другой текст - другой запрос, он платный
        project.meta = {}
        await generate_sfx_files(_Session(), project, [_ev(prompt="soft click")])
        assert provider.calls == 2


@needs_ffmpeg
@pytest.mark.asyncio
async def test_forced_regeneration_still_pays(tmp_path):
    """Осознанная перегенерация человеком (force) идёт мимо кэша: иначе её нельзя было бы заказать."""
    project = SimpleNamespace(id=1, meta={}, data_dir=tmp_path)
    provider = _Provider()
    with patch.object(settings, "elevenlabs_api_key", "test-key"), \
            patch("app.services.sfx_gen._elevenlabs_sfx", provider):
        await generate_sfx_files(_Session(), project, [_ev()])
        drop_ai_job_checkpoint(project, _PROGRESS)
        await generate_sfx_files(_Session(), project, [_ev()], force=True)
    assert provider.calls == 2
