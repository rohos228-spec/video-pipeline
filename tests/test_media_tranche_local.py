from __future__ import annotations

import ast
import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (Artifact, ArtifactKind, Base, HITLDecision, HITLKind, HITLRequest, Project,
                        ProjectStatus)
from app.orchestrator import auto_advance as aa
from app.orchestrator.steps import assemble as asm
from app.services import media_recovery, media_spend
from app.services.elevenlabs_api import (ElevenLabsApiError, ElevenLabsUncertainError, synthesize_speech,
                                         tts_run_budget)
from app.services.step_cancel import StepCancelledError
from app.settings import settings

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")
_REAL_CLIENT = httpx.AsyncClient
_MP3 = b"ID3" + b"\x00" * 2000



class _Net:
    def __init__(self):
        self.script: list = []
        self.posts = 0


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
        kw.pop("proxy", None)

        def handler(request: httpx.Request) -> httpx.Response:
            holder.posts += 1
            step = holder.script.pop(0) if holder.script else 200
            if isinstance(step, Exception):
                raise step
            return httpx.Response(step, content=_MP3 if step == 200 else b'{"detail":"x"}')

        kw["transport"] = httpx.MockTransport(handler)
        return _REAL_CLIENT(**kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return holder


def _project(tmp_path):
    return SimpleNamespace(id=1, data_dir=tmp_path, meta={})


def test_audio_timeout_after_send_is_typed_held_and_never_reposted(tmp_path, net):
    net.script = [httpx.ReadTimeout("slow")]
    audio = tmp_path / "audio"
    with pytest.raises(ElevenLabsUncertainError):
        asyncio.run(synthesize_speech("Закадровый текст.", audio / "frame_001.mp3"))
    assert net.posts == 1
    verdict = asyncio.run(media_recovery.assess(_project(tmp_path), "audio"))
    assert (verdict["verdict"]["mode"], verdict["verdict"]["code"]) == ("denied", "unknown_outcome_elevenlabs")
    assert verdict["keys"][0]["markers"] and not verdict["submits_new_paid_request"]
    for _ in range(2):
        with pytest.raises(ElevenLabsUncertainError):
            asyncio.run(synthesize_speech("Закадровый текст.", audio / "frame_001.mp3"))
    assert net.posts == 1


def test_audio_resume_only_scope_never_sends_a_post(tmp_path, net):
    async def go():
        with media_spend.resume_only():
            await synthesize_speech("Совсем новый текст.", tmp_path / "audio" / "frame_002.mp3")

    with pytest.raises(media_spend.ResumeOnlyBlocked):
        asyncio.run(go())
    assert net.posts == 0


def test_audio_finished_file_is_resumed_locally_even_in_the_resume_only_scope(tmp_path, net):
    audio = tmp_path / "audio"
    asyncio.run(synthesize_speech("Готовая реплика.", audio / "frame_001.mp3"))
    assert net.posts == 1
    verdict = asyncio.run(media_recovery.assess(_project(tmp_path), "audio"))
    assert (verdict["verdict"]["mode"], verdict["verdict"]["code"]) == ("resume_local", "finished_file_on_disk")

    async def again():
        with media_spend.resume_only():
            return await synthesize_speech("Готовая реплика.", audio / "frame_009.mp3")

    out = asyncio.run(again())
    assert net.posts == 1 and out.read_bytes() == _MP3


def test_audio_a_stale_marker_does_not_deny_when_the_finished_file_exists(tmp_path, net):
    net.script = [httpx.ReadTimeout("slow")]
    audio = tmp_path / "audio"
    with pytest.raises(ElevenLabsUncertainError):
        asyncio.run(synthesize_speech("Первая.", audio / "frame_001.mp3"))
    (audio / "voice_full.mp3").write_bytes(_MP3)
    verdict = asyncio.run(media_recovery.assess(_project(tmp_path), "audio"))
    assert verdict["verdict"]["mode"] == "resume_local" and verdict["keys"][0]["stale_markers"]


def test_audio_retry_is_allowed_only_without_job_and_marker_and_stays_bounded(tmp_path, net):
    audio = tmp_path / "audio"
    net.script = [httpx.ConnectError("refused"), httpx.ConnectError("refused")]
    with pytest.raises(ElevenLabsApiError):
        asyncio.run(synthesize_speech("Нет связи.", audio / "frame_001.mp3"))
    assert net.posts == 2
    verdict = asyncio.run(media_recovery.assess(_project(tmp_path), "audio"))
    assert (verdict["verdict"]["mode"], verdict["verdict"]["code"]) == ("retry_same", "no_paid_job_and_no_marker")
    assert verdict["retry_same_max"] == 1
    asyncio.run(synthesize_speech("Нет связи.", audio / "frame_001.mp3"))
    assert net.posts == 3
    assert not list(audio.glob(".tts_uncertain_*"))


def test_audio_answered_error_is_not_billed_and_does_not_leave_a_marker(tmp_path, net):
    net.script = [500]
    with pytest.raises(ElevenLabsApiError, match="500"):
        asyncio.run(synthesize_speech("Ответ с ошибкой.", tmp_path / "audio" / "frame_001.mp3"))
    assert not list((tmp_path / "audio").glob(".tts_uncertain_*"))
    assert asyncio.run(media_recovery.assess(_project(tmp_path), "audio"))["verdict"]["mode"] == "retry_same"


def test_audio_run_budget_still_caps_posts(tmp_path, net):
    async def go():
        with tts_run_budget(1):
            await synthesize_speech("Один.", tmp_path / "a.mp3")
            await synthesize_speech("Два.", tmp_path / "b.mp3")

    with pytest.raises(ElevenLabsApiError, match="AUDIO_MAX_TTS_POSTS_PER_RUN"):
        asyncio.run(go())
    assert net.posts == 1



def _sfx_setup(monkeypatch, tmp_path, n_events, script):
    from app.models import Project as P
    from app.services import sfx_gen
    from app.services.sfx_plan import SfxEvent

    monkeypatch.setattr(settings, "elevenlabs_api_key", "stub")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(sfx_gen, "verify_audio_file", lambda *a, **k: [])
    calls: list[str] = []

    async def fake_sfx(prompt, duration, out_path):
        calls.append(prompt)
        step = script.pop(0) if script else None
        if step is not None:
            raise step
        out_path.write_bytes(b"x" * 2000)
        return out_path

    monkeypatch.setattr(sfx_gen, "_elevenlabs_sfx", fake_sfx)
    import app.services.observation as obs

    facts: list = []
    monkeypatch.setattr(obs, "emit", lambda kind, payload=None, **k: facts.append((kind, payload)))
    project = P(slug="sfx-tranche", title="t", topic="t")
    project.meta = {}
    events = [SfxEvent(1, 0.0, 1.0, "hit", f"p{i}", 0.5, False) for i in range(n_events)]
    return sfx_gen, project, events, calls, facts


class _Sess:
    async def flush(self):
        return None


def _gen(sfx_gen, project, events, **kw):
    return asyncio.run(sfx_gen.generate_sfx_files(_Sess(), project, events, **kw))


def test_sfx_retry_by_event_at_most_once_and_never_after_an_uncertain_fallback(monkeypatch, tmp_path):
    answered = RuntimeError("11labs sfx 500: x")
    script = [answered, httpx.ReadTimeout("t"), None]
    sfx_gen, project, events, calls, facts = _sfx_setup(monkeypatch, tmp_path, 3, script)
    first = _gen(sfx_gen, project, events)
    assert [f["provider"] for f in first] == ["local_synth", "local_synth", "elevenlabs"] and len(calls) == 3
    assert [(k, p["idx"]) for k, p in facts] == [("provider_fallback", 0), ("provider_fallback", 1)]

    assess = asyncio.run(media_recovery.assess(project, "sfx_gen"))
    verdicts = {i["key"]: (i["mode"], i["code"]) for i in assess["keys"]}
    assert verdicts == {"sfx_0": ("retry_same", "fallback_after_provider_answer"),
                        "sfx_1": ("denied", "unknown_outcome_elevenlabs")}

    _gen(sfx_gen, project, events)
    assert len(calls) == 3

    second = _gen(sfx_gen, project, events, retry_degraded=True)
    assert len(calls) == 4
    by_idx = {f["idx"]: f for f in second}
    assert by_idx[0]["provider"] == "elevenlabs" and by_idx[0]["provider_retry"] == 1
    assert by_idx[1]["provider"] == "local_synth" and "provider_retry" not in by_idx[1]
    assert by_idx[2]["provider"] == "elevenlabs"

    _gen(sfx_gen, project, events, retry_degraded=True)
    assert len(calls) == 4


def test_sfx_a_failed_retry_is_recorded_again_and_is_the_last_one(monkeypatch, tmp_path):
    script = [RuntimeError("11labs sfx 500: a"), RuntimeError("11labs sfx 503: b")]
    sfx_gen, project, events, calls, facts = _sfx_setup(monkeypatch, tmp_path, 1, script)
    _gen(sfx_gen, project, events)
    again = _gen(sfx_gen, project, events, retry_degraded=True)
    assert len(calls) == 2
    assert again[0]["provider"] == "local_synth" and again[0]["provider_retry"] == 1
    assert again[0]["provider_fallback"]["may_have_charged"] == "no"
    assert [k for k, _ in facts] == ["provider_fallback", "provider_fallback"]
    _gen(sfx_gen, project, events, retry_degraded=True)
    assert len(calls) == 2
    assess = asyncio.run(media_recovery.assess(project, "sfx_gen"))
    assert assess["verdict"]["mode"] == "denied" and assess["verdict"]["code"] == "retry_cap_reached"


def test_sfx_resume_only_scope_raises_instead_of_silently_falling_back(monkeypatch, tmp_path):
    from app.services import sfx_gen
    from app.services.sfx_plan import SfxEvent

    monkeypatch.setattr(settings, "elevenlabs_api_key", "stub")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    project = Project(slug="sfx-resume-only", title="t", topic="t")
    project.meta = {}
    events = [SfxEvent(1, 0.0, 1.0, "hit", "p", 0.5, False)]

    async def go():
        with media_spend.resume_only():
            await sfx_gen.generate_sfx_files(_Sess(), project, events)

    with pytest.raises(media_spend.ResumeOnlyBlocked):
        asyncio.run(go())
    assert list((project.data_dir / "sfx").glob("*")) == []


def test_sfx_step_asks_for_the_bounded_retry():
    src = Path("app/orchestrator/steps/generate_sfx.py").read_text(encoding="utf-8")
    assert "retry_degraded=True" in src



@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _new_project(session, slug="tr-asm"):
    p = Project(slug=slug, title="t", topic="t", status=ProjectStatus.assembling)
    session.add(p)
    await session.flush()
    return p


class Core:

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = 0

    async def __call__(self, session, project, bot, *, out_path, **kw):
        self.calls += 1
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        out_path.write_bytes(step)
        return 1.0, 0.0


def _wire(monkeypatch, core, *, real_verifier=False, verdicts=None):
    import app.services.artifact_verifier as av
    import app.services.mass_factory as mf

    sent: list[dict] = []
    emitted: list = []
    verdicts = list(verdicts or [])

    async def fake_verify(path, *, root, kind, expected=None):
        status = verdicts.pop(0) if verdicts else "passed"
        res = {"status": status, "kind": kind, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        if status != "passed":
            res["reason"] = "ValueError"
        return res

    async def send(bot, session, project, *, kind, video_path, caption, payload):
        req = HITLRequest(project_id=project.id, kind=kind, payload=payload)
        session.add(req)
        await session.flush()
        sent.append(payload)
        return req

    async def child(session, project):
        return None

    import app.services.observation as obs

    monkeypatch.setattr(asm, "_assemble_core", core)
    if not real_verifier:
        monkeypatch.setattr(av, "verify_artifact", fake_verify)
    monkeypatch.setattr(asm, "send_hitl_video", send)
    monkeypatch.setattr(mf, "on_child_montage_complete", child)
    monkeypatch.setattr(obs, "emit", lambda kind, payload=None, **k: emitted.append((kind, payload)))
    return sent, emitted


async def _approvals(session, project):
    return (await session.execute(select(HITLRequest).where(
        HITLRequest.project_id == project.id, HITLRequest.kind == HITLKind.approve_final))).scalars().all()


async def test_assemble_a_failed_build_gets_exactly_one_retry_and_then_succeeds(db, monkeypatch):
    p = await _new_project(db)
    core = Core(RuntimeError("ffmpeg exited with 1"), b"GOOD")
    sent, emitted = _wire(monkeypatch, core)
    await asm._assemble_body(db, p, None)
    assert core.calls == 2
    assert (p.data_dir / "final" / f"{p.slug}.mp4").read_bytes() == b"GOOD"
    assert len(sent) == 1 and len(await _approvals(db, p)) == 1
    assert emitted == [("assemble_retry", {"attempt": 1, "error_type": "RuntimeError", "retry_cap": 1})]
    assert [f.name for f in (p.data_dir / "final").iterdir()] == [f"{p.slug}.mp4"]


async def test_assemble_a_failed_verification_is_retried_once_and_a_second_failure_ends_it(db, monkeypatch):
    p = await _new_project(db)
    final = p.data_dir / "final" / f"{p.slug}.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"OLD")
    core = Core(b"BAD1", b"BAD2", b"NEVER")
    sent, emitted = _wire(monkeypatch, core, verdicts=["failed", "unknown"])
    with pytest.raises(RuntimeError, match="проверка итогового видео"):
        await asm._assemble_body(db, p, None)
    assert core.calls == 2
    assert final.read_bytes() == b"OLD" and sent == [] and await _approvals(db, p) == []
    assert p.status is ProjectStatus.assembling and len(emitted) == 1
    assert [f.name for f in final.parent.iterdir()] == [final.name]


async def test_assemble_input_errors_and_cancellation_are_not_retried(db, monkeypatch):
    p = await _new_project(db)
    core = Core(RuntimeError("нет клипов для кадров [3]"))
    _wire(monkeypatch, core)
    with pytest.raises(RuntimeError, match="нет клипов"):
        await asm._assemble_body(db, p, None)
    assert core.calls == 1
    core = Core(StepCancelledError("stop"))
    _wire(monkeypatch, core)
    with pytest.raises(StepCancelledError):
        await asm._assemble_body(db, p, None)
    assert core.calls == 1


def _mp4(path: Path, *, audio: bool, seconds: int = 1) -> bytes:
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=blue:s=64x96:r=10:d={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(args, check=True, timeout=60)
    return path.read_bytes()


@needs_ffmpeg
async def test_assemble_dry_run_with_the_real_verifier(db, monkeypatch, tmp_path):
    good = _mp4(tmp_path / "good.mp4", audio=True)
    silent = _mp4(tmp_path / "silent.mp4", audio=False)
    p = await _new_project(db, "tr-real")
    final = p.data_dir / "final" / f"{p.slug}.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"OLDVIDEO")

    core = Core(silent, silent)
    sent, _ = _wire(monkeypatch, core, real_verifier=True)
    with pytest.raises(RuntimeError, match="проверка итогового видео"):
        await asm._assemble_body(db, p, None)
    assert core.calls == 2 and final.read_bytes() == b"OLDVIDEO" and sent == []
    assert not [f for f in final.parent.iterdir() if f.name != final.name]

    core = Core(silent, good)
    sent, _ = _wire(monkeypatch, core, real_verifier=True)
    await asm._assemble_body(db, p, None)
    sha = hashlib.sha256(good).hexdigest()
    assert core.calls == 2 and final.read_bytes() == good
    assert [r.payload["sha256"] for r in await _approvals(db, p)] == [sha] and len(sent) == 1
    art = (await db.execute(select(Artifact).where(Artifact.kind == ArtifactKind.final_video))).scalars().one()
    assert art.meta["sha256"] == sha and art.meta["verification"]["status"] == "passed"
    assert art.meta["verification"]["streams"] == ["audio", "video"]

    core = Core(good)
    sent, _ = _wire(monkeypatch, core, real_verifier=True)
    await asm._assemble_body(db, p, None)
    assert sent == [] and len(await _approvals(db, p)) == 1


@needs_ffmpeg
async def test_approve_final_barrier_cannot_be_bypassed_by_auto_mode_or_a_tampered_file(db, monkeypatch, tmp_path):
    good = _mp4(tmp_path / "good.mp4", audio=True)
    silent = _mp4(tmp_path / "silent.mp4", audio=False)
    p = await _new_project(db, "tr-barrier")
    p.status = ProjectStatus.assembled
    p.auto_mode = True
    monkeypatch.setattr(settings, "hitl_auto_approve", True)
    final = p.data_dir / "final" / f"{p.slug}.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(good)
    sha = hashlib.sha256(good).hexdigest()
    db.add(Artifact(project_id=p.id, kind=ArtifactKind.final_video, uuid="f1", path=str(final), meta={"sha256": sha}))
    await db.flush()
    transition = aa.TRANSITIONS[ProjectStatus.assembled]

    final.write_bytes(silent)
    assert await aa._final_verified(db, p) is False
    await aa._apply_approve(db, p, None, transition)
    assert p.status is ProjectStatus.assembled
    pending = HITLRequest(project_id=p.id, kind=HITLKind.approve_final, payload={"sha256": sha})
    db.add(pending)
    await db.flush()
    await aa._apply_approve(db, p, pending, transition)
    assert p.status is ProjectStatus.assembled and pending.decision is HITLDecision.pending

    final.write_bytes(good)
    assert await aa._final_verified(db, p) is True

    p.status = ProjectStatus.assembled
    final.write_bytes(silent)
    human = HITLRequest(project_id=p.id, kind=HITLKind.approve_final, payload={"sha256": sha},
                        decision=HITLDecision.approved)
    db.add(human)
    await db.flush()
    from loguru import logger

    logs: list[str] = []
    sink = logger.add(lambda m: logs.append(str(m)), level="WARNING")
    try:
        await aa._apply_approve(db, p, human, transition)
    finally:
        logger.remove(sink)
    assert not any("verification not passed" in m for m in logs)



def _calls_named(path: str, name: str) -> list[ast.Call]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == name]


@pytest.mark.parametrize("path", ["app/orchestrator/steps/generate_hero.py", "app/orchestrator/steps/generate_items.py",
                                  "app/orchestrator/steps/generate_images.py"])
def test_every_image_generation_call_carries_the_project_id(path):
    calls = _calls_named(path, "generate_image_with_retries")
    assert calls, path
    for call in calls:
        assert "project_id" in {k.arg for k in call.keywords}, (path, call.lineno)


class _Rows:
    def scalars(self):
        return self

    def all(self):
        return []


class _Db:
    def __init__(self):
        self.added = []

    async def execute(self, *a, **k):
        return _Rows()

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    async def commit(self):
        return None


async def test_items_step_passes_its_project_to_the_generation_guard(monkeypatch, tmp_path):
    from contextlib import asynccontextmanager
    from app.orchestrator.steps import generate_items as gi

    seen: list[dict] = []

    async def fake_generate(outsee, gpt, **kw):
        seen.append(kw)
        kw["out_path"].write_bytes(b"png" * 500)
        return SimpleNamespace(file_path=kw["out_path"])

    @asynccontextmanager
    async def fake_browser():
        yield object()

    import app.services.vibecode_catalog as vc

    monkeypatch.setattr(gi, "generate_image_with_retries", fake_generate)
    monkeypatch.setattr(gi, "browser_session", fake_browser)
    monkeypatch.setattr(gi, "OutseeBot", lambda bs: object())
    monkeypatch.setattr(gi, "get_gpt_client", lambda: object())
    monkeypatch.setattr(gi, "_items_style_prompt", lambda project: "style")
    monkeypatch.setattr(vc, "resolve_node_media_settings", lambda project, node_type: {
        "image_generator_id": None, "resolution_id": None, "quality_slug": None, "aspect_slug": None})
    project = SimpleNamespace(id=42, status=ProjectStatus.generating_items, item_descriptions=["кубок"],
                              slug="items-guard", data_dir=tmp_path, meta={})
    await gi.run(_Db(), project, None)
    assert [kw["project_id"] for kw in seen] == [42]
    assert project.status is ProjectStatus.items_ready


async def _run_music(monkeypatch, tmp_path, *, pending: bool):
    from app.orchestrator.steps import generate_music as gm
    import app.bots.kie_http as kh
    import app.services.gpt_client as gc
    import app.services.gpt_text_builder as gtb
    import app.services.post_step_validate as psv
    from app.services import media_resume

    music_dir = tmp_path / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "voiceover.txt").write_text("Текст ролика.", encoding="utf-8")
    cached = "сохранённый промт Suno для этого проекта " * 2
    if pending:
        (music_dir / ".suno_prompt.txt").write_text(cached, encoding="utf-8")
    gpt_calls, generation = [], []

    class Gpt:
        async def new_conversation(self):
            return None

        async def ask_with_files(self, *a, **k):
            gpt_calls.append(1)
            return "свежий промт от GPT для музыки в этом проекте"

    async def run_generation(spec, payload, out_path, *, timeout_s, project_id=None):
        generation.append({"payload": payload, "project_id": project_id})
        return SimpleNamespace(file_path=out_path)

    async def finalize(*a, **k):
        return True

    monkeypatch.setattr(media_resume, "pending_for_key", AsyncMock(return_value=pending))
    monkeypatch.setattr(kh, "kie_configured", lambda: True)
    monkeypatch.setattr(kh, "run_generation", run_generation)
    monkeypatch.setattr(gc, "get_gpt_client", lambda: Gpt())
    monkeypatch.setattr(gtb, "get_effective_text", lambda *a, **k: "msg")
    monkeypatch.setattr(psv, "finalize_or_retry", finalize)
    monkeypatch.setattr(settings, "fleet_enabled", False)
    project = SimpleNamespace(id=11, status=ProjectStatus.generating_music, data_dir=tmp_path, topic="тема",
                              slug="m", meta={})
    await gm.run(_Db(), project, None)
    return gpt_calls, generation, music_dir, cached


async def test_music_with_an_unfinished_paid_task_reuses_the_saved_prompt_and_calls_no_gpt(monkeypatch, tmp_path):
    gpt_calls, generation, _, cached = await _run_music(monkeypatch, tmp_path, pending=True)
    assert gpt_calls == [] and generation[0]["project_id"] == 11
    assert cached.strip()[:20] in str(generation[0]["payload"])


async def test_music_normal_run_asks_gpt_once_saves_the_prompt_and_guards_the_submit(monkeypatch, tmp_path):
    gpt_calls, generation, music_dir, _ = await _run_music(monkeypatch, tmp_path, pending=False)
    assert gpt_calls == [1] and generation[0]["project_id"] == 11
    assert (music_dir / ".suno_prompt.txt").read_text(encoding="utf-8").startswith("свежий промт")



def test_suno_music_is_priced_and_keyed_in_the_spend_and_task_ledgers():
    from app.services import kie_catalog

    spec = kie_catalog.get_model("suno-music")
    payload = kie_catalog.build_payload(spec, {"customMode": False, "prompt": "p", "instrumental": True,
                                               "model": "V5_5"})
    price = media_spend.price_kie(spec, payload)
    assert price["unknown"] is False and price["credits"] > 0 and price["usd"] > 0
    assert media_spend._task_key("music_ab12cd34.mp3") == "music"
    with media_spend.kie_scope(spec, payload, Path("music_ab12cd34.mp3")):
        ctx = media_spend._kie_ctx.get()
    assert (ctx["key"], ctx["api"], ctx["unknown"]) == ("music", "suno", False)
