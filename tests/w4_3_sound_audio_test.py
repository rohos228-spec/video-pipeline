from __future__ import annotations

import asyncio
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models import ProjectStatus
from app.orchestrator.steps import generate_audio, generate_sfx
from app.services import audio_verification as av
from app.services import sfx_gen
from app.services.ai_result_io import ai_job_checkpoint
from app.services.audio_verification import AudioStepError, classify_audio_failure, verify_audio
from app.services.sfx_plan import SfxEvent
from app.settings import settings

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")


def _wav(path: Path, seconds: float = 1.0, amp: float = 0.5) -> Path:
    n = int(44100 * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * 440 * i / 44100)))
                               for i in range(n)))
    return path


class Sess:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        return None


@pytest.fixture
def facts():
    import app.services.observation as obs
    got: list = []
    with patch.object(obs, "emit", lambda kind, payload=None, **k: got.append((kind, payload))):
        yield got




@needs_ffmpeg
def test_good_wav_passes_with_levels(tmp_path):
    v = verify_audio(_wav(tmp_path / "a.wav", 1.0), expect_duration=1.0)
    assert v.ok and v.codes == [] and v.silent is False
    assert 0.95 < v.duration < 1.05 and v.peak_db > -10 and "wav" in v.format


@needs_ffmpeg
def test_good_mp3_passes(tmp_path):
    p = tmp_path / "a.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:a", "libmp3lame", str(p)], check=True, timeout=60)
    v = verify_audio(p, expect_duration=2.0)
    assert v.ok, v.problems
    assert "mp3" in v.format


def test_missing_and_tiny_file(tmp_path):
    assert verify_audio(tmp_path / "none.wav").codes == ["missing_file"]
    tiny = tmp_path / "t.wav"
    tiny.write_bytes(b"x" * 10)
    v = verify_audio(tiny)
    assert not v.ok and v.codes == ["empty_file"]


@needs_ffmpeg
def test_garbage_bytes_are_unreadable(tmp_path):
    p = tmp_path / "g.mp3"
    p.write_bytes(b"not audio at all " * 200)
    v = verify_audio(p)
    assert not v.ok and v.codes == ["unreadable"]


@needs_ffmpeg
def test_digital_silence_is_rejected_as_zero_amplitude(tmp_path):
    v = verify_audio(_wav(tmp_path / "s.wav", 1.0, amp=0.0), expect_duration=1.0)
    assert not v.ok and "silent" in v.codes and v.silent is True


@needs_ffmpeg
def test_silent_mp3_is_rejected_through_ffmpeg_levels(tmp_path):
    p = tmp_path / "s.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", "1", "-c:a", "libmp3lame", str(p)], check=True, timeout=60)
    v = verify_audio(p, expect_duration=1.0)
    assert not v.ok and "silent" in v.codes


@needs_ffmpeg
def test_duration_out_of_tolerance_and_format_whitelist(tmp_path):
    p = _wav(tmp_path / "d.wav", 1.0)
    assert "duration_mismatch" in verify_audio(p, expect_duration=3.0, duration_tol=0.5).codes
    assert verify_audio(p, expect_duration=3.0, duration_tol=2.5).ok
    assert "bad_format" in verify_audio(p, allowed_formats=("mp3",)).codes


@needs_ffmpeg
def test_video_without_audio_stream_is_rejected(tmp_path):
    p = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=blue:s=64x64:r=10:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True, timeout=60)
    assert verify_audio(p).codes == ["no_audio_stream"]


def test_failure_classification_is_typed_and_russian():
    cases = [(RuntimeError("нет кадров"), "no_frames"),
             (RuntimeError("нет закадрового текста в БД"), "missing_text"),
             (RuntimeError("нет озвучки в x. 11Labs отключён"), "no_voice_source"),
             (RuntimeError("whisper упал"), "asr_failed"),
             (RuntimeError("elevenlabs 500"), "tts_failed"),
             (type("PaidCallBlocked", (RuntimeError,), {})("лимит"), "budget_blocked"),
             (ValueError("что-то иное"), "unknown"),
             (AudioStepError("verification_failed", "x"), "verification_failed")]
    for exc, want in cases:
        kind, text = classify_audio_failure(exc)
        assert kind == want and text and any("а" <= c.lower() <= "я" for c in text)




def _project(tmp_path):
    return SimpleNamespace(id=7, slug="p", data_dir=tmp_path / "proj", meta={}, status=ProjectStatus.generating_sfx)


def _events(n=2):
    return [SfxEvent(i + 1, float(i), 1.0, "hit", f"p{i}", 0.5, False) for i in range(n)]


@needs_ffmpeg
def test_no_key_is_an_explicit_fallback_event_per_sound(tmp_path, facts):
    project = _project(tmp_path)
    with patch.object(settings, "elevenlabs_api_key", None):
        files = asyncio.run(sfx_gen.generate_sfx_files(Sess(), project, _events(2)))
    assert [f["provider"] for f in files] == ["local_synth", "local_synth"]
    fb = [p for k, p in facts if k == "provider_fallback"]
    assert len(fb) == 2
    assert all(p["reason"] == "no_api_key" and p["synthetic"] is True and p["may_have_charged"] == "no"
               and p["error_class"] == "NoApiKey" for p in fb)
    assert all(f["synthetic"] is True and f["provider_fallback"]["reason"] == "no_api_key" for f in files)
    cp = ai_job_checkpoint(project, "sfx_files")
    assert cp["synthetic"] == 2 and cp["no_api_key"] is True and cp["provider_fallbacks"] == 2
    assert all(Path(f["path"]).is_file() for f in files)


@needs_ffmpeg
def test_with_key_and_success_there_is_no_synthetic_mark(tmp_path, facts):
    project = _project(tmp_path)

    async def fake(prompt, duration, out_path):
        _wav(out_path, duration)
        return out_path

    with patch.object(settings, "elevenlabs_api_key", "k"), patch.object(sfx_gen, "_elevenlabs_sfx", fake):
        files = asyncio.run(sfx_gen.generate_sfx_files(Sess(), project, _events(2)))
    assert [f["provider"] for f in files] == ["elevenlabs"] * 2
    assert not [1 for k, _ in facts if k == "provider_fallback"]
    assert not any("synthetic" in f for f in files)


@needs_ffmpeg
def test_provider_error_with_key_still_records_the_real_error_not_no_key(tmp_path, facts):
    project = _project(tmp_path)

    async def boom(prompt, duration, out_path):
        err = RuntimeError("11labs sfx 500")
        err.status_code = 500
        raise err

    with patch.object(settings, "elevenlabs_api_key", "k"), patch.object(sfx_gen, "_elevenlabs_sfx", boom):
        files = asyncio.run(sfx_gen.generate_sfx_files(Sess(), project, _events(1)))
    fb = [p for k, p in facts if k == "provider_fallback"]
    assert len(fb) == 1 and fb[0]["http_status"] == 500 and "reason" not in fb[0]
    assert files[0]["provider"] == "local_synth" and files[0]["synthetic"] is True


def test_sfx_acceptance_uses_the_independent_verifier(tmp_path):
    silent = _wav(tmp_path / "s.wav", 1.0, amp=0.0) if HAVE_FFMPEG else tmp_path / "x.wav"
    errs = sfx_gen.verify_audio_file(silent, expect_duration=1.0)
    assert errs if HAVE_FFMPEG else True
    assert sfx_gen.verify_audio_file(tmp_path / "missing.wav") != []




@needs_ffmpeg
def test_node_without_key_is_marked_degraded_and_journaled(tmp_path, facts):
    project = _project(tmp_path)
    with patch.object(settings, "elevenlabs_api_key", None), patch.object(settings, "sfx_enabled", True), \
            patch("app.services.sfx_plan.load_sfx_plan", lambda p: _events(2)):
        asyncio.run(generate_sfx.run(Sess(), project))
    m = project.meta
    assert project.status is ProjectStatus.sfx_ready
    assert m["sfx_degraded"] is True and m["sfx_generated"] is False
    assert m["sfx_provider"] == "local_synth" and m["sfx_synthetic_count"] == 2
    assert "ключа" in m["sfx_degraded_reason"]
    kinds = [k for k, _ in facts]
    assert kinds.count("provider_fallback") == 2 and kinds.count("sfx_node_degraded") == 1
    node = next(p for k, p in facts if k == "sfx_node_degraded")
    assert node["reason"] == "no_api_key" and "не считается готовой нормально" in node["text_ru"]


@needs_ffmpeg
def test_node_with_real_provider_files_is_normal(tmp_path, facts):
    project = _project(tmp_path)

    async def fake(prompt, duration, out_path):
        _wav(out_path, duration)
        return out_path

    with patch.object(settings, "elevenlabs_api_key", "k"), patch.object(settings, "sfx_enabled", True), \
            patch.object(sfx_gen, "_elevenlabs_sfx", fake), patch("app.services.sfx_plan.load_sfx_plan", lambda p: _events(2)):
        asyncio.run(generate_sfx.run(Sess(), project))
    assert project.meta["sfx_degraded"] is False and project.meta["sfx_generated"] is True
    assert project.meta["sfx_provider"] == "elevenlabs"
    assert "sfx_node_degraded" not in [k for k, _ in facts]


def test_disabled_sfx_is_an_explicit_skip_not_generation(tmp_path, facts):
    project = _project(tmp_path)
    with patch.object(settings, "sfx_enabled", False):
        asyncio.run(generate_sfx.run(Sess(), project))
    assert project.status is ProjectStatus.sfx_ready
    assert project.meta["sfx_skipped"] is True and "sfx_generated" not in project.meta
    assert project.meta["sfx_skipped_reason"] == "SFX_ENABLED=false"
    assert [k for k, _ in facts] == ["sfx_skipped"]


def test_degradation_summary_counts_mixed_results():
    files = [{"provider": "elevenlabs"},
             {"provider": "local_synth", "synthetic": True, "provider_fallback": {"error_class": "ReadTimeout"}}]
    s = generate_sfx.degradation_summary(files)
    assert (s["synthetic"], s["provider"], s["degraded"], s["no_api_key"]) == (1, "mixed", True, 0)




def _clip(path, end):
    from app.services.frame_audio import FrameAudioClip
    return FrameAudioClip(frame_number=1, path=path, text="т", start_ts=0.0, end_ts=end, duration=end)


@needs_ffmpeg
def test_persist_rejects_a_silent_voice_file_before_touching_the_db(tmp_path, facts):
    voice = _wav(tmp_path / "voice_full_x.wav", 1.0, amp=0.0)
    session = Sess()
    frame = SimpleNamespace(number=1, start_ts=None, end_ts=None, duration_seconds=None)
    project = SimpleNamespace(id=7, data_dir=tmp_path)
    with pytest.raises(AudioStepError) as ei:
        asyncio.run(generate_audio._persist_audio_results(
            session, project, [frame], [_clip(voice, 1.0)], voice, [], tmp_path, source="elevenlabs"))
    assert ei.value.failure_class == "verification_failed"
    assert session.added == [] and frame.start_ts is None
    ver = next(p for k, p in facts if k == "audio_result_verification")
    assert ver["ok"] is False and "silent" in ver["codes"]


@needs_ffmpeg
def test_persist_accepts_a_real_voice_file_and_records_the_verification(tmp_path, facts):
    voice = _wav(tmp_path / "voice_full_x.wav", 1.0)
    session = Sess()
    frame = SimpleNamespace(number=1, start_ts=None, end_ts=None, duration_seconds=None)
    project = SimpleNamespace(id=7, data_dir=tmp_path)

    async def no_store(*a, **k):
        return None

    with patch("app.services.asr_words_store.replace_project_asr_words", no_store), \
            patch.object(generate_audio, "dump_words_json", lambda *a, **k: None):
        asyncio.run(generate_audio._persist_audio_results(
            session, project, [frame], [_clip(voice, 1.0)], voice, [], tmp_path, source="disk_whisper"))
    art = session.added[0]
    assert art.meta["verification"]["ok"] is True and art.meta["verification"]["duration"] > 0.9
    assert frame.end_ts == 1.0
    assert next(p for k, p in facts if k == "audio_result_verification")["ok"] is True


def test_run_reports_every_failure_through_one_path_and_keeps_the_exception(tmp_path, facts):
    project = SimpleNamespace(id=7, slug="p", data_dir=tmp_path, meta={}, status=ProjectStatus.generating_audio)
    error = RuntimeError("elevenlabs 500: boom")

    async def failing(*a, **k):
        raise error

    with patch.object(generate_audio, "_run", failing):
        with pytest.raises(RuntimeError) as ei:
            asyncio.run(generate_audio.run(Sess(), project))
    assert ei.value is error and error.failure_class == "tts_failed" and error.operator_text
    fail = [p for k, p in facts if k == "audio_step_failure"]
    assert len(fail) == 1 and fail[0]["failure_class"] == "tts_failed" and fail[0]["error_class"] == "RuntimeError"


def test_run_without_frames_gives_a_typed_failure(tmp_path, facts):
    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class S(Sess):
        async def execute(self, *a, **k):
            return Result()

    async def noop(*a, **k):
        return None

    project = SimpleNamespace(id=7, slug="p", data_dir=tmp_path, meta={}, status=ProjectStatus.generating_audio)
    with patch.object(generate_audio, "recover_scene_videos_from_disk", noop), \
            patch.object(generate_audio, "recover_audio_from_disk", noop), \
            patch.object(generate_audio, "recover_whisper_from_disk", noop):
        with pytest.raises(AudioStepError) as ei:
            asyncio.run(generate_audio.run(S(), project))
    assert ei.value.failure_class == "no_frames"
    assert [p["failure_class"] for k, p in facts if k == "audio_step_failure"] == ["no_frames"]


def test_cancellation_is_not_reported_as_an_audio_failure(tmp_path, facts):
    project = SimpleNamespace(id=7, slug="p", data_dir=tmp_path, meta={}, status=ProjectStatus.generating_audio)

    async def cancelled(*a, **k):
        raise asyncio.CancelledError()

    with patch.object(generate_audio, "_run", cancelled):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(generate_audio.run(Sess(), project))
    assert not [1 for k, _ in facts if k == "audio_step_failure"]
