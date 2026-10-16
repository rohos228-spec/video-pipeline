from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bots import outsee_http
from app.bots.outsee import GenerationResult, OutseeDownloadError, OutseeImageError
from app.bots.outsee_http import OutseeApiError, OutseeSubmitUnknownError
from app.models import Base
from app.services import media_resume, media_spend, outsee_guard, outsee_idempotency as idem, outsee_retry
from app.services.video_error_policy import is_no_resubmit
from app.settings import settings

PID = 7
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 4096


class ProcessDied(BaseException):
    pass


class Provider:

    def __init__(self):
        self.calls = 0
        self.script: list = []
        self.prompts: list[str] = []

    async def generate_image(self, prompt, out_path, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        step = self.script.pop(0) if self.script else "ok"
        outsee_http._probe_mark(maybe_sent=True)
        if callable(step):
            return await step(out_path)
        if isinstance(step, BaseException):
            raise step
        outsee_http._probe_mark(task_id="G-1", done=True)
        Path(out_path).write_bytes(PNG)
        return GenerationResult(file_path=Path(out_path), raw_url="https://cdn.example/x.png", gen_id="G-1")


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
    monkeypatch.setattr(settings, "image_provider", "outsee")
    monkeypatch.setattr(settings, "media_spend_recording", True)
    monkeypatch.setattr(idem, "_ensured", set())
    provider = Provider()
    monkeypatch.setattr(outsee_http, "generate_image", provider.generate_image)
    monkeypatch.setattr(outsee_http, "outsee_api_configured", lambda: True)

    class E:
        pass

    e = E()
    e.provider, e.sessions, e.tmp = provider, sessions, tmp_path
    e.n = 0

    def out(name="frame_012"):
        e.n += 1
        return tmp_path / f"{name}_{e.n:08x}.png"

    async def gen(prompt="кот на крыше", *, idempotent=True, out_path=None, model="nano-banana-2", pid=PID, **kw):
        return await outsee_retry.generate_image_with_retries(
            None, None, prompt=prompt, out_path=out_path or out(), model_slug=model, project_id=pid,
            idempotent=idempotent, max_attempts_per_prompt=1, gpt_rewrite=False, **kw)

    async def attempts(key="frame_012"):
        async with sessions() as s:
            return await idem._attempts(s, PID, key)

    async def events(key="frame_012"):
        async with sessions() as s:
            rows = (await s.scalars(select(idem.OutseeIdempotency).order_by(idem.OutseeIdempotency.id))).all()
        return [(r.event, r.note) for r in rows if r.key == key]

    e.out, e.gen, e.attempts, e.events = out, gen, attempts, events
    yield e
    await media_spend.drain()
    await engine.dispose()


def lost_answer():
    return OutseeSubmitUnknownError("no answer")



def test_request_key_is_sha256_of_project_frame_model_and_prompt():
    a = idem.request_sha(7, "frame_012", "nano-banana-2", "кот")
    assert len(a) == 64 and a == idem.request_sha(7, "frame_012", "nano-banana-2", "кот")
    assert a == hashlib.sha256("\x1f".join(["7", "frame_012", "nano-banana-2", "кот"]).encode()).hexdigest()
    for other in (idem.request_sha(8, "frame_012", "nano-banana-2", "кот"),
                  idem.request_sha(7, "frame_013", "nano-banana-2", "кот"),
                  idem.request_sha(7, "frame_012", "gpt-image-2-vip", "кот"),
                  idem.request_sha(7, "frame_012", "nano-banana-2", "кот2")):
        assert other != a


def test_context_key_drops_the_per_attempt_hex_and_rejects_unkeyed_files(tmp_path):
    ok = idem.make_ctx(7, tmp_path / "frame_012_ab12cd34.png", "m", "p")
    assert ok.key == "frame_012"
    assert idem.make_ctx(7, tmp_path / "char_ab12cd34.png", "m", "p").key == ""
    assert idem.make_ctx(None, tmp_path / "frame_012_ab12cd34.png", "m", "p").project_id is None



async def test_answer_lost_after_send_blocks_a_second_paid_request(env):
    env.provider.script = [lost_answer()]
    with pytest.raises(OutseeSubmitUnknownError):
        await env.gen()
    assert env.provider.calls == 1
    with pytest.raises(idem.OutseeIdempotencyUncertainError) as caught:
        await env.gen()
    assert env.provider.calls == 1, "второй платный запрос не должен уйти"
    assert is_no_resubmit(caught.value)
    assert caught.value.context["code"] == idem.CODE_UNCERTAIN and caught.value.context["human_decision"] is True
    states = [e for e, _ in await env.events()]
    assert states[0] == "intent" and "uncertain" in states


async def test_killed_process_after_intent_is_read_as_uncertain_and_blocks(env):
    env.provider.script = [ProcessDied()]
    with pytest.raises(ProcessDied):
        await env.gen()
    assert [e for e, _ in await env.events()] == ["intent"]
    verdict = await idem.assess(PID, "frame_012")
    assert verdict["mode"] == "uncertain" and verdict["attempts"][0]["interrupted"] is True
    with pytest.raises(OutseeImageError) as caught:
        await env.gen()
    assert is_no_resubmit(caught.value) and env.provider.calls == 1


async def test_other_prompt_of_the_same_frame_is_blocked_too(env):
    env.provider.script = [lost_answer()]
    with pytest.raises(OutseeSubmitUnknownError):
        await env.gen("первый промт")
    with pytest.raises(idem.OutseeIdempotencyUncertainError):
        await env.gen("совсем другой промт")
    assert env.provider.calls == 1


async def test_other_frame_and_other_project_are_independent(env):
    env.provider.script = [lost_answer()]
    with pytest.raises(OutseeSubmitUnknownError):
        await env.gen()
    await env.gen(out_path=env.out("frame_013"))
    await env.gen(pid=PID + 1)
    assert env.provider.calls == 3



async def test_result_is_recorded_and_the_restart_takes_it_without_paying(env):
    first = await env.gen()
    assert env.provider.calls == 1
    atts = await env.attempts()
    assert [a["state"] for a in atts] == ["result"]
    assert atts[0]["file_path"] == str(first.file_path) and atts[0]["file_sha256"] == hashlib.sha256(PNG).hexdigest()
    again = await env.gen()
    assert env.provider.calls == 1, "второй платный запрос не должен уйти"
    assert Path(again.file_path) == Path(first.file_path)
    assert again.gen_id.startswith("idem:") and again.raw_url == "https://cdn.example/x.png"


async def test_consumed_result_is_not_reused_so_a_regeneration_is_a_new_request(env):
    await env.gen()
    assert await idem.mark_consumed(PID, "frame_012") == 1
    await env.gen()
    assert env.provider.calls == 2
    assert await idem.mark_consumed(PID, "frame_012") == 1


async def test_changed_prompt_or_model_is_a_new_request(env):
    await env.gen("промт один")
    await env.gen("промт два")
    await env.gen("промт один", model="gpt-image-2-vip")
    assert env.provider.calls == 3


async def test_a_changed_or_missing_result_file_is_not_adopted(env):
    first = await env.gen()
    Path(first.file_path).write_bytes(PNG + b"tampered")
    await env.gen()
    assert env.provider.calls == 2
    Path((await env.attempts())[-1]["file_path"]).unlink()
    await env.gen()
    assert env.provider.calls == 3


async def test_result_older_than_the_window_is_not_adopted(env, monkeypatch):
    await env.gen()
    monkeypatch.setattr(media_resume, "window_s", lambda: 1)
    import asyncio
    await asyncio.sleep(1.2)
    await env.gen()
    assert env.provider.calls == 2


async def test_recovery_verdict_assess_reports_the_adoptable_file(env):
    first = await env.gen()
    verdict = await idem.assess(PID, "frame_012", idem.request_sha(PID, "frame_012", "nano-banana-2", "кот на крыше"))
    assert verdict["mode"] == "adopt" and verdict["file_path"] == str(first.file_path)
    assert (await idem.assess(PID, "frame_099"))["mode"] == "free"



async def test_download_failure_after_generation_is_uncertain_until_the_download_only_retry_records_it(env, monkeypatch):
    async def generated_but_not_downloaded(out_path):
        outsee_http._probe_mark(task_id="G-1", done=True)
        raise OutseeDownloadError("download", context={"img_url": "https://cdn.example/x.png", "gen_id": "G-1"})

    async def download(url, out_path):
        Path(out_path).write_bytes(PNG)

    monkeypatch.setattr(outsee_http, "_download", download)
    env.provider.script = [generated_but_not_downloaded]
    result = await env.gen()
    assert env.provider.calls == 1 and Path(result.file_path).is_file()
    states = [e for e, _ in await env.events()]
    assert states == ["intent", "uncertain", "result"]
    again = await env.gen()
    assert env.provider.calls == 1 and again.gen_id.startswith("idem:")


async def test_download_failure_that_never_recovers_stays_uncertain(env, monkeypatch):
    async def generated_but_not_downloaded(out_path):
        outsee_http._probe_mark(task_id="G-1", done=True)
        raise OutseeDownloadError("download", context={})

    env.provider.script = [generated_but_not_downloaded]
    with pytest.raises(OutseeDownloadError):
        await env.gen()
    with pytest.raises(idem.OutseeIdempotencyUncertainError):
        await env.gen()
    assert env.provider.calls == 1



async def test_provider_declared_failure_is_a_known_outcome_and_the_next_call_may_pay(env):
    failed = OutseeApiError("Outsee generation failed", context={"id": "G-1", "status": {"status": "failed"}})

    async def accepted_then_failed(out_path):
        outsee_http._probe_mark(task_id="G-1")
        raise failed

    env.provider.script = [accepted_then_failed]
    with pytest.raises(OutseeApiError):
        await env.gen()
    assert [e for e, _ in await env.events()] == ["intent", "failed"]
    await env.gen()
    assert env.provider.calls == 2


async def test_never_sent_error_does_not_block(env):
    class Refused(OutseeApiError):
        pass

    async def refused(out_path):
        outsee_http._probe_mark(task_id="G-2", rejected=True)
        raise Refused("bad request", context={"status": 400, "code": "invalid_request"})

    env.provider.script = [refused]
    with pytest.raises(OutseeApiError):
        await env.gen()
    await env.gen()
    assert env.provider.calls == 2



async def test_human_release_allows_a_new_request(env):
    env.provider.script = [lost_answer()]
    with pytest.raises(OutseeSubmitUnknownError):
        await env.gen()
    assert await idem.release(PID, "frame_012") == 1
    assert (await idem.assess(PID, "frame_012"))["mode"] == "free"
    await env.gen()
    assert env.provider.calls == 2


async def test_existing_ledger_release_by_a_human_also_unblocks(env):
    env.provider.script = [lost_answer()]
    with pytest.raises(OutseeSubmitUnknownError):
        await env.gen()
    assert await outsee_guard.release_unknown(PID, "frame_012") == 1
    await env.gen()
    assert env.provider.calls == 2


async def test_human_names_the_found_file_and_the_next_call_takes_it(env):
    env.provider.script = [lost_answer()]
    with pytest.raises(OutseeSubmitUnknownError):
        await env.gen()
    found = env.tmp / "found_in_outsee_history.png"
    found.write_bytes(PNG)
    assert await idem.adopt_file(PID, "frame_012", found) is True
    got = await env.gen()
    assert env.provider.calls == 1 and Path(got.file_path) == found
    assert await idem.adopt_file(PID, "frame_012", env.tmp / "missing.png") is False



async def test_resume_only_returns_a_recorded_result_and_sends_nothing_otherwise(env):
    recorded = await env.gen()
    hits: list = []
    with media_spend.resume_only(hits):
        got = await env.gen()
        assert Path(got.file_path) == Path(recorded.file_path)
        with pytest.raises(idem.OutseeResumeOnlyError) as caught:
            await env.gen("промт, которого не было", out_path=env.out("frame_050"))
    assert env.provider.calls == 1
    assert caught.value.context["code"] == idem.CODE_RESUME_ONLY and caught.value.context["request_sent"] is False
    assert is_no_resubmit(caught.value) and hits
    assert await env.events("frame_050") == []


async def test_resume_only_with_an_uncertain_attempt_still_refuses(env):
    env.provider.script = [lost_answer()]
    with pytest.raises(OutseeSubmitUnknownError):
        await env.gen()
    with media_spend.resume_only():
        with pytest.raises(idem.OutseeIdempotencyUncertainError):
            await env.gen()
    assert env.provider.calls == 1



async def test_no_durable_intent_means_no_paid_request(env, monkeypatch):
    async def broken_write(ctx, attempt, event, **kw):
        return False

    monkeypatch.setattr(idem, "_write", broken_write)
    with pytest.raises(idem.OutseeIdempotencyUncertainError) as caught:
        await env.gen()
    assert env.provider.calls == 0 and caught.value.context["request_sent"] is False


async def test_without_the_flag_the_old_behaviour_is_unchanged(env):
    await env.gen(idempotent=False)
    await env.gen(idempotent=False)
    assert env.provider.calls == 2
    assert await env.events() == []


async def test_window_zero_switches_the_layer_off(env, monkeypatch):
    monkeypatch.setattr(media_resume, "window_s", lambda: 0)
    await env.gen()
    await env.gen()
    assert env.provider.calls == 2 and await env.events() == []


async def test_files_without_a_frame_key_are_not_touched(env):
    await env.gen(out_path=env.tmp / "cover_ab12cd34.png")
    await env.gen(out_path=env.tmp / "cover_ab12cd35.png")
    assert env.provider.calls == 2 and await env.events("cover") == []


async def test_the_log_is_append_only(env):
    await env.gen()
    from sqlalchemy import text
    async with env.sessions() as s:
        with pytest.raises(Exception, match="append-only"):
            await s.execute(text("UPDATE outsee_idempotency SET note='x'"))
        await s.rollback()
    async with env.sessions() as s:
        with pytest.raises(Exception, match="append-only"):
            await s.execute(text("DELETE FROM outsee_idempotency"))
        await s.rollback()


async def test_kie_slug_without_a_bot_keeps_its_typed_error_and_writes_nothing(env, monkeypatch):
    from app.services import media_route
    monkeypatch.setattr(media_route, "image_provider_for", lambda slug: "kie")
    with pytest.raises(outsee_retry.KieSlugWithoutOutseeBotError):
        await env.gen(model="flux-2-pro")
    assert env.provider.calls == 0 and await env.events() == []
