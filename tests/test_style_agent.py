"""Агент разбора стиля: схема, бюджет, агрегация, извлечение, прогон — LLM замокан.

Ни один тест не обращается к платным API: ``style_agent.llm.ask`` подменяется
фейком, ASR — функцией-заглушкой, ролик — синтетический (ffmpeg lavfi).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.models import NodeRunStatus, Project, ProjectStatus
from app.services.style_agent import agents, aggregate, apply, budget, extract, job, llm, schema

# ── helpers ────────────────────────────────────────────────────────────────


class _Word:
    def __init__(self, word: str, start: float, end: float) -> None:
        self.word, self.start, self.end, self.prob = word, start, end, 1.0


def _fake_words(audio: Path, language: str) -> list[_Word]:
    out = []
    t = 0.3
    for i, w in enumerate("это короткий тестовый ролик про стиль а теперь вывод".split()):
        out.append(_Word(w, round(t, 2), round(t + 0.25, 2)))
        t += 0.4 if i != 4 else 0.9
    return out


def _agent_reply(role: str) -> dict[str, Any]:
    if role == "visual":
        return {
            "medium": "2d_flat_vector",
            "palette_hex": ["#112233", "#AABBCC", "#FF8800"],
            "saturation": "medium",
            "contrast": "high",
            "line": "clean vector outline",
            "light": "soft flat lighting",
            "composition": "centered subject",
            "framing_mix": {"close": 0.2, "medium": 0.5, "full": 0.3},
            "subjects": "anthropomorphic cats",
            "world": "cozy town",
            "text_in_image": False,
            "forbidden": ["photoreal faces", "neon"],
            "mood": "warm calm",
        }
    if role == "voice":
        return {
            "language": "ru",
            "gender": "male",
            "age_band": "adult",
            "timbre": "низкий спокойный",
            "intonation": "ровная",
            "address": "ты",
            "hook_first_3s": "question",
            "cta": "subscribe",
            "delivery_markers": ["короткие фразы"],
            "music_genre": "lo-fi",
            "music_role": "bed",
            "sfx_kinds": [],
            "sfx_density": "sparse",
            "voice_name_hint": "male_low_calm",
            "tts_stability": 0.7,
            "tts_similarity_boost": 0.8,
        }
    if role == "motion":
        return {
            "camera_vocab": {"static": 0.6, "slow_push_in": 0.4},
            "subject_motion": "лёгкий параллакс",
            "transitions": {"cut": 1.0},
            "subtitles_enabled": True,
            "subtitles_mode": "word",
            "subtitles_position": "center",
            "subtitles_style": "bold white",
            "end_card": False,
            "vo_cut_sync": "per_phrase",
            "rhythm_notes": "равномерно",
        }
    if role == "narrative":
        return {
            "structure": "hook → body → payoff",
            "hook_type": "question",
            "segments": ["hook 10%", "body 70%", "payoff 20%"],
            "forbidden_phrases_seed": ["в современном мире", "давайте разберёмся"],
            "tone_notes": "дружелюбно",
        }
    return {
        "label": "Тёплые коты",
        "style_id": "warm_cats",
        "visual_core_en": "2D flat vector, clean outline, palette #112233 #AABBCC #FF8800, soft flat lighting, centered composition, warm calm mood",
        "lighting_en": "Soft flat lighting, no harsh shadows.",
        "negative_en": ["photoreal faces", "neon", "text"],
        "anim_negative_en": ["morphing", "camera shake"],
        "camera_motion_en": "Mostly static with occasional slow push-in.",
        "composition_en": "Aspect 9:16, centered subject, medium shots dominate.",
        "character_anatomy_en": None,
        "voice_tone_ru": "Спокойный низкий голос, короткие фразы, обращение на ты.",
        "narrative_ru": "Крючок-вопрос → тело → вывод с призывом подписаться.",
        "forbidden_phrases_ru": ["в современном мире", "давайте разберёмся"],
        "music_suno_core_en": "lo-fi hip hop bed, 80 bpm, instrumental",
        "frame_style_lock_en": "2D flat vector cats, clean outline, warm palette.",
        "variance_notes": ["проверить субтитры вручную"],
    }


class FakeLlm:
    """Замена ``llm.ask``: считает вызовы, отвечает по роли из system-промпта."""

    def __init__(self, *, broken_first: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.broken_first = broken_first

    async def __call__(self, text: str, *, files=None, system=None, budget, label="", project_id=None, timeout=0, log_path=None) -> str:
        n = budget.reserve(label)
        self.calls.append({"n": n, "label": label, "files": [p.name for p in (files or [])], "system": system or ""})
        if self.broken_first and len(self.calls) == 1:
            return "не json"
        role = label.split(":", 1)[0]
        if role not in schema.AGENT_FIELDS:
            role = "synth"
        return "```json\n" + json.dumps(_agent_reply(role), ensure_ascii=False) + "\n```"


def _ffmpeg_ok() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_video(path: Path, *, seconds: float = 4.0) -> Path:
    """Два цветных куска (жёсткая склейка посередине) + синус в аудио."""
    half = seconds / 2
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=red:s=320x568:d={half}:r=25",
        "-f", "lavfi", "-i", f"color=c=blue:s=320x568:d={half}:r=25",
        "-f", "lavfi", "-i", f"sine=frequency=440:d={seconds}",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _project(tmp_path: Path, monkeypatch, meta: dict | None = None) -> Project:
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(id=7, slug="style-test", topic="t", status=ProjectStatus.new, hero_mode="no_hero", meta=meta or {})
    p.data_dir.mkdir(parents=True, exist_ok=True)
    return p


# ── schema ─────────────────────────────────────────────────────────────────


def test_validate_agent_reply_normalizes_types() -> None:
    clean, problems = schema.validate_agent_reply(
        "visual",
        {"medium": "pixel_art", "palette_hex": "112233, #abc", "framing_mix": {"close": 2, "full": 2}, "text_in_image": "нет", "forbidden": "a; b", "unknown": 1},
    )
    assert not problems
    assert clean["palette_hex"] == ["#112233", "#AABBCC"]
    assert clean["framing_mix"] == {"close": 0.5, "full": 0.5}
    assert clean["text_in_image"] is False
    assert clean["forbidden"] == ["a", "b"]
    assert "unknown" not in clean


def test_validate_agent_reply_reports_bad_types() -> None:
    clean, problems = schema.validate_agent_reply("voice", {"tts_stability": "много", "language": ["ru"]})
    assert any("tts_stability" in p for p in problems)
    assert any("language" in p for p in problems)
    _, empty = schema.validate_agent_reply("motion", {})
    assert "все поля анкеты пустые" in empty
    _, not_obj = schema.validate_agent_reply("motion", "text")
    assert not_obj


def test_validate_passport_structure() -> None:
    p = schema.empty_passport("ok_id", "x")
    assert schema.validate_passport(p) == []
    p["visual"]["medium"] = "raw string"
    p["style_id"] = "Bad Id!"
    problems = schema.validate_passport(p)
    assert any("style_id" in x for x in problems)
    assert any("visual.medium" in x for x in problems)


# ── budget ─────────────────────────────────────────────────────────────────


def test_llm_budget_hard_limit() -> None:
    b = budget.LlmBudget(2)
    b.reserve("a")
    b.reserve("b")
    with pytest.raises(budget.BudgetExceeded):
        b.reserve("c")
    assert b.snapshot() == {"used": 2, "limit": 2, "remaining": 0}
    assert budget.clamp_max_calls("abc") == budget.DEFAULT_MAX_LLM_CALLS
    assert budget.clamp_max_calls(0) == budget.MIN_LLM_CALLS
    assert budget.clamp_max_calls(10_000) == budget.MAX_LLM_CALLS_HARD


def test_plan_calls_fits_and_shortfall() -> None:
    ok = budget.plan_calls(n_videos=2, sheets_per_video=4, max_calls=12)
    assert ok["fits"] is True
    assert ok["videos_covered"] == 2
    assert ok["planned_total"] <= 12
    short = budget.plan_calls(n_videos=3, sheets_per_video=4, max_calls=5)
    assert short["fits"] is False
    assert short["videos_covered"] == 1
    assert short["skip_plan"]
    none = budget.plan_calls(n_videos=0, sheets_per_video=4, max_calls=5)
    assert none["planned_total"] == 0 and "нет роликов" in none["hint"]


def test_estimate_cost_uses_catalog_or_fallback() -> None:
    plan = budget.plan_calls(n_videos=1, sheets_per_video=2, max_calls=6)
    cheap = budget.estimate_cost(plan, model={"id": "x", "pricing": {"input_usd_per_m": 0.1, "output_usd_per_m": 0.2}}, model_id="x")
    default = budget.estimate_cost(plan, model=None, model_id="gpt-5.6-sol")
    assert cheap["usd"] < default["usd"]
    assert default["usd_per_m_in"] == 1.28
    assert cheap["calls"] == plan["planned_total"]


# ── aggregate ──────────────────────────────────────────────────────────────


def test_aggregate_numeric_confidence_rules() -> None:
    stable = aggregate.aggregate_numeric([2.0, 2.1, 1.9], 3, iqr_threshold=0.35)
    assert stable["confidence"] == "high" and stable["support"] == "3/3"
    two = aggregate.aggregate_numeric([2.0, 2.1], 2, iqr_threshold=0.35)
    assert two["confidence"] == "medium"
    one = aggregate.aggregate_numeric([2.0], 1, iqr_threshold=0.35)
    assert one["confidence"] == "low"
    noisy = aggregate.aggregate_numeric([1.0, 5.0, 9.0], 3, iqr_threshold=0.35)
    assert noisy["confidence"] == "low" and "разброс" in noisy["note"]


def test_aggregate_categorical_voting() -> None:
    maj = aggregate.aggregate_categorical(["9:16", "9:16", "9:16"], 3, min_support=0.7)
    assert maj["value"] == "9:16" and maj["confidence"] == "high"
    split = aggregate.aggregate_categorical(["9:16", "16:9", "9:16"], 3, min_support=0.7)
    assert split["confidence"] == "low" and "распределение" in split["note"]


def test_kmeans_palette_and_lists() -> None:
    pal = aggregate.kmeans_palette(["#FF0000", "#FE0101", "#0000FF", "#0101FE", "#00FF00"], k=3)
    assert len(pal) == 3 and all(schema.normalize_hex(h) for h in pal)
    lst = aggregate.aggregate_list([["a", "b"], ["A", "c"], ["a"]], 3)
    assert lst["value"] == ["a"] and lst["confidence"] == "high"


def _video_entry(name: str, *, dur: float, pace: float, medium: str = "2d_flat_vector") -> dict[str, Any]:
    return {
        "video": name,
        "metrics": {
            "format": {"duration_s": dur, "fps": 25.0, "aspect": "9:16", "resolution": "720p"},
            "speech": {"pace_wpm": pace, "chars_per_sec": 14.0, "speech_ratio": 0.9, "pause_median_s": 0.3, "pause_p90_s": 0.8},
            "music": {"present": True, "loudness_lufs": -30.0, "level_vs_voice_db": -14.0},
            "motion": {"shot_len_s": 2.5, "shot_len_p10_s": 1.0, "shot_len_p90_s": 4.0, "static_ratio": 0.6, "cuts_per_min": 20.0},
            "palette": {"palette_hex": ["#112233", "#AABBCC"], "saturation": "medium", "contrast": "high"},
        },
        "agents": {
            "visual": {**_agent_reply("visual"), "medium": medium},
            "voice": _agent_reply("voice"),
            "motion": _agent_reply("motion"),
            "narrative": _agent_reply("narrative"),
        },
    }


def test_build_passport_three_videos_stable() -> None:
    videos = [_video_entry(f"v{i}.mp4", dur=58 + i, pace=150 + i) for i in range(3)]
    p = aggregate.build_passport(videos, style_id="demo")
    assert schema.validate_passport(p) == []
    assert schema.passport_confidence(p, "format.duration_s") == "high"
    assert schema.passport_value(p, "visual.medium") == "2d_flat_vector"
    assert schema.passport_confidence(p, "visual.medium") == "high"
    assert schema.passport_value(p, "motion.camera_vocab")["static"] == pytest.approx(0.6)
    assert schema.passport_value(p, "music.present") is True
    assert schema.passport_value(p, "visual.palette_hex")
    assert schema.passport_value(p, "narrative.forbidden_phrases_seed") == ["в современном мире", "давайте разберёмся"]
    report = aggregate.variance_report(p)
    assert "Устойчиво (high)" in report


def test_build_passport_single_video_all_low_and_conflict_low() -> None:
    one = aggregate.build_passport([_video_entry("a.mp4", dur=60, pace=150)])
    assert all(schema.passport_confidence(one, f"{ax}.{k}") == "low" for ax in schema.AXES for k in (one.get(ax) or {}))
    mixed = aggregate.build_passport(
        [_video_entry("a.mp4", dur=60, pace=150), _video_entry("b.mp4", dur=61, pace=151, medium="pixel_art"), _video_entry("c.mp4", dur=62, pace=152, medium="anime")]
    )
    assert schema.passport_confidence(mixed, "visual.medium") == "low"
    assert not schema.is_usable(mixed, "visual.medium")


def test_fallback_mapping_without_llm() -> None:
    p = aggregate.build_passport([_video_entry(f"v{i}.mp4", dur=60, pace=150) for i in range(3)], style_id="nollm")
    aggregate.fallback_mapping(p)
    m = p["mapping"]
    assert "2d_flat_vector" in m["visual_core_en"]
    assert m["negative_en"] == ["photoreal faces", "neon"]
    assert "Camera:" in m["camera_motion_en"]
    assert m["forbidden_phrases_ru"]


# ── extract (реальный ffmpeg на синтетике, ASR — заглушка) ────────────────


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg/ffprobe не установлены")
def test_analyze_video_synthetic(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "clip.mp4")
    profile = extract.analyze_video(video, tmp_path / "an", max_sheets=2, asr_fn=_fake_words)
    fmt = profile["format"]
    assert fmt["aspect"] == "9:16" and 3.5 <= fmt["duration_s"] <= 4.5
    assert profile["motion"]["shots"] >= 2  # склейка red→blue найдена
    assert profile["sheets"]["overview"] and Path(profile["sheets"]["overview"][0]).is_file()
    assert profile["sheets"]["motion"] and Path(profile["sheets"]["motion"][0]).is_file()
    assert profile["palette"]["palette_hex"]
    assert profile["speech"]["words"] == 9 and profile["speech"]["pace_wpm"] > 0
    assert (tmp_path / "an" / "metrics.json").is_file()
    assert (tmp_path / "an" / "words.json").is_file()
    assert (tmp_path / "an" / "voice.wav").is_file()


def test_speech_metrics_and_shot_metrics_pure() -> None:
    words = _fake_words(Path("x"), "ru")
    sp = extract.speech_metrics(words, 10.0, [])
    assert sp["words"] == 9 and sp["pause_p90_s"] >= sp["pause_median_s"]
    assert sp["first_3s_text"].startswith("это короткий")
    sm = extract.shot_metrics([{"start": 0, "end": 2, "len": 2.0}, {"start": 2, "end": 5, "len": 3.0}], 5.0)
    assert sm["shots"] == 2 and sm["shot_len_s"] == 2.5 and sm["cuts_per_min"] == 12.0
    assert extract.aspect_label(1080, 1920) == "9:16"
    assert extract.resolution_label(1920, 1080) == "1080p"


def test_detect_shots_parses_ffmpeg_output(monkeypatch) -> None:
    class _P:
        returncode = 0
        stdout = ""
        stderr = "[Parsed_showinfo] n:0 pts_time:2.04 ... \n[Parsed_showinfo] n:1 pts_time:5.5\n"

    monkeypatch.setattr(extract, "run_cmd", lambda cmd, timeout=0: _P())
    shots = extract.detect_shots(Path("v.mp4"), 8.0)
    assert [s["start"] for s in shots] == [0.0, 2.04, 5.5]


# ── agents (LLM замокан) ───────────────────────────────────────────────────


def test_specialist_retries_once_on_invalid_json(monkeypatch, tmp_path: Path) -> None:
    fake = FakeLlm(broken_first=True)
    monkeypatch.setattr(llm, "ask", fake)
    b = budget.LlmBudget(5)
    profile = _video_entry("a.mp4", dur=60, pace=150)["metrics"]
    profile["speech"]["words"] = 9
    res = asyncio.run(agents.run_specialist("voice", profile, sheets=None, budget=b, video_label="a.mp4", log_path=tmp_path / "llm.jsonl"))
    assert res["reply"]["language"] == "ru"
    assert res["calls"] == 2 and fake.calls[1]["label"].endswith("/retry")
    assert "Специалист «Озвучка" in fake.calls[0]["system"] and "оркестратор" in fake.calls[0]["system"]


def test_specialist_vision_batches_respect_budget(monkeypatch, tmp_path: Path) -> None:
    fake = FakeLlm()
    monkeypatch.setattr(llm, "ask", fake)
    sheets = []
    for i in range(10):
        fp = tmp_path / f"s{i}.jpg"
        fp.write_bytes(b"x")
        sheets.append(fp)
    b = budget.LlmBudget(1)
    profile = _video_entry("a.mp4", dur=60, pace=150)["metrics"]
    res = asyncio.run(agents.run_specialist("visual", profile, sheets=sheets, budget=b, video_label="a.mp4", max_batches=2))
    assert b.used == 1  # вторая пачка не влезла в лимит, исключения нет
    assert res["reply"]["medium"] == "2d_flat_vector"
    assert len(fake.calls[0]["files"]) == budget.IMAGES_PER_CALL
    assert any("лимит" in p for p in res["problems"])


# ── полный прогон (extract замокан, LLM замокан) ──────────────────────────


def _fake_analyze(video: Path, out_dir: Path, **kw: Any) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets = out_dir / "sheets"
    sheets.mkdir(exist_ok=True)
    ov = sheets / "overview_01.jpg"
    mo = sheets / "motion_01.jpg"
    ov.write_bytes(b"jpg")
    mo.write_bytes(b"jpg")
    m = _video_entry(video.name, dur=60, pace=150)["metrics"]
    m["speech"]["words"] = 9
    m["speech"]["transcript"] = "это тест"
    m["sheets"] = {"overview": [str(ov)], "motion": [str(mo)], "keyframes": 6}
    return m


def test_run_style_extract_end_to_end(monkeypatch, tmp_path: Path) -> None:
    fake = FakeLlm()
    monkeypatch.setattr(llm, "ask", fake)
    monkeypatch.setattr(extract, "analyze_video", _fake_analyze)
    videos = []
    for i in range(3):
        fp = tmp_path / f"ref{i}.mp4"
        fp.write_bytes(b"mp4")
        videos.append(fp)
    b = budget.LlmBudget(13)
    stages: list[str] = []
    res = asyncio.run(
        job.run_style_extract(
            videos, tmp_path / "style", settings={**job.DEFAULT_SETTINGS, "style_id": "e2e"}, budget=b, model_id="fake", progress=lambda i: stages.append(i["stage"])
        )
    )
    passport = res["passport"]
    assert b.used == 13  # 4 специалиста × 3 ролика + synth
    assert passport["llm"] == {"calls_used": 13, "calls_limit": 13, "model": "fake"}
    assert passport["style_id"] == "e2e" and passport["label"] == "Тёплые коты"
    assert passport["mapping"]["visual_core_en"].startswith("2D flat vector")
    assert schema.validate_passport(passport) == []
    assert schema.passport_confidence(passport, "visual.medium") == "high"
    assert {"extract", "agent", "aggregate", "synth"} <= set(stages)
    assert (tmp_path / "style" / "style_passport.draft.json").is_file()
    assert (tmp_path / "style" / "variance_report.md").is_file()
    assert (tmp_path / "style" / "analysis" / "ref0" / "q_visual.json").is_file()


def test_run_style_extract_budget_too_small_degrades(monkeypatch, tmp_path: Path) -> None:
    fake = FakeLlm()
    monkeypatch.setattr(llm, "ask", fake)
    monkeypatch.setattr(extract, "analyze_video", _fake_analyze)
    videos = []
    for i in range(3):
        fp = tmp_path / f"ref{i}.mp4"
        fp.write_bytes(b"mp4")
        videos.append(fp)
    b = budget.LlmBudget(5)
    res = asyncio.run(job.run_style_extract(videos, tmp_path / "style", settings=dict(job.DEFAULT_SETTINGS), budget=b))
    assert b.used <= 5
    assert len(fake.calls) <= 5
    p = res["passport"]
    # Детерминированные метрики есть по всем трём роликам, даже без LLM
    assert p["sources"]["count"] == 3
    assert schema.passport_confidence(p, "format.duration_s") == "high"
    assert p["mapping"]["visual_core_en"]  # fallback / synth


def test_run_style_extract_skip_llm_makes_no_calls(monkeypatch, tmp_path: Path) -> None:
    fake = FakeLlm()
    monkeypatch.setattr(llm, "ask", fake)
    monkeypatch.setattr(extract, "analyze_video", _fake_analyze)
    fp = tmp_path / "only.mp4"
    fp.write_bytes(b"mp4")
    b = budget.LlmBudget(12)
    res = asyncio.run(job.run_style_extract([fp], tmp_path / "style", settings={**job.DEFAULT_SETTINGS, "skip_llm": True}, budget=b))
    assert fake.calls == [] and b.used == 0
    assert res["passport"]["style_id"] == "only"
    assert res["passport"]["mapping"]  # fallback из метрик


# ── materialize ────────────────────────────────────────────────────────────


def _approved_passport() -> dict[str, Any]:
    p = aggregate.build_passport([_video_entry(f"v{i}.mp4", dur=60, pace=150) for i in range(3)], style_id="warm_cats", label="Тёплые коты")
    aggregate.apply_synth(p, _agent_reply("synth"))
    aggregate.fallback_mapping(p)
    return p


def test_materialize_writes_blocks_preset_and_frame_style(tmp_path: Path) -> None:
    roots = apply.MaterializeRoots(prompts=tmp_path / "prompts", frame_styles=tmp_path / "fstyles")
    report = apply.materialize(_approved_passport(), roots=roots)
    assert report["style_id"] == "warm_cats"
    for cat in ("visual_style", "lighting", "negative", "anim_negative", "camera_motion", "composition", "voice_tone", "script_voice_tone", "narrative_structure", "forbidden_phrases", "world"):
        assert (tmp_path / "prompts" / "blocks" / cat / "warm_cats.md").is_file(), cat
    preset = json.loads((tmp_path / "prompts" / "styles" / "warm_cats.json").read_text(encoding="utf-8"))
    assert preset["blocks"]["visual_style"] == "warm_cats"
    assert preset["vars"]["VIDEO_DURATION_SEC"] == 60 and preset["vars"]["ASPECT_RATIO_VIDEO"] == "9:16"
    fs = (tmp_path / "fstyles" / "style_warm_cats.md").read_text(encoding="utf-8")
    assert fs.startswith("STYLE:") and "Final style lock:" in fs and "Negative:" in fs
    from app.services import frame_style

    assert len(fs) <= frame_style.max_style_chars()
    nsp = report["node_step_params_patch"]
    assert nsp["plan"]["duration_seconds"] == 60
    assert nsp["split"]["cell_min_chars"] < nsp["split"]["cell_max_chars"]
    assert nsp["assemble"]["subtitles_enabled"] is True and 5 <= nsp["assemble"]["bgm_level"] <= 90
    assert nsp["audio"]["voice_hint"] == "male_low_calm"
    assert report["prompt_overrides_patch"] == {"style_profile": "warm_cats", "use_blocks_v2": True}
    # повторная запись — бэкап
    report2 = apply.materialize(_approved_passport(), roots=apply.MaterializeRoots(prompts=tmp_path / "prompts", frame_styles=tmp_path / "fstyles"))
    assert report2["backed_up"]


def test_materialize_skips_low_confidence_fields(tmp_path: Path) -> None:
    p = aggregate.build_passport([_video_entry("a.mp4", dur=60, pace=150)], style_id="single")
    aggregate.fallback_mapping(p)
    report = apply.materialize(p, roots=apply.MaterializeRoots(prompts=tmp_path / "p", frame_styles=tmp_path / "f"))
    assert report["node_step_params_patch"] == {}  # всё low → в проект не пишем
    preset = json.loads(Path(report["preset"]).read_text(encoding="utf-8"))
    assert "VIDEO_DURATION_SEC" not in preset["vars"]


def test_apply_to_project_merges_overrides_and_params(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path, monkeypatch, meta={"node_step_params": {"plan": {"duration_seconds": 30, "x": 1}}})
    project.prompt_overrides = {"script": "v1"}
    report = apply.materialize(_approved_passport(), roots=apply.MaterializeRoots(prompts=tmp_path / "p", frame_styles=tmp_path / "f"))
    apply.apply_to_project(project, report)
    assert project.prompt_overrides == {"script": "v1", "style_profile": "warm_cats", "use_blocks_v2": True}
    assert project.meta["node_step_params"]["plan"] == {"duration_seconds": 60, "x": 1}


# ── job: meta / approve / настройки ───────────────────────────────────────


def test_settings_normalize_and_meta_roundtrip(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path, monkeypatch)
    patch = job.normalize_settings({"max_llm_calls": 999, "min_support": 2, "agents": ["visual", "bogus"], "style_id": "My Style!", "junk": 1})
    assert patch == {"max_llm_calls": budget.MAX_LLM_CALLS_HARD, "min_support": 1.0, "agents": ["visual"], "style_id": "my_style"}
    job.set_meta(project, {"settings": patch})
    st = job.get_settings(project)
    assert st["max_llm_calls"] == budget.MAX_LLM_CALLS_HARD and st["agents"] == ["visual"]
    snap = job.snapshot(project)
    assert snap["job"]["status"] == "idle" and snap["videos"] == [] and snap["passport"]["hasDraft"] is False


def test_refs_upload_and_storage_sources(tmp_path: Path, monkeypatch) -> None:
    from app.services.storage_node import save_upload

    project = _project(tmp_path, monkeypatch, meta={
        "canvas_graph": {
            "nodes": [
                {"id": "n_storage_1", "type": "storage", "position": {"x": 0, "y": 0}, "data": {}},
                {"id": "n_style_1", "type": "style_extract", "position": {"x": 1, "y": 0}, "data": {}},
            ],
            "edges": [{"id": "e1", "source": "n_storage_1", "target": "n_style_1"}],
        }
    })
    with pytest.raises(ValueError):
        job.save_ref_upload(project, "notes.txt", b"x")
    job.save_ref_upload(project, "ref one.mp4", b"mp4")
    save_upload(project, "n_storage_1", "from_storage.mp4", b"mp4")
    save_upload(project, "n_storage_1", "image.png", b"png")
    names = [p.name for p in job.list_ref_videos(project, "n_style_1")]
    assert names[0] == "ref_one.mp4"
    assert any(n.endswith("from_storage.mp4") for n in names)
    assert not any(n.endswith(".png") for n in names)
    assert job.remove_ref(project, "ref_one.mp4") is True
    assert [p.name for p in job.list_ref_videos(project)] == []


def test_estimate_without_llm(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path, monkeypatch)
    job.save_ref_upload(project, "a.mp4", b"x")
    job.save_ref_upload(project, "b.mp4", b"x")
    est = job.estimate(project, None, {"max_llm_calls": 4})
    assert est["plan"]["n_videos"] == 2 and est["plan"]["fits"] is False
    assert est["cost"]["usd"] >= 0 and est["cost"]["calls"] == est["plan"]["planned_total"]


def test_approve_materializes_and_marks_meta(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        job.approve(project)
    job.write_passport(project, _approved_passport())
    roots = apply.MaterializeRoots(prompts=tmp_path / "p", frame_styles=tmp_path / "f")
    report = job.approve(project, roots=roots)
    assert report["materialized"] is True
    assert job.approved_path(project).is_file()
    assert (job.style_dir(project) / "apply_report.json").is_file()
    sa = project.meta["style_agent"]
    assert sa["passport"]["approvedAt"] and sa["apply"]["style_id"] == "warm_cats"
    assert project.prompt_overrides["style_profile"] == "warm_cats"
    snap = job.snapshot(project)
    assert snap["passport"]["hasApproved"] is True


# ── планировщик: статусы нод и прозрачность ───────────────────────────────


def test_planner_style_nodes_states_and_passthrough() -> None:
    from app.orchestrator.graph.planner import WorkflowGraph, is_passthrough_node_type

    assert is_passthrough_node_type("style_extract") and is_passthrough_node_type("hitl_style")
    nodes = [
        {"id": "n_topic", "type": "topic", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "n_style", "type": "style_extract", "position": {"x": 1, "y": 0}, "data": {}},
        {"id": "n_hs", "type": "hitl_style", "position": {"x": 2, "y": 0}, "data": {}},
        {"id": "n_plan", "type": "plan", "position": {"x": 3, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e0", "source": "n_topic", "target": "n_style"},
        {"id": "e1", "source": "n_style", "target": "n_hs"},
        {"id": "e2", "source": "n_hs", "target": "n_plan"},
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(id=1, slug="t", topic="t", status=ProjectStatus.new, meta={})
    st = g.derived_node_states(p)
    assert st["n_style"] is NodeRunStatus.pending and st["n_hs"] is NodeRunStatus.pending
    assert st["n_plan"] is NodeRunStatus.pending  # не skipped: цепочка через style-ноды прозрачна
    assert g._effective_predecessors("n_plan", set()) == set()

    p.meta = {"style_agent": {"job": {"status": "running"}}}
    assert g.derived_node_states(p)["n_style"] is NodeRunStatus.running
    p.meta = {"style_agent": {"job": {"status": "done"}, "passport": {"draftAt": "x"}}}
    st = g.derived_node_states(p)
    assert st["n_style"] is NodeRunStatus.done and st["n_hs"] is NodeRunStatus.waiting_hitl
    p.meta = {"style_agent": {"job": {"status": "failed"}, "passport": {"draftAt": "x", "approvedAt": "y"}}}
    st = g.derived_node_states(p)
    assert st["n_style"] is NodeRunStatus.failed and st["n_hs"] is NodeRunStatus.done
