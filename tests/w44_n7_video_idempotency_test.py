"""N-7: у платной отправки клипа Outsee есть ключ запроса (проект, клип, sha модели и промта).

Класс доказательства: U/D на заменителе провайдера. Заменитель вместо ``outsee_http.generate_video``, настоящая
лестница ``generate_video_with_retries`` и настоящая таблица ``outsee_idempotency`` на временной SQLite. Сети,
платных вызовов и боевой базы нет. Живого доказательства нет.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bots import outsee_http
from app.bots.outsee import GenerationResult
from app.bots.outsee_http import OutseeSubmitUnknownError
from app.models import Base
from app.services import media_spend, outsee_idempotency as idem, outsee_retry
from app.services.video_error_policy import is_no_resubmit
from app.settings import settings

PID = 7
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 4096


class Provider:
    def __init__(self):
        self.calls = 0
        self.script: list = []

    async def generate_video(self, prompt, out_path, **kw):
        self.calls += 1
        step = self.script.pop(0) if self.script else "ok"
        outsee_http._probe_mark(maybe_sent=True)
        if isinstance(step, BaseException):
            raise step
        outsee_http._probe_mark(task_id="V-1", done=True)
        Path(out_path).write_bytes(MP4)
        return GenerationResult(file_path=Path(out_path), raw_url="https://cdn.example/x.mp4", gen_id="V-1")


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ledger.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr("app.db.SessionLocal", sessions)
    monkeypatch.setattr(media_spend, "TEST_FORCE", True)
    monkeypatch.setattr(media_spend, "_enabled", lambda p: True)
    monkeypatch.setattr(media_spend, "_managed", lambda: False)
    monkeypatch.setattr(settings, "media_spend_recording", True)
    monkeypatch.setattr(idem, "_ensured", set())
    provider = Provider()
    monkeypatch.setattr(outsee_http, "generate_video", provider.generate_video)
    monkeypatch.setattr(outsee_http, "outsee_api_configured", lambda: True)

    class E:
        pass

    e = E()
    e.provider, e.sessions, e.tmp, e.n = provider, sessions, tmp_path, 0

    def out(name="clip_003"):
        e.n += 1
        return tmp_path / f"{name}_{e.n:08x}.mp4"

    async def gen(prompt="slow pan", *, idempotent=True, out_path=None, pid=PID, **kw):
        return await outsee_retry.generate_video_with_retries(
            None, None, prompt=prompt, out_path=out_path or out(), model_slug="veo-3-1-lite", project_id=pid,
            idempotent=idempotent, gpt_rewrite=False, **kw)

    e.out, e.gen = out, gen
    yield e
    await media_spend.drain()
    await engine.dispose()


def test_clip_key_drops_the_per_attempt_hex_and_keeps_the_shot(tmp_path):
    assert idem.make_ctx(7, tmp_path / "clip_003_ab12cd34.mp4", "m", "p", media="video").key == "clip_003"
    assert idem.make_ctx(7, tmp_path / "clip_003_s2_ab12cd34.mp4", "m", "p", media="video").key == "clip_003_s2"


async def test_answer_lost_after_send_blocks_every_next_paid_request(env):
    """Раньше лестница из 4+3 попыток могла послать платный запрос заново после потери ответа."""
    env.provider.script = [OutseeSubmitUnknownError("no answer")]
    with pytest.raises(OutseeSubmitUnknownError):
        await env.gen()
    assert env.provider.calls == 1
    with pytest.raises(idem.OutseeIdempotencyUncertainError) as caught:
        await env.gen()  # тот же клип после рестарта
    assert env.provider.calls == 1, "второй платный запрос не должен уйти"
    assert is_no_resubmit(caught.value)


async def test_recorded_result_is_adopted_instead_of_paying_again(env):
    first = await env.gen()
    assert env.provider.calls == 1 and Path(first.file_path).is_file()
    again = await env.gen(out_path=env.out())  # тот же ключ, результат записан и ещё не принят в Artifact
    assert env.provider.calls == 1, "платный результат уже есть"
    assert Path(again.file_path) == Path(first.file_path)


async def test_consumed_request_allows_a_new_paid_regeneration(env):
    await env.gen()
    await idem.mark_consumed(PID, "clip_003")  # клип принят: человек может заказать новый
    await env.gen(out_path=env.out())
    assert env.provider.calls == 2


async def test_without_the_flag_nothing_is_recorded(env):
    """Старое поведение сохранено для вызовов без флага (ручные и вспомогательные пути)."""
    env.provider.script = [OutseeSubmitUnknownError("no answer")]
    with pytest.raises(OutseeSubmitUnknownError):
        await env.gen(idempotent=False)
    async with env.sessions() as s:
        assert await idem._attempts(s, PID, "clip_003") == []


async def test_idempotent_flag_is_not_forwarded_to_the_provider(env, monkeypatch):
    seen: dict = {}

    async def spy(prompt, out_path, **kw):
        seen.update(kw)
        Path(out_path).write_bytes(MP4)
        return GenerationResult(file_path=Path(out_path), gen_id="V-2")

    monkeypatch.setattr(outsee_http, "generate_video", spy)
    await env.gen()
    assert "idempotent" not in seen
