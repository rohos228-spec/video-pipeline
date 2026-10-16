from __future__ import annotations

import os
import shutil
import wave
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bots import outsee_http
from app.models import Base
from app.services import (actions_recovery, media_admission, media_local_resume as local, media_recovery, media_resume,
                          media_spend, outsee_idempotency as idem, spend_guard)
from app.services.media_admission import MediaAdmissionDenied

PID = 7
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffprobe is required for audio probes")


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
    monkeypatch.setattr(idem, "_ensured", set())
    paid: list = []

    async def forbidden(*a, **k):
        paid.append((a, k))
        raise AssertionError("a provider was contacted by the supervised recovery")

    monkeypatch.setattr(outsee_http, "generate_image", forbidden)
    data = tmp_path / "project"
    (data / "scenes").mkdir(parents=True)
    project = SimpleNamespace(id=PID, data_dir=data, meta={})

    class E:
        pass

    e = E()
    e.sessions, e.project, e.data, e.tmp, e.paid = sessions, project, data, tmp_path, paid

    def png(path: Path, size=(320, 320)):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(path, "PNG")
        return path

    async def record(key, file: Path, prompt="p", model="nano-banana-2", *, consume=False):
        ctx = idem.make_ctx(PID, file.parent / f"{key}_ab12cd34.png", model, prompt)
        attempt = "a" * 32
        await idem._write(ctx, attempt, "intent")
        await idem._write(ctx, attempt, "result", file_path=str(file), file_size=file.stat().st_size,
                          fsha=idem.file_sha256(file))
        if consume:
            await idem._write(ctx, attempt, "consumed")
        return ctx

    async def unresolved(key, prompt="p", model="nano-banana-2"):
        ctx = idem.make_ctx(PID, e.data / "scenes" / f"{key}_ab12cd34.png", model, prompt)
        await idem._write(ctx, "b" * 32, "intent")
        return ctx

    async def kie_row(key, task_id, status, event="bound"):
        async with sessions() as s:
            await spend_guard.append_task_event(s, project_id=PID, key=key, task_id=task_id, provider="kie", api="jobs",
                                                model="z-image", event=event, status=status, note="ph=0123456789abcdef")
            await s.commit()

    e.png, e.record, e.unresolved, e.kie_row = png, record, unresolved, kie_row
    yield e
    await media_spend.drain()
    await engine.dispose()



@pytest.mark.parametrize("node,expected_gate", [
    ("images", "local"), ("hero", "local"), ("items", "local"), ("audio", "local"), ("sfx_gen", "local"),
    ("videos", "kie"), ("music", "kie"), ("assemble", "kie"),
])
def test_every_media_node_is_routed_to_exactly_one_admission_module(node, expected_gate):
    gate = actions_recovery._media_gate(node)
    assert (gate is local) == (expected_gate == "local")
    assert (gate is media_admission) == (expected_gate == "kie")


@pytest.mark.parametrize("node", ["plan", "script", "split", "sd_agent", "excel_gpt"])
async def test_a_text_node_is_never_admitted_by_the_local_resume(env, node):
    assert local.admitted(node) is False
    with pytest.raises(MediaAdmissionDenied) as caught:
        await local.admit(env.project, node)
    assert caught.value.code == "node_not_admitted"
    with pytest.raises(ValueError):
        await local.assess(env.project, node)



@pytest.mark.parametrize("node,key,folder", [("images", "frame_012", "scenes"), ("hero", "hero_1_v1", "characters"),
                                             ("items", "predmet2", "items")])
async def test_recorded_outsee_result_is_resume_local_and_verified_without_a_request(env, node, key, folder):
    file = env.png(env.data / folder / f"{key}_0a1b2c3d.png")
    await env.record(key, file)
    ledger_before = await media_admission.task_fingerprint(PID)
    rows_before = await local.request_key_fingerprint(PID)
    admission = await local.admit(env.project, node)
    assert admission["operation"] == "resume_local"
    assert admission["verdict"]["code"] == "outsee_result_on_record"
    outcome = await local.run_and_verify(env.project, node, admission)
    assert outcome["verified"] is True, outcome
    assert outcome["new_paid_requests"] == 0 and outcome["new_provider_task_ids"] == []
    assert outcome["node_started"] is False and outcome["semantic_quality_verified"] is False
    assert [f["key"] for f in outcome["files"]] == [key] and outcome["files"][0]["status"] == "passed"
    assert env.paid == []
    assert await media_admission.task_fingerprint(PID) == ledger_before
    assert await local.request_key_fingerprint(PID) == rows_before


async def test_a_result_the_step_already_consumed_is_nothing_to_do(env):
    file = env.png(env.data / "scenes" / "frame_001_0a1b2c3d.png")
    await env.record("frame_001", file, consume=True)
    with pytest.raises(MediaAdmissionDenied) as caught:
        await local.admit(env.project, "images")
    assert caught.value.code == "nothing_to_do"


async def test_an_attempt_without_a_verdict_is_denied_and_a_human_decides(env):
    await env.unresolved("frame_003")
    with pytest.raises(MediaAdmissionDenied) as caught:
        await local.admit(env.project, "images")
    assert caught.value.code == "verdict_denied"
    report = await local.assess(env.project, "images")
    assert report["verdict"]["code"] == "unknown_outcome_outsee"
    assert report["keys"][-1]["attempts"][0]["interrupted"] is True
    assert report["submits_new_paid_request"] is False


async def test_the_strongest_key_wins_one_denied_key_denies_the_node(env):
    await env.record("frame_001", env.png(env.data / "scenes" / "frame_001_0a1b2c3d.png"))
    await env.unresolved("frame_002")
    with pytest.raises(MediaAdmissionDenied) as caught:
        await local.admit(env.project, "images")
    assert caught.value.code == "verdict_denied"


async def test_a_picture_outside_the_project_folder_fails_the_verification(env):
    outside = env.png(env.tmp / "elsewhere" / "frame_004_0a1b2c3d.png")
    await env.record("frame_004", outside)
    admission = await local.admit(env.project, "images")
    outcome = await local.run_and_verify(env.project, "images", admission)
    assert outcome["verified"] is False
    assert any("file_path_outside_project" in p for p in outcome["problems"]), outcome["problems"]


async def test_a_corrupt_picture_fails_the_verification(env):
    file = env.png(env.data / "scenes" / "frame_005_0a1b2c3d.png")
    await env.record("frame_005", file)
    admission = await local.admit(env.project, "images")
    file.write_bytes(file.read_bytes()[:40] + b"x" * 2000)
    outcome = await local.run_and_verify(env.project, "images", admission)
    assert outcome["verified"] is False and outcome["problems"]


async def test_nothing_recorded_is_nothing_to_do(env):
    with pytest.raises(MediaAdmissionDenied) as caught:
        await local.admit(env.project, "hero")
    assert caught.value.code == "nothing_to_do"


async def test_a_known_kie_task_still_resumes_by_polling_and_a_failed_one_is_not_resent(env):
    await env.kie_row("frame_020", "kie-task-1", "submitted")
    admission = await local.admit(env.project, "images")
    assert admission["operation"] == "resume_poll"
    assert [(k["mode"], k["task_id"]) for k in admission["report"]["keys"]] == [("resume_poll", "kie-task-1")]
    await env.kie_row("hero_1_v1", "kie-task-2", "failed", event="failed")
    with pytest.raises(MediaAdmissionDenied) as caught:
        await local.admit(env.project, "hero")
    assert caught.value.code == "retry_same_not_admitted"


async def test_without_a_kie_key_the_request_key_log_still_decides(env, monkeypatch):
    monkeypatch.setattr(media_spend, "_enabled", lambda p: p != "kie")
    await env.record("frame_030", env.png(env.data / "scenes" / "frame_030_0a1b2c3d.png"))
    assert media_resume.enabled() is False
    admission = await local.admit(env.project, "images")
    assert admission["operation"] == "resume_local"


async def test_the_window_zero_keeps_the_blanket_denial(env, monkeypatch):
    await env.record("frame_031", env.png(env.data / "scenes" / "frame_031_0a1b2c3d.png"))
    monkeypatch.setattr(media_resume, "window_s", lambda: 0)
    with pytest.raises(MediaAdmissionDenied) as caught:
        await local.admit(env.project, "images")
    assert caught.value.code == "verdict_denied"


async def test_a_human_decision_unblocks_and_the_result_becomes_resumable(env):
    await env.unresolved("predmet1")
    found = env.png(env.data / "items" / "found_0a1b2c3d.png")
    assert await idem.adopt_file(PID, "predmet1", found) is True
    admission = await local.admit(env.project, "items")
    assert admission["operation"] == "resume_local"
    outcome = await local.run_and_verify(env.project, "items", admission)
    assert outcome["verified"] is True



def _wav(path: Path, seconds=0.5):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x10\x20" * int(16000 * seconds))
    return path


@needs_ffmpeg
async def test_audio_finished_file_is_resume_local_and_verified(env):
    voice = _wav(env.data / "audio" / "voice_01.wav")
    voice.with_name(voice.name + ".tts.json").write_text("{}", encoding="utf-8")
    admission = await local.admit(env.project, "audio")
    assert admission["operation"] == "resume_local" and admission["verdict"]["code"] == "finished_file_on_disk"
    outcome = await local.run_and_verify(env.project, "audio", admission)
    assert outcome["verified"] is True, outcome
    assert outcome["files"][0]["status"] == "passed" and outcome["new_paid_requests"] == 0


async def test_audio_with_an_uncertain_marker_and_no_file_is_denied(env):
    (env.data / "audio").mkdir()
    (env.data / "audio" / ".tts_uncertain_abc").write_text("x", encoding="utf-8")
    with pytest.raises(MediaAdmissionDenied) as caught:
        await local.admit(env.project, "audio")
    assert caught.value.code == "verdict_denied"
    assert (await local.assess(env.project, "audio"))["verdict"]["code"] == "unknown_outcome_elevenlabs"


async def test_audio_with_nothing_on_disk_is_a_paid_resend_that_the_supervisor_does_not_admit(env):
    with pytest.raises(MediaAdmissionDenied) as caught:
        await local.admit(env.project, "audio")
    assert caught.value.code == "retry_same_not_admitted"



def _sfx_project(env, files, inflight=None):
    jobs = {"sfx_files": {"files": files}}
    if inflight:
        jobs["sfx_progress"] = {"events": {}, "inflight": inflight}
    return SimpleNamespace(id=PID, data_dir=env.data, meta={"ai_jobs": jobs})


@needs_ffmpeg
async def test_sfx_event_files_on_record_are_resume_local_and_verified(env):
    p0 = _wav(env.data / "sfx" / "sfx_00_whoosh.wav")
    p1 = _wav(env.data / "sfx" / "sfx_01_hit.wav")
    project = _sfx_project(env, [{"idx": 0, "path": str(p0), "provider": "elevenlabs"},
                                 {"idx": 1, "path": str(p1), "provider": "elevenlabs"},
                                 {"idx": 2, "path": str(p1), "provider": "local_synth", "synthetic": True}])
    admission = await local.admit(project, "sfx_gen")
    assert admission["operation"] == "resume_local"
    assert [k["key"] for k in admission["report"]["keys"] if k["mode"] == "resume_local"] == ["sfx_0", "sfx_1"]
    outcome = await local.run_and_verify(project, "sfx_gen", admission)
    assert outcome["verified"] is True and len(outcome["files"]) == 2 and outcome["new_paid_requests"] == 0


async def test_sfx_event_sent_without_an_answer_is_denied(env):
    p0 = _wav(env.data / "sfx" / "sfx_00_whoosh.wav")
    project = _sfx_project(env, [{"idx": 0, "path": str(p0), "provider": "elevenlabs"}], inflight={"1": {"kind": "hit"}})
    with pytest.raises(MediaAdmissionDenied) as caught:
        await local.admit(project, "sfx_gen")
    assert caught.value.code == "verdict_denied"


async def test_sfx_fallback_after_a_timeout_is_denied_and_after_an_answer_is_not_resent(env):
    p0 = _wav(env.data / "sfx" / "sfx_00_whoosh.wav")
    after_timeout = _sfx_project(env, [{"idx": 0, "path": str(p0), "provider": "local_synth",
                                        "provider_fallback": {"may_have_charged": "uncertain"}}])
    with pytest.raises(MediaAdmissionDenied) as denied:
        await local.admit(after_timeout, "sfx_gen")
    assert denied.value.code == "verdict_denied"
    after_answer = _sfx_project(env, [{"idx": 0, "path": str(p0), "provider": "local_synth",
                                       "provider_fallback": {"may_have_charged": "no"}}])
    with pytest.raises(MediaAdmissionDenied) as resend:
        await local.admit(after_answer, "sfx_gen")
    assert resend.value.code == "retry_same_not_admitted"


async def test_sfx_without_files_is_not_admitted_as_a_resume(env):
    with pytest.raises(MediaAdmissionDenied) as caught:
        await local.admit(_sfx_project(env, []), "sfx_gen")
    assert caught.value.code == "nothing_to_do"



async def test_the_local_verification_runs_inside_the_resume_only_scope(env, monkeypatch):
    file = env.png(env.data / "scenes" / "frame_040_0a1b2c3d.png")
    await env.record("frame_040", file)
    admission = await local.admit(env.project, "images")
    seen: list[bool] = []
    from app.services import media_verification
    real = media_verification._probe

    async def spy(*a, **k):
        seen.append(media_spend.in_resume_only())
        return await real(*a, **k)

    monkeypatch.setattr(media_verification, "_probe", spy)
    outcome = await local.run_and_verify(env.project, "images", admission)
    assert outcome["verified"] is True and seen == [True]
    assert media_spend.in_resume_only() is False


async def test_a_node_outside_the_set_cannot_be_run_by_the_local_resume(env):
    with pytest.raises(ValueError):
        await local.run_and_verify(env.project, "videos", {"operation": "resume_local", "report": {"keys": []}})
