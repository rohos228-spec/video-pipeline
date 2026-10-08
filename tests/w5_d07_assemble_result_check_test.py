"""W5-3 / D07 (U/D): независимая проверка результата сборки и сохранность запроса approve_final.

* чистая проверка дорожек и длины по ответу ffprobe (check_assembly_tracks);
* настоящий ffprobe на маленьких файлах, собранных ffmpeg (пропуск, если ffmpeg нет);
* _assemble_body: доказанный брак проваливает попытку и ровно один раз повторяется, прежний final не тронут, запрос
  approve_final не создаётся; в журнал пишется событие artifact_verified (stage assembly_result);
* отказ Telegram после проверки не теряет запрос approve_final, сборка остаётся собранной, подтверждение не выдаётся.
Без сети, платных вызовов и Telegram.
"""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Artifact, ArtifactKind, Base, HITLDecision, HITLKind, HITLRequest, Project, ProjectStatus
from app.orchestrator.steps import assemble as asm

OK_PROBE = {"streams": [{"codec_type": "video", "duration": "10.0", "width": 1920, "height": 1080},
                        {"codec_type": "audio", "duration": "10.0"}],
            "format": {"duration": "10.0", "format_name": "mov,mp4"}}


def _probe(**changes):
    probe = {"streams": [dict(s) for s in OK_PROBE["streams"]], "format": dict(OK_PROBE["format"])}
    probe.update(changes)
    return probe


# ------------------------------------------------------------------ pure verdict -----------------------------------

def test_a_complete_file_passes_and_reports_its_tracks():
    check = asm.check_assembly_tracks(OK_PROBE, expected_duration=10.0, size_bytes=1000)
    assert check["status"] == "passed" and check["errors"] == []
    assert (check["video_tracks"], check["audio_tracks"]) == (1, 1)
    assert check["video_duration"] == check["audio_duration"] == check["format_duration"] == 10.0
    assert check["semantic_quality_verified"] is False


@pytest.mark.parametrize("probe, size, error", [
    (_probe(streams=[{"codec_type": "audio", "duration": "10.0"}]), 1000, "video_track_missing"),
    (_probe(streams=[{"codec_type": "video", "duration": "10.0"}]), 1000, "audio_track_missing"),
    (_probe(streams=[{"codec_type": "video", "duration": "10.0"}, {"codec_type": "audio", "duration": "N/A"}],
            format={"duration": "0"}), 1000, "audio_track_empty"),
    (_probe(streams=[{"codec_type": "video", "duration": "20.0"}, {"codec_type": "audio", "duration": "10.0"}],
            format={"duration": "20.0"}), 1000, "tracks_out_of_sync"),
    (_probe(format={"duration": "8.0"}), 1000, "shorter_than_voice"),
    (_probe(streams=[{"codec_type": "video", "duration": "60.0"}, {"codec_type": "audio", "duration": "60.0"}],
            format={"duration": "60.0"}), 1000, "longer_than_voice"),
    (OK_PROBE, 0, "file_empty"),
])
def test_proven_defects_fail_with_a_named_reason(probe, size, error):
    check = asm.check_assembly_tracks(probe, expected_duration=10.0, size_bytes=size)
    assert check["status"] == "failed" and error in check["errors"] and check["reason"] == check["errors"][0]


def test_a_missing_probe_is_unknown_never_a_failure_and_never_a_pass():
    for probe in (None, {}, {"streams": "x"}):
        check = asm.check_assembly_tracks(probe, expected_duration=10.0, size_bytes=5)
        assert check["status"] == "unknown" and check["reason"] == "probe_unavailable"


def test_tail_and_ratio_slack_keep_a_normal_montage_passing():
    # voice 10 s + 4 s tail: video runs 14 s, audio 10.5 s -> inside the slack (3 s + 5 % + tail)
    probe = _probe(streams=[{"codec_type": "video", "duration": "14.0"}, {"codec_type": "audio", "duration": "10.0"}],
                   format={"duration": "14.0"})
    assert asm.check_assembly_tracks(probe, expected_duration=10.0, tail_seconds=4.0, size_bytes=9)["status"] == "passed"
    assert asm.check_assembly_tracks(probe, expected_duration=10.0, tail_seconds=0.0, size_bytes=9)["status"] == "failed"


# ------------------------------------------------------------------ real ffprobe on tiny files --------------------

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe missing")


def _make(path: Path, *, audio: bool, seconds: float = 2.0) -> Path:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x90:rate=10"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"] + (["-c:a", "aac", "-shortest"] if audio else []) + [str(path)]
    subprocess.run(cmd, check=True, timeout=120)
    return path


@needs_ffmpeg
@pytest.mark.asyncio
async def test_real_file_with_both_tracks_passes_and_a_silent_file_fails(tmp_path):
    good = _make(tmp_path / "good.mp4", audio=True)
    silent = _make(tmp_path / "silent.mp4", audio=False)
    ok = await asm._verify_result_tracks(good, expected_duration=2.0, tail_seconds=0.0)
    assert ok["status"] == "passed" and ok["audio_tracks"] == 1 and ok["video_tracks"] == 1
    bad = await asm._verify_result_tracks(silent, expected_duration=2.0, tail_seconds=0.0)
    assert bad["status"] == "failed" and "audio_track_missing" in bad["errors"]
    short = await asm._verify_result_tracks(good, expected_duration=30.0, tail_seconds=0.0)
    assert short["status"] == "failed" and "shorter_than_voice" in short["errors"]


@pytest.mark.asyncio
async def test_an_unreadable_file_is_unknown_not_a_pass(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"NOT A VIDEO")
    check = await asm._verify_result_tracks(junk, expected_duration=1.0, tail_seconds=0.0)
    assert check["status"] == "unknown"


# ------------------------------------------------------------------ _assemble_body ---------------------------------

@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _project(session, slug="w5d07") -> Project:
    project = Project(slug=slug, title="t", topic="t", status=ProjectStatus.assembling)
    session.add(project)
    await session.flush()
    return project


class Wire:
    """Replaces the engine, verify_artifact, the probe, the journal and the Telegram send; records what happened."""

    def __init__(self, monkeypatch, *, content=b"NEWVIDEO", probes=None, send=None):
        import app.services.artifact_verifier as av
        import app.services.mass_factory as mf

        self.journal: list[tuple[str, dict]] = []
        self.probe_calls = 0
        self.cores = 0
        self.sent: list[dict] = []
        probes = list(probes if probes is not None else [OK_PROBE])

        async def core(session, project, bot, *, out_path, **kw):
            self.cores += 1
            out_path.write_bytes(content)
            return 10.0, 0.0

        async def verify(path, *, root, kind, expected=None):
            return {"status": "passed", "kind": kind, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

        async def probe(path):
            self.probe_calls += 1
            return probes.pop(0) if len(probes) > 1 else probes[0]

        async def journal(kind, payload):
            self.journal.append((kind, payload))

        async def default_send(bot, session, project, *, kind, video_path, caption, payload):
            req = HITLRequest(project_id=project.id, kind=kind, payload=payload)
            session.add(req)
            await session.flush()
            self.sent.append(payload)
            return req

        async def child(session, project):
            return None

        monkeypatch.setattr(asm, "_assemble_core", core)
        monkeypatch.setattr(av, "verify_artifact", verify)
        monkeypatch.setattr(asm, "_probe_tracks", probe)
        monkeypatch.setattr(asm, "_journal", journal)
        monkeypatch.setattr(asm, "send_hitl_video", send or default_send)
        monkeypatch.setattr(mf, "on_child_montage_complete", child)


async def _approvals(session, project):
    return (await session.execute(select(HITLRequest).where(
        HITLRequest.project_id == project.id, HITLRequest.kind == HITLKind.approve_final))).scalars().all()


async def _finals(session, project):
    return (await session.execute(select(Artifact).where(
        Artifact.project_id == project.id, Artifact.kind == ArtifactKind.final_video))).scalars().all()


@pytest.mark.asyncio
async def test_a_verified_result_is_journaled_and_its_check_is_stored_with_the_artifact(db, monkeypatch):
    project = await _project(db)
    wire = Wire(monkeypatch)
    await asm._assemble_body(db, project, None)
    events = [(k, p) for k, p in wire.journal if k == "artifact_verified"]
    assert len(events) == 1 and events[0][1]["stage"] == "assembly_result" and events[0][1]["status"] == "passed"
    assert events[0][1]["semantic_quality_verified"] is False
    final = (await _finals(db, project))[0]
    assert final.meta["verification"]["result_check"]["status"] == "passed"
    assert len(await _approvals(db, project)) == 1 and project.status is ProjectStatus.assembled


@pytest.mark.asyncio
async def test_proven_defect_fails_the_attempt_and_the_single_local_retry_repairs_it(db, monkeypatch):
    project = await _project(db, "w5d07b")
    broken = _probe(streams=[{"codec_type": "video", "duration": "10.0"}])   # no audio track
    wire = Wire(monkeypatch, probes=[broken, OK_PROBE])
    await asm._assemble_body(db, project, None)
    assert wire.cores == 2, "exactly one controlled repeat"
    statuses = [p["status"] for k, p in wire.journal if k == "artifact_verified"]
    assert statuses == ["failed", "passed"]
    assert (await _finals(db, project))[0].meta["verification"]["result_check"]["status"] == "passed"
    final = project.data_dir / "final" / f"{project.slug}.mp4"
    assert final.read_bytes() == b"NEWVIDEO" and [f.name for f in final.parent.iterdir()] == [final.name]


@pytest.mark.asyncio
async def test_persistent_defect_keeps_the_old_final_and_creates_no_approval(db, monkeypatch):
    project = await _project(db, "w5d07c")
    old = project.data_dir / "final" / f"{project.slug}.mp4"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"OLDVIDEO")
    broken = _probe(streams=[{"codec_type": "audio", "duration": "10.0"}])
    wire = Wire(monkeypatch, probes=[broken])
    with pytest.raises(asm.AssembleVerificationError, match="независимая проверка файла и дорожек"):
        await asm._assemble_body(db, project, None)
    assert wire.cores == asm.ASSEMBLE_MAX_ATTEMPTS and wire.sent == []
    assert old.read_bytes() == b"OLDVIDEO" and [f.name for f in old.parent.iterdir()] == [old.name]
    assert await _approvals(db, project) == [] and await _finals(db, project) == []
    assert project.status is ProjectStatus.assembling


@pytest.mark.asyncio
async def test_unknown_probe_does_not_turn_a_passed_verify_artifact_into_a_failure(db, monkeypatch):
    project = await _project(db, "w5d07d")
    wire = Wire(monkeypatch, probes=[None])
    await asm._assemble_body(db, project, None)
    assert [p["status"] for k, p in wire.journal if k == "artifact_verified"] == ["unknown"]
    assert len(await _approvals(db, project)) == 1


# ------------------------------------------------------------------ Telegram refusal -------------------------------

@pytest.mark.asyncio
async def test_telegram_refusal_after_the_request_exists_keeps_it_pending_and_marks_it(db, monkeypatch):
    project = await _project(db, "w5d07e")

    async def send(bot, session, project, *, kind, video_path, caption, payload):
        req = HITLRequest(project_id=project.id, kind=kind, payload=dict(payload, video_path=video_path))
        session.add(req)
        await session.flush()                     # create_hitl happened ...
        raise ConnectionError("telegram refused")  # ... then the notification failed

    wire = Wire(monkeypatch, send=send)
    await asm._assemble_body(db, project, None)    # does not raise: the file is verified and assembled
    approvals = await _approvals(db, project)
    assert len(approvals) == 1 and approvals[0].decision is HITLDecision.pending
    assert approvals[0].payload["notify_failed"]["error_type"] == "ConnectionError"
    assert approvals[0].payload["sha256"] == (await _finals(db, project))[0].meta["sha256"]
    assert project.status is ProjectStatus.assembled
    checks = [p for k, p in wire.journal if k == "runtime_check"]
    assert checks and checks[0]["status"] == "failed" and checks[0]["request_kept"] is True
    assert checks[0]["hitl_request_id"] == approvals[0].id


@pytest.mark.asyncio
async def test_telegram_refusal_before_the_request_exists_still_creates_it(db, monkeypatch):
    project = await _project(db, "w5d07f")

    async def send(bot, session, project, **kw):
        raise RuntimeError("telegram_active but bot is None")

    wire = Wire(monkeypatch, send=send)
    await asm._assemble_body(db, project, None)
    approvals = await _approvals(db, project)
    assert len(approvals) == 1 and approvals[0].decision is HITLDecision.pending
    assert approvals[0].payload["step"] == "final" and approvals[0].payload["notify_failed"]["error_type"] == "RuntimeError"
    assert approvals[0].decision is not HITLDecision.approved, "a refusal never approves anything"
    assert any(k == "runtime_check" for k, _ in wire.journal)


@pytest.mark.asyncio
async def test_repeat_after_a_refused_notification_does_not_duplicate_the_request(db, monkeypatch):
    project = await _project(db, "w5d07g")

    async def send(bot, session, project, **kw):
        raise RuntimeError("telegram down")

    Wire(monkeypatch, send=send)
    await asm._assemble_body(db, project, None)
    await asm._assemble_body(db, project, None)            # same output: approve_final for this digest exists
    assert len(await _approvals(db, project)) == 1


@pytest.mark.asyncio
async def test_cancellation_is_never_swallowed_as_a_telegram_refusal(db, monkeypatch):
    project = await _project(db, "w5d07h")

    async def send(bot, session, project, **kw):
        raise asyncio.CancelledError()

    Wire(monkeypatch, send=send)
    with pytest.raises(asyncio.CancelledError):
        await asm._assemble_body(db, project, None)
