import asyncio
import importlib.util
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_PATH = ROOT / "scripts" / "e2e_assemble_local.py"
_spec = importlib.util.spec_from_file_location("e2e_assemble_local_real", _PATH)
script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = script
_spec.loader.exec_module(script)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stand_workdir import make_workdir, remove_workdir

PORT = 18817
DEFAULT_WAV = Path(os.environ.get("F07_VOICE_WAV") or r"C:\video-pipeline\_asr1\voice.wav")
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")


@pytest.fixture
def tmp_path():
    path = make_workdir()
    try:
        yield path
    finally:
        remove_workdir(path)


def _words(texts=None, step=0.7):
    texts = texts or ["Привет,", "это", "проверка", "сборки", "видео.", "Сегодня", "мы", "покажем,", "как", "работает", "субтитр."]
    return [{"word": t, "start": round(i * step, 3), "end": round(i * step + 0.6, 3), "prob": 0.9} for i, t in enumerate(texts)]



def test_normalisation_ignores_case_punctuation_and_yo():
    assert script.normalize_word("Привет,") == "привет" and script.normalize_word("ЁЖ!") == "еж"
    assert script.normalize_word("...") == "" and script.normalize_word(None) == ""


def test_alignment_keeps_order_and_tolerates_an_ending_but_not_a_short_miss():
    pairs = script.align_words(["привет", "это", "проверка", "субтитр"], ["Привет", "это", "ПРОВЕРКА!", "субтитры"])
    assert pairs == [(0, 0), (1, 1), (2, 2), (3, 3)]
    assert script.align_words(["мы", "как"], ["мы", "ли", "как"]) == [(0, 0), (1, 2)]
    assert script.align_words(["кот"], ["код"]) == []
    assert script.align_words(["а", "б"], ["б", "а"]) in ([(0, 1)], [(1, 0)])


def test_asr_check_passes_with_eight_of_eleven_and_sane_stamps():
    got = script.check_asr_words(_words(), voice_s=8.7)
    assert got["ok"] and got["matched"] == 11 and got["monotonic"] and got["inside_voice"]
    poor = script.check_asr_words(_words()[:7], voice_s=8.7)
    assert poor["matched"] == 7 and not poor["ok"]
    assert script.check_asr_words(_words()[:8], voice_s=8.7)["ok"]


def test_asr_check_fails_on_non_monotonic_or_outside_the_voice_or_nothing():
    back = _words()
    back[4]["start"], back[4]["end"] = 0.1, 0.2
    assert not script.check_asr_words(back, voice_s=8.7)["monotonic"]
    assert not script.check_asr_words(_words(), voice_s=3.0)["inside_voice"]
    assert not script.check_asr_words([], voice_s=8.7)["ok"]



def _ass_for(cues, tmp_path):
    from app.services.assembly import make_simple_ass
    path = tmp_path / "subs.ass"
    make_simple_ass(cues, path, width=540, height=960)
    return script.parse_ass_cues(path.read_text(encoding="utf-8"))


GOOD_CUES = [(0.0, 0.7, "Привет"), (0.7, 1.9, "Это проверка"), (1.9, 3.4, "сборки видео"), (3.9, 5.0, "Сегодня мы"),
             (5.0, 6.4, "покажем как"), (6.4, 8.0, "работает субтитр")]


def test_ass_cues_are_read_back_without_override_tags(tmp_path):
    cues = _ass_for([(0.0, 0.7, "Привет"), (1.25, 2.5, "{x} Это\nпроверка")], tmp_path)
    assert cues[0] == (0.0, 0.7, "Привет") and cues[1][0] == 1.25 and cues[1][2] == "[x] Это проверка"


def test_subtitles_built_from_the_words_pass_and_wrong_text_or_timing_fails(tmp_path):
    cues = _ass_for(GOOD_CUES, tmp_path)
    words = _words(step=0.7)
    words = [dict(w, start=s, end=s + 0.5) for w, s in zip(words, [0.0, 0.7, 0.9, 1.9, 2.3, 3.9, 4.3, 5.0, 5.8, 6.4, 7.0])]
    good = script.check_subtitles_from_words(cues, words)
    assert good["ok"] and good["script_words_shown"] and good["aligned_to_asr"] == 11 and good["cue_first_words_aligned"] == 6
    wrong_text = script.check_subtitles_from_words(_ass_for([(0.0, 1.0, "совсем другой текст")], tmp_path), words)
    assert not wrong_text["ok"] and not wrong_text["script_words_shown"]
    late = [dict(w, start=w["start"] + 3.0, end=w["end"] + 3.0) for w in words]
    assert not script.check_subtitles_from_words(cues, late)["ok"]
    assert not script.check_subtitles_from_words([], words)["ok"]


def test_probe_times_are_inside_a_spoken_word_with_a_cue_and_a_silence_without_one():
    words = [{"word": "a", "start": 0.0, "end": 3.2}, {"word": "b", "start": 4.6, "end": 8.0}]
    cues = [(0.0, 3.2, "a"), (4.4, 8.0, "b")]
    got = script.pick_probe_times(cues, words, voice_s=8.7)
    assert got["word"] == {"t": 6.3, "word": "b"}
    assert got["silent"] == 3.9 and not any(s <= got["silent"] <= e for s, e, _ in cues)
    assert script.pick_probe_times([], words, 8.7)["word"] is None
    assert script.pick_probe_times(cues, [{"word": "a", "start": 0.0, "end": 8.6}], 8.7)["silent"] is None



def test_a_lower_third_band_difference_is_seen_and_noise_elsewhere_is_not():
    import numpy as np
    a = np.full((960, 540), 20, dtype=np.uint8)
    b = a.copy()
    b[640:700, 100:440] = 255
    low = script.band_difference(a, b, band=script.LOWER_BAND)
    up = script.band_difference(a, b, band=script.UPPER_BAND)
    assert low["fraction"] > 0.1 and up["fraction"] == 0.0
    word = {"lower": low, "upper": up}
    assert script.check_subtitle_pixels(word, {"lower": up, "upper": up})["ok"]
    assert not script.check_subtitle_pixels({"lower": up, "upper": up}, None)["ok"]
    assert not script.check_subtitle_pixels(word, {"lower": low, "upper": up})["ok"]
    assert not script.check_subtitle_pixels({"lower": low, "upper": low}, None)["ok"]
    assert not script.check_subtitle_pixels(None, None)["ok"]


def test_small_codec_noise_below_the_pixel_threshold_does_not_count():
    import numpy as np
    a = np.full((960, 540), 100, dtype=np.uint8)
    b = a + np.uint8(script.PIXEL_THRESHOLD)
    assert script.band_difference(a, b, band=script.LOWER_BAND)["fraction"] == 0.0


@needs_ffmpeg
def test_a_frame_is_taken_with_ffmpeg_at_the_requested_time(tmp_path):
    clip = tmp_path / "c.mp4"
    script.make_clip(clip, "navy", seconds=2, size="540x960")
    frame = script.gray_frame(clip, 1.0)
    assert frame.shape == (960, 540) and 0 < int(frame.mean()) < 80
    pair = script.frame_pair_difference(clip, clip, 1.0)
    assert pair["lower"]["fraction"] == 0.0 and pair["upper"]["fraction"] == 0.0



def test_loopback_hosts_are_recognised():
    for host in ("127.0.0.1", "::1", "localhost", "[::1]", None, "127.5.5.5"):
        assert script.is_loopback_host(host), host
    for host in ("8.8.8.8", "huggingface.co", "203.0.113.7", "2001:db8::1"):
        assert not script.is_loopback_host(host), host


def test_the_network_guard_records_and_refuses_a_foreign_connect_and_lookup_then_restores():
    seen = []

    async def go():
        restore = script.install_network_guard(seen)
        try:
            s = socket.socket()
            s.settimeout(0.2)
            with pytest.raises(OSError):
                s.connect(("203.0.113.9", 80))
            assert s.connect_ex(("203.0.113.9", 80)) == 10061
            s.close()
            with pytest.raises(socket.gaierror):
                socket.getaddrinfo("huggingface.co", 443)
            with pytest.raises(OSError):
                await asyncio.get_running_loop().sock_connect(socket.socket(), ("203.0.113.9", 80))
            assert socket.getaddrinfo("127.0.0.1", 80)
        finally:
            restore()

    asyncio.run(go())
    assert {a["kind"] for a in seen} >= {"connect", "connect_ex", "getaddrinfo", "sock_connect"}
    assert all(a["host"] in ("203.0.113.9", "huggingface.co") for a in seen)
    assert socket.getaddrinfo("localhost", 80)



def _write_wav(path: Path, *, width=2, seconds=4.0, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes((struct.pack("<h", 0) if width == 2 else b"\x80") * int(rate * seconds))


def test_the_voice_wav_must_be_16_bit_pcm_and_long_enough(tmp_path):
    ok = tmp_path / "ok.wav"
    _write_wav(ok)
    assert script.validate_voice_wav(ok)["sample_width"] == 2 and script.validate_voice_wav(ok)["duration"] == 4.0
    eight = tmp_path / "eight.wav"
    _write_wav(eight, width=1)
    with pytest.raises(ValueError, match="16-bit"):
        script.validate_voice_wav(eight)
    short = tmp_path / "short.wav"
    _write_wav(short, seconds=1.0)
    with pytest.raises(ValueError, match="short"):
        script.validate_voice_wav(short)
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not a wav")
    with pytest.raises(ValueError):
        script.validate_voice_wav(junk)


def test_the_plan_sheet_carries_the_frame_texts_where_the_full_voice_path_reads_them(tmp_path):
    from app.storage.plan_sheet_v8 import _read_plan_voiceover_cells_raw
    xlsx = tmp_path / "videos" / "project.xlsx"
    script.make_plan_xlsx(xlsx)

    class P:
        id = 1
        data_dir = xlsx.parent

    cells = _read_plan_voiceover_cells_raw(P, [1, 2, 3])
    assert [t for _n, t in cells] == list(script.REAL_FRAME_TEXTS)
    assert script.read_r15_labels(xlsx, 3) == ["", "", ""]
    assert len(script.REAL_EXPECTED_WORDS) == 11 and len(script.REAL_VOICE_TEXT.split()) == 11


def test_the_cached_model_is_found_linked_into_the_scratch_data_and_only_the_link_is_removed(tmp_path):
    hub = tmp_path / "hub"
    snap = hub / "models--Systran--faster-whisper-tiny" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x" * 10)
    env = {"HF_HUB_CACHE": str(hub)}
    assert script.find_cached_model("tiny", env) == hub / "models--Systran--faster-whisper-tiny"
    assert script.find_cached_model("nope", env) is None
    with pytest.raises(FileNotFoundError):
        script.link_cached_model(tmp_path / "data", "nope", env)
    link = script.link_cached_model(tmp_path / "data", "tiny", env)
    assert (link / "snapshots" / "abc" / "model.bin").read_bytes() == b"x" * 10
    assert link.parent == tmp_path / "data" / ".cache" / "huggingface" / "hub"
    script.unlink_cached_model(link)
    assert not link.exists() and (snap / "model.bin").is_file()
    real_dir = tmp_path / "data" / ".cache" / "huggingface" / "hub" / "plain"
    real_dir.mkdir(parents=True)
    with pytest.raises(OSError):
        script.unlink_cached_model(real_dir)



def test_real_speech_needs_a_wav_and_the_wav_needs_the_flag(tmp_path):
    wav = tmp_path / "v.wav"
    _write_wav(wav)
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--real-speech"])
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--real-speech", "--voice-wav", str(tmp_path / "missing.wav")])
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--voice-wav", str(wav)])
    got = script._args(["--work-dir", "w", "--dry", "--real-speech", "--voice-wav", str(wav), "--asr-model", "small"])
    assert got.real_speech and got.asr_model == "small" and got.timeout_s == 1500
    plain = script._args(["--work-dir", "w", "--dry"])
    assert not plain.real_speech and plain.asr_model == "medium" and plain.timeout_s == 420


def test_the_reports_say_what_is_real_and_what_is_not_proven_in_real_speech_mode():
    text = json.dumps(script.reality_report(True), ensure_ascii=False)
    assert "SAPI" in text and "NOT a human voice" in text and "lavfi" in text and "faster-whisper" in text
    assert "Telegram: off" in text and "forced failure" in text
    assert "human voices" in json.dumps(script.reality_not_proven(True))
    assert "ASR / Whisper and burned subtitles: not used" in json.dumps(script.reality_report(False))
    assert script.final_verdict(True, True).startswith("DRY OK") and "NOT proven" in script.final_verdict(True, True)
    assert script.final_verdict(False, True) == "NOT PASSED"
    assert "NOT exercised" in script.final_verdict(True)
    assert script.asr_probe(True)["used_by_this_stand"] is True and script.asr_probe()["used_by_this_stand"] is False



def _good_real_snapshot(**over):
    from w5h_assemble_local_script_test import _good_snapshot
    snap = _good_snapshot(final_check={"status": "passed", "streams": ["video", "audio"], "duration": 8.7, "sha256": "abc"})
    words = _words()
    rs = {
        "voice": {"duration": 8.7}, "asr_model": "medium", "audio_mode": "full_voice", "subtitles_enabled": True,
        "asr": {"calls": 1, "stubbed": False, "model": "medium", "device": "cpu", "compute_type": "int8", "words": words,
                "word_count": 11, "seconds_each": [8.0], "check": script.check_asr_words(words, 8.7)},
        "subtitles": {"ok": True, "cue_count": 6, "cue_words": [], "script_words_shown": True, "aligned_to_asr": 11,
                      "within_tolerance": 6, "max_abs_offset_s": 0.18},
        "burn": {"vf_calls": 1, "burn_commands": 1, "ass_files": 1, "filter": "subtitles=subs.ass", "burn_uses_vf_filter": True},
        "pixels": {"ok": True, "lower_fraction_at_word": 0.045, "upper_fraction_at_word": 0.0},
        "r15_labels": ["0:00.00-0:00.78", "0:01.86-0:04.00", "0:04.66-0:08.71"],
        "network_attempts": [], "network_guard": True}
    snap.update(real_speech_mode=True, real_speech=rs)
    snap.update(over)
    return snap


def _failed(snap):
    return set(script.verdict(script.evaluate(snap))[1])


def test_a_good_real_speech_snapshot_passes_every_check_old_and_new():
    checks = script.evaluate(_good_real_snapshot())
    assert script.verdict(checks) == (True, [])
    assert len({c["name"] for c in checks}) == len(checks) >= 28


def test_the_per_frame_duration_window_does_not_apply_to_the_real_voice():
    snap = _good_real_snapshot()
    assert "final_file_passes_the_real_verifier_after_the_run" not in _failed(snap)
    snap["final_check"] = {"status": "passed", "streams": ["video", "audio"], "duration": 5.0, "sha256": "abc"}
    assert {"final_file_passes_the_real_verifier_after_the_run", "final_duration_matches_the_voice"} <= _failed(snap)


def test_every_real_speech_proof_can_fail_on_its_own():
    def broken(path, value):
        snap = _good_real_snapshot()
        cur = snap["real_speech"]
        for key in path[:-1]:
            cur = cur[key]
        cur[path[-1]] = value
        return _failed(snap)

    assert "real_asr_ran_offline_without_a_stub" in broken(["asr", "stubbed"], True)
    assert "real_asr_ran_offline_without_a_stub" in broken(["asr", "calls"], 0)
    assert "real_asr_ran_offline_without_a_stub" in broken(["asr", "model"], "tiny")
    assert "asr_returned_enough_of_the_expected_words" in broken(["asr", "check"], script.check_asr_words(_words()[:5], 8.7))
    assert "asr_timestamps_monotonic_and_inside_the_voice" in broken(["asr", "check"], script.check_asr_words(_words(), 2.0))
    assert "timeline_r15_came_from_the_asr_words" in broken(["r15_labels"], ["", "", ""])
    assert "voice_is_the_full_voice_artifact_not_per_frame" in broken(["audio_mode"], "per_frame")
    assert "subtitle_file_was_built_from_the_asr_words" in broken(["subtitles", "ok"], False)
    assert "production_filter_burned_the_generated_ass" in broken(["burn", "burn_commands"], 0)
    assert "production_filter_burned_the_generated_ass" in broken(["burn", "filter"], "subtitles=other.ass")
    assert "subtitles_are_really_on_the_picture" in broken(["pixels", "ok"], False)
    assert "no_socket_left_the_machine" in broken(["network_attempts"], [{"kind": "connect", "host": "8.8.8.8"}])
    assert "no_socket_left_the_machine" in broken(["network_guard"], False)


def test_a_run_that_collected_no_real_speech_evidence_fails_every_new_check():
    snap = _good_real_snapshot()
    snap["real_speech"] = {}
    new = {"real_asr_ran_offline_without_a_stub", "asr_returned_enough_of_the_expected_words",
           "asr_timestamps_monotonic_and_inside_the_voice", "timeline_r15_came_from_the_asr_words",
           "voice_is_the_full_voice_artifact_not_per_frame", "subtitle_file_was_built_from_the_asr_words",
           "production_filter_burned_the_generated_ass", "final_duration_matches_the_voice",
           "subtitles_are_really_on_the_picture", "no_socket_left_the_machine"}
    assert new <= _failed(snap)


def test_the_default_snapshot_gets_none_of_the_new_checks():
    from w5h_assemble_local_script_test import _good_snapshot
    names = {c["name"] for c in script.evaluate(_good_snapshot())}
    assert "real_asr_ran_offline_without_a_stub" not in names and len(names) == 18



def _real_run_skip_reason():
    if not HAVE_FFMPEG:
        return "ffmpeg/ffprobe are required"
    if importlib.util.find_spec("faster_whisper") is None:
        return "faster_whisper is not installed in this environment"
    if script.find_cached_model(script.DEFAULT_ASR_MODEL) is None:
        return "the faster-whisper 'medium' model is not in the local Hugging Face cache (the run is offline)"
    if not DEFAULT_WAV.is_file():
        return f"the SAPI voice wav is missing: {DEFAULT_WAV} (set F07_VOICE_WAV)"
    return None


@pytest.mark.slow
@pytest.mark.skipif(_real_run_skip_reason() is not None, reason=_real_run_skip_reason() or "")
def test_a_real_dry_run_with_real_speech_asr_and_burned_subtitles(tmp_path):
    env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}
    for name in ("GPT_RELAY_TOKEN", "GPT_PROXY_URL", "VIBECODE_API_KEY", "GPT_API_KEY", "OUTSEE_API_KEY", "KIE_API_KEY",
                 "ELEVENLABS_API_KEY"):
        env.pop(name, None)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(tmp_path), "--dry", "--port", str(PORT),
         "--settle-s", "3", "--sleep-minutes", "0.01", "--real-speech", "--voice-wav", str(DEFAULT_WAV),
         "--asr-model", "medium"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1700)
    result_path = tmp_path / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    assert proc.returncode == 0, (proc.stdout or "")[-2500:]
    assert result["ok"] is True, result.get("failed")
    rs = result["real_speech"]
    assert rs["asr"]["calls"] >= 1 and rs["asr"]["stubbed"] is False and rs["asr"]["model"] == "medium"
    assert rs["asr"]["check"]["matched"] >= 8 and rs["asr"]["check"]["monotonic"] and rs["asr"]["check"]["inside_voice"]
    assert rs["subtitles"]["script_words_shown"] and rs["subtitles"]["aligned_to_asr"] >= 8
    assert rs["burn"]["filter"] == "subtitles=subs.ass" and rs["burn"]["burn_commands"] >= 1
    assert rs["pixels"]["lower_fraction_at_word"] >= script.MIN_LOWER_FRACTION
    assert rs["pixels"]["upper_fraction_at_word"] <= script.MAX_NOISE_FRACTION
    assert abs(result["final_check"]["duration"] - rs["voice"]["duration"]) <= 0.5
    assert rs["network_attempts"] == [] and rs["network_guard"] is True
    counters = result["counters"]
    assert counters["injected_failures"] >= 4 and counters["injected_failures"] % 2 == 0
    recoveries = result["actions"]["recoveries"]
    assert len(recoveries) == 1 and recoveries[0]["status"] == "verified"
    assert recoveries[0]["verification_result"]["local_details"]["approve_final"]["approved_by_supervisor"] is False
    assert [a["decision"] for a in result["approvals"]] == ["pending"]
    assert result["ledger_rows"] == [] and result["spend_rows_paid"] == [] and result["refused_hosts"] == []
    assert any("SAPI" in x for x in result["reality"]["not_real"])
    assert not (tmp_path / "data" / ".cache" / "huggingface" / "hub" / script.model_cache_dir_name("medium")).exists()
    assert script.find_cached_model("medium") is not None
