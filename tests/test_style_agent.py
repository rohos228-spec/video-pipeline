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
    if role == "shots":
        return {
            "shots": [
                {"i": 1, "what": "кот на крыше", "framing": "medium", "camera": "static", "subject_anim": "машет хвостом", "start_state": "сидит", "change": "поворачивает голову", "end_state": "смотрит в камеру", "transition_out": "cut", "transition_note": "по взгляду", "illustrates": "кот", "text_on_screen": None, "vfx": None},
                {"i": 2, "what": "улица сверху", "framing": "wide", "camera": "push_in", "subject_anim": "машины едут", "start_state": "общий план", "change": "наезд", "end_state": "крупнее", "transition_out": "none", "transition_note": None, "illustrates": None, "text_on_screen": None, "vfx": "motion blur"},
            ],
            "false_cuts": [],
            "camera_vocab": {"static": 0.6, "push_in": 0.4},
            "subject_motion": "лёгкий параллакс",
            "transitions": {"cut": 1.0},
            "subtitles_enabled": True,
            "subtitles_mode": "line",
            "subtitles_position": "lower_third",
            "subtitles_style": "bold white, 3 words",
            "end_card": False,
            "vo_cut_sync": "per_phrase",
            "rhythm_notes": "равномерно",
            "shot_construction": "состояние → одно изменение → состояние",
        }
    if role == "narrative":
        return {
            "structure": "hook → body → payoff",
            "hook_type": "question",
            "segments": [
                {"name": "hook", "start": 0.0, "end": 3.0, "share": 0.1, "function": "вопрос", "quote": "это короткий"},
                {"name": "payoff", "start": 3.0, "end": 10.0, "share": 0.9, "function": "вывод", "quote": "а теперь вывод"},
            ],
            "beats_per_sec": 0.9,
            "sentence_templates": ["«Если {условие}, то {последствие}»"],
            "rhetoric_devices": [{"name": "повтор", "description": "повтор ключевой фразы", "frequency": 2, "examples": ["[0.3] это короткий"]}],
            "example_hook": "[0.3] это короткий тестовый ролик",
            "example_payoff": "[2.9] а теперь вывод",
            "address": "second_person",
            "forbidden_phrases_seed": ["в современном мире", "давайте разберёмся"],
            "tone_notes": "дружелюбно",
        }
    if role == "patterns":
        return {
            "patterns": [
                {"name": "jump_zoom", "kind": "camera", "description": "резкий наезд на ключевом слове", "frequency": 3, "examples": ["0:02", "0:05"], "how_to_reproduce": "scale 1.0→1.15 за 2 кадра"},
            ],
            "peculiarities": ["субтитры по 3 слова", "каждая фраза = новый план"],
            "visual_illustrates_noun_ratio": 0.7,
            "linkage_notes": "картинка следует за существительным",
            "timing_notes": "склейка через +40 мс после начала фразы",
        }
    if role == "blueprint":
        return {
            "chain": [{"type": "script", "new": False, "role": "сценарий", "inputs": ["topic"], "outputs": ["script"], "prompt_hint": "3 слова на фразу", "params": {"target_duration_s": 60}}],
            "prompt_templates": {"script_writer": "Пиши короткими фразами {topic}", "image_prompt": "2D flat vector {shot}"},
            "new_nodes_needed": ["shot_plan — нарезка на планы"],
            "notes": ["jump_zoom делать на монтаже"],
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
    assert p["$schema"] == schema.PASSPORT_SCHEMA == "style_passport/v2"
    assert schema.validate_passport(p) == []
    p["visual"]["medium"] = "raw string"
    p["style_id"] = "Bad Id!"
    p["patterns"] = "not a list"
    problems = schema.validate_passport(p)
    assert any("style_id" in x for x in problems)
    assert any("visual.medium" in x for x in problems)
    assert any("patterns" in x for x in problems)
    # паспорт v1 (без новых секций) по-прежнему принимается
    v1 = schema.empty_passport("old_id", "x")
    for k in ("videos", "linkage", "script", "patterns", "peculiarities", "blueprint"):
        v1.pop(k, None)
    v1["$schema"] = schema.PASSPORT_SCHEMA_V1
    assert schema.validate_passport(v1) == []
    v1["$schema"] = "style_passport/v9"
    assert any("schema" in x for x in schema.validate_passport(v1))


def test_validate_agent_reply_v2_objlists() -> None:
    clean, problems = schema.validate_agent_reply("shots", {**_agent_reply("shots"), "false_cuts": ["1.5", 2, "x"]})
    assert not problems
    assert clean["false_cuts"] == [1.5, 2.0]
    assert clean["shots"][0]["i"] == 1 and clean["shots"][0]["framing"] == "medium"
    clean, _ = schema.validate_agent_reply("narrative", _agent_reply("narrative"))
    assert clean["segments"][0]["name"] == "hook" and clean["beats_per_sec"] == pytest.approx(0.9)
    clean, _ = schema.validate_agent_reply("blueprint", _agent_reply("blueprint"))
    assert clean["chain"][0]["type"] == "script" and "script_writer" in clean["prompt_templates"]


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
    assert ok["global_agents"] == ["synth", "blueprint"]
    # 2 ролика × 5 специалистов + synth + blueprint = 12 — лимит по умолчанию покрывает минимум
    assert ok["minimal_total"] == 12 == budget.DEFAULT_MAX_LLM_CALLS
    short = budget.plan_calls(n_videos=3, sheets_per_video=4, max_calls=7)
    assert short["fits"] is False
    assert short["videos_covered"] == 1
    assert short["skip_plan"]
    no_bp = budget.plan_calls(n_videos=1, sheets_per_video=4, max_calls=8, with_blueprint=False)
    assert no_bp["global_agents"] == ["synth"] and no_bp["minimal_total"] == 6
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
            "motion": {"shot_len_s": 2.5, "shot_len_p10_s": 1.0, "shot_len_p90_s": 4.0, "static_ratio": 0.6, "cuts_per_min": 20.0, "shots": 2, "transitions_measured": {"cut": 1.0}},
            "palette": {"palette_hex": ["#112233", "#AABBCC"], "saturation": "medium", "contrast": "high"},
            "linkage": {
                "lines": [{"i": 1, "start": 0.3, "end": 2.0, "text": "это короткий тестовый", "shot_ids": [1]}],
                "shots": [{"i": 1, "start": 0.0, "end": 2.0, "line_ids": [1], "text": "это короткий тестовый"}],
                "stats": {"lines": 1, "shots": 2, "cuts": 1, "cut_on_phrase_ratio": 0.7, "phrase_starts_new_shot_ratio": 0.6, "cut_offset_ms_median": 40, "cut_offset_ms_abs_median": 60, "lines_per_shot_median": 2, "words_per_line_median": 3, "line_len_s_median": 1.1, "pattern": "each_phrase_new_shot"},
            },
            "script": {"beats_per_sec": 0.95, "sentences_per_min": 11, "words_per_sentence_median": 8, "words_per_sentence_max": 14, "sentence_len_s_median": 2.8, "hook_text": "это короткий тестовый ролик", "tail_silence_s": 0.4, "speech_starts_at_s": 0.3},
        },
        "shots_final": [
            {"i": 1, "start": 0.0, "end": 2.0, "len": 2.0, "transition_in": "start", "what": "кот на крыше", "framing": "medium", "camera": "static", "vo_text": "это короткий тестовый"},
            {"i": 2, "start": 2.0, "end": dur, "len": dur - 2.0, "transition_in": "hard", "what": "улица сверху", "framing": "wide", "camera": "push_in", "vo_text": "ролик про стиль"},
        ],
        "agents": {
            "visual": {**_agent_reply("visual"), "medium": medium},
            "voice": _agent_reply("voice"),
            "shots": _agent_reply("shots"),
            "narrative": _agent_reply("narrative"),
            "patterns": _agent_reply("patterns"),
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
    # v2: связка, сценарий, приёмы, разбор по роликам
    assert schema.passport_value(p, "linkage.cut_on_phrase_ratio") == pytest.approx(0.7)
    assert schema.passport_value(p, "linkage.pattern") == "each_phrase_new_shot"
    assert schema.passport_value(p, "linkage.visual_illustrates_noun_ratio") == pytest.approx(0.7)
    assert schema.passport_value(p, "script.beats_per_sec") == pytest.approx(0.95)
    assert schema.passport_value(p, "narrative.beats_per_sec") == pytest.approx(0.9)
    assert schema.passport_value(p, "narrative.segments")[0]["name"] == "hook"
    assert len(p["videos"]) == 3 and p["videos"][0]["shots"][0]["what"] == "кот на крыше"
    assert p["patterns"] and p["patterns"][0]["name"] == "jump_zoom" and p["patterns"][0]["support"] == "3/3"
    assert "субтитры по 3 слова" in p["peculiarities"]


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
    profile = extract.analyze_video(video, tmp_path / "an", max_sheets=2, asr_fn=_fake_words, skip_ocr=True)
    fmt = profile["format"]
    assert fmt["aspect"] == "9:16" and 3.5 <= fmt["duration_s"] <= 4.5
    assert profile["motion"]["shots"] == 2  # ровно одна склейка red→blue
    assert 1.8 <= profile["cuts"][0]["t"] <= 2.2 and profile["cuts"][0]["kind"] == "hard"
    assert profile["sheets"]["overview"] and Path(profile["sheets"]["overview"][0]).is_file()
    assert profile["sheets"]["shots"] and Path(profile["sheets"]["shots"][0]).is_file()
    assert profile["sheets"]["cuts"] and Path(profile["sheets"]["cuts"][0]).is_file()
    assert profile["sheets"]["motion"] == profile["sheets"]["shots"]  # совместимость v1
    tl = profile["timeline"]
    assert tl["rate_fps"] == pytest.approx(2.0) and tl["frames"] >= 10 and tl["frames_per_min"] == 120
    tl_dir = tmp_path / "an" / "timeline"
    assert len(list(tl_dir.glob("tl_*.jpg"))) >= 7  # 4 с × 2 fps
    assert list(tl_dir.glob("hk_*.jpg"))  # хук плотнее
    assert len(list(tl_dir.glob("cut_*_before.jpg"))) == 1 and len(list(tl_dir.glob("cut_*_after.jpg"))) == 1
    assert profile["palette"]["palette_hex"]
    assert profile["speech"]["words"] == 9 and profile["speech"]["pace_wpm"] > 0
    assert profile["speech"]["transcript_source"] == "asr" and profile["subtitles"] == []
    assert profile["linkage"]["stats"]["shots"] == 2 and profile["script"]["beats_per_sec"] is None  # субтитров нет
    for name in ("metrics.json", "words.json", "voice.wav", "cuts.json", "shots.json", "scene_scores.json", "linkage.json", "subtitles.json"):
        assert (tmp_path / "an" / name).is_file(), name


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg/ffprobe не установлены")
def test_analyze_video_with_injected_ocr(tmp_path: Path) -> None:
    """OCR-заглушка: субтитры становятся источником транскрипта вместо ASR."""
    video = _make_video(tmp_path / "clip.mp4")
    asr_calls: list[str] = []

    def _asr(audio: Path, language: str):
        asr_calls.append(language)
        return _fake_words(audio, language)

    def _ocr(frame: Path):
        # Красная половина (0–2 с) → фраза 1, синяя → фраза 2, ещё одна в конце
        t = _frame_time(frame)
        if t is None:
            return []
        if t < 1.9:
            return [("the first line", 0.98, 0.9, 0.5)]
        if t < 3.0:
            return [("second line here", 0.97, 0.9, 0.5)]
        return [("and the end", 0.99, 0.9, 0.5)]

    profile = extract.analyze_video(video, tmp_path / "an", max_sheets=1, asr_fn=_asr, ocr_fn=_ocr)
    assert asr_calls == []  # субтитров ≥ 3 строк → ASR не нужен
    assert profile["speech"]["transcript_source"] == "subtitles"
    texts = [s["text"] for s in profile["subtitles"]]
    assert texts == ["the first line", "second line here", "and the end"]
    assert profile["subtitles_info"]["backend"] == "injected" and profile["subtitles_info"]["lines"] == 3
    link = profile["linkage"]
    assert link["stats"]["lines"] == 3 and link["lines"][0]["shot_ids"] == [1]
    assert profile["script"]["hook_text"].startswith("the first line")
    assert (tmp_path / "an" / "ocr_raw.json").is_file()


def _frame_time(frame: Path) -> float | None:
    """Время кадра из имени файла выборки (tl_NNNNN / hk_NNNNN / cut_<ms>_*)."""
    import re

    m = re.match(r"tl_(\d+)", frame.stem)
    if m:
        return (int(m.group(1)) - 1) / extract.DEFAULT_FRAMES_PER_MIN * 60
    m = re.match(r"hk_(\d+)", frame.stem)
    if m:
        return (int(m.group(1)) - 1) / (extract.DEFAULT_FRAMES_PER_MIN * extract.HOOK_DENSITY) * 60
    m = re.match(r"cut_(\d+)_(before|after)", frame.stem)
    if m:
        return int(m.group(1)) / 1000 + (-extract.CUT_PAD_S if m.group(2) == "before" else extract.CUT_PAD_S)
    return None


def test_speech_metrics_and_shot_metrics_pure() -> None:
    words = _fake_words(Path("x"), "ru")
    sp = extract.speech_metrics(words, 10.0, [])
    assert sp["words"] == 9 and sp["pause_p90_s"] >= sp["pause_median_s"]
    assert sp["first_3s_text"].startswith("это короткий")
    sm = extract.shot_metrics([{"start": 0, "end": 2, "len": 2.0}, {"start": 2, "end": 5, "len": 3.0}], 5.0)
    assert sm["shots"] == 2 and sm["shot_len_s"] == 2.5 and sm["cuts_per_min"] == 12.0
    assert extract.aspect_label(1080, 1920) == "9:16"
    assert extract.resolution_label(1920, 1080) == "1080p"


def _scores(duration: float, fps: float, spikes: dict[float, float], noise: float = 0.004) -> list[tuple[float, float]]:
    out = []
    n = int(duration * fps)
    for i in range(n):
        t = round(i / fps, 4)
        s = noise * (1 + (i % 3) * 0.3)
        for st, sv in spikes.items():
            if abs(t - st) < 0.5 / fps:
                s = sv
        out.append((t, s))
    return out


def test_detect_cuts_adaptive_and_subtitle_cues() -> None:
    # жёсткие склейки 2.0 и 5.0, слабый кандидат 7.0 (score 0.03) — принимается только рядом со сменой субтитра
    sc = _scores(10.0, 30.0, {2.0: 0.4, 5.0: 0.09, 7.0: 0.03})
    cuts = extract.detect_cuts(sc, 10.0)
    assert [c["t"] for c in cuts] == [2.0, 5.0]
    assert all(c["kind"] == "hard" for c in cuts)
    with_cue = extract.detect_cuts(sc, 10.0, cue_times=[7.1])
    assert [c["t"] for c in with_cue] == [2.0, 5.0, 7.0]
    assert with_cue[-1]["kind"] == "cue" and with_cue[-1]["subtitle_cue"] is True
    far_cue = extract.detect_cuts(sc, 10.0, cue_times=[8.5])
    assert [c["t"] for c in far_cue] == [2.0, 5.0]
    # плавный переход: серия высоких значений ≥ 0.25 с → одна склейка kind=gradual
    grad = {round(4.0 + k / 30.0, 4): 0.08 + 0.01 * (k == 5) for k in range(12)}
    g = extract.detect_cuts(_scores(10.0, 30.0, grad), 10.0)
    assert len(g) == 1 and g[0]["kind"] == "gradual" and g[0]["span"] >= 0.25
    # пустой/короткий вход
    assert extract.detect_cuts([], 10.0) == []
    shots = extract.shots_from_cuts(with_cue, 10.0)
    assert [s["start"] for s in shots] == [0.0, 2.0, 5.0, 7.0] and shots[-1]["end"] == 10.0
    assert shots[1]["transition_in"] == "hard" and shots[3]["transition_in"] == "cue" and shots[3]["subtitle_cue"] is True


def test_parse_scene_scores() -> None:
    text = (
        "frame:0 pts:0 pts_time:0\nlavfi.scene_score=0.000000\n"
        "frame:1 pts:1 pts_time:0.0333\nlavfi.scene_score=0.012000\n"
        "frame:2 pts:2 pts_time:0.0667\nlavfi.scene_score=0.450000\n"
    )
    sc = extract.parse_scene_scores(text)
    assert sc == [(0.0, 0.0), (0.0333, 0.012), (0.0667, 0.45)]


def test_apply_false_cuts_merges_shots() -> None:
    shots = extract.shots_from_cuts([{"t": 2.0, "kind": "hard"}, {"t": 2.6, "kind": "hard"}, {"t": 6.0, "kind": "hard"}], 10.0)
    merged = agents.apply_false_cuts(shots, [2.55])
    assert [s["start"] for s in merged] == [0.0, 2.0, 6.0]
    assert merged[1]["end"] == 6.0 and merged[1]["len"] == 4.0 and merged[1]["merged_from"] == [2, 3]
    assert [s["i"] for s in merged] == [1, 2, 3] and merged[2]["orig_i"] == 4
    desc = agents.merge_shot_descriptions(merged, {"shots": [{"i": 2, "what": "крупно крючок", "framing": "close"}, {"i": 4, "what": "рыба", "camera": "pan"}]})
    assert desc[1]["what"] == "крупно крючок" and desc[2]["camera"] == "pan"
    assert agents.apply_false_cuts(shots, None) is shots


# ── OCR субтитров и связка с планами (чистые функции) ─────────────────────


def _raw_ocr() -> list[dict[str, Any]]:
    seq = [
        (0.0, "ZACK D FILMS", 0.3),  # надпись не в полосе субтитров
        (0.5, "he was alone", 0.9),
        (1.0, "he was alone", 0.9),
        (1.5, "he was a1one", 0.9),  # опечатка OCR → та же строка
        (2.0, "at sea for", 0.9),
        (2.5, "at sea for", 0.9),
        (3.0, "", 0.9),
        (3.5, "133 days", 0.9),
    ]
    return [{"t": t, "boxes": ([[txt, 0.97, cy, 0.5]] if txt else [])} for t, txt, cy in seq]


def test_ocr_build_lines_and_words() -> None:
    from app.services.style_agent import ocr

    raw = _raw_ocr()
    band = ocr.subtitle_band(raw)
    assert band is not None and band[0] <= 0.9 <= band[1] and band[0] > 0.3
    lines = ocr.build_lines(raw, band=band, frame_step_s=0.5)
    assert [ln.text for ln in lines] == ["he was alone", "at sea for", "133 days"]
    assert lines[0].start == pytest.approx(0.5) and lines[0].end >= 1.5
    assert lines[1].end <= 3.0 + 0.5
    words = ocr.lines_to_words(lines)
    assert [w.word for w in words][:3] == ["he", "was", "alone"] and words[0].start == pytest.approx(0.5)
    assert ocr.normalize_text("  he  | was  ") == "he I was"


def test_linkage_and_script_metrics() -> None:
    from app.services.style_agent import linkage

    subs = [
        {"start": 0.3, "end": 1.2, "text": "he was alone", "conf": 0.9},
        {"start": 1.25, "end": 2.1, "text": "at sea for", "conf": 0.9},
        {"start": 2.15, "end": 3.0, "text": "133 days.", "conf": 0.9},
        {"start": 3.4, "end": 4.6, "text": "Then a shark came?", "conf": 0.9},
    ]
    shots = extract.shots_from_cuts([{"t": 1.3, "kind": "hard"}, {"t": 2.2, "kind": "hard"}, {"t": 3.45, "kind": "hard"}], 6.0)
    link = linkage.link_subtitles_to_shots(subs, shots)
    st = link["stats"]
    assert st["lines"] == 4 and st["shots"] == 4 and st["cuts"] == 3
    assert st["phrase_starts_new_shot_ratio"] == pytest.approx(1.0)  # первая фраза — с началом ролика, остальные — со склейкой
    assert st["cut_on_phrase_ratio"] == pytest.approx(1.0)
    assert 0 < st["cut_offset_ms_median"] <= 60
    assert st["pattern"] == "each_phrase_new_shot"
    assert link["lines"][0]["shot_ids"] == [1] and link["shots"][1]["line_ids"] == [2]
    assert link["shots"][1]["text"] == "at sea for"
    sents = linkage.sentences_from_lines(subs)
    assert [s["text"] for s in sents] == ["he was alone at sea for 133 days.", "Then a shark came?"]
    sm = linkage.script_metrics(subs, 6.0)
    assert sm["sentences"] == 2 and sm["words_per_sentence_max"] == 8
    assert sm["hook_text"].startswith("he was alone") and sm["speech_starts_at_s"] == pytest.approx(0.3)
    assert sm["tail_silence_s"] == pytest.approx(1.4) and sm["beats_per_sec"] > 0
    tt = linkage.timed_transcript(subs, link["shots"])
    assert "(shot 1) he was alone" in tt and "(shot 2) at sea for" in tt
    assert "2 | 1.30 | 2.20" in linkage.shots_table(shots, link)
    empty = linkage.link_subtitles_to_shots([], shots)
    assert empty["stats"]["lines"] == 0 and empty["stats"]["pattern"] == "unknown"


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
    sh = sheets / "shot_01.jpg"
    cu = sheets / "cuts_01.jpg"
    for fp in (ov, sh, cu):
        fp.write_bytes(b"jpg")
    entry = _video_entry(video.name, dur=60, pace=150)
    m = entry["metrics"]
    m["video"] = video.name
    m["speech"]["words"] = 9
    m["speech"]["transcript"] = "это тест"
    m["speech"]["transcript_source"] = "subtitles"
    m["shots"] = [{k: v for k, v in s.items() if k in ("i", "start", "end", "len", "transition_in")} for s in entry["shots_final"]]
    m["cuts"] = [{"t": 2.0, "score": 0.3, "z": 20.0, "kind": "hard", "span": 0.0, "subtitle_cue": True}]
    m["subtitles"] = [{"start": 0.3, "end": 2.0, "text": "это короткий тестовый", "conf": 0.9}, {"start": 2.1, "end": 4.0, "text": "ролик про стиль", "conf": 0.9}]
    m["sheets"] = {"overview": [str(ov)], "shots": [str(sh)], "cuts": [str(cu)], "motion": [str(sh)], "keyframes": 6}
    return m


def test_sheets_for_role_and_user_text() -> None:
    entry = _video_entry("a.mp4", dur=60, pace=150)
    m = entry["metrics"]
    m["sheets"] = {"overview": ["/x/overview_01.jpg"], "shots": ["/x/shot_01.jpg"], "cuts": ["/x/cuts_01.jpg"], "motion": ["/x/shot_01.jpg"]}
    assert [p.name for p in agents.sheets_for_role("shots", m)] == ["shot_01.jpg", "cuts_01.jpg"]
    assert [p.name for p in agents.sheets_for_role("visual", m)] == ["overview_01.jpg"]
    assert [p.name for p in agents.sheets_for_role("patterns", m)] == ["shot_01.jpg"]
    assert agents.sheets_for_role("voice", m) == []
    m["subtitles"] = [{"start": 0.3, "end": 2.0, "text": "это короткий тестовый", "conf": 0.9}]
    m["shots"] = entry["shots_final"]
    txt = agents._user_text("shots", m, "a.mp4")
    assert "(shot 1) это короткий тестовый" in txt and "transition_in" in txt and "анкету роли «shots»" in txt
    txt = agents._user_text("patterns", m, "a.mp4", shots_reply=_agent_reply("shots"))
    assert "кот на крыше" in txt
    m["script"] = {"sentences_list": [{"i": 1, "start": 0.3, "end": 2.0, "text": "Это короткий тестовый."}]}
    assert "1. [0.3–2.0] Это короткий тестовый." in agents._user_text("narrative", m, "a.mp4")


def test_run_style_extract_end_to_end(monkeypatch, tmp_path: Path) -> None:
    fake = FakeLlm()
    monkeypatch.setattr(llm, "ask", fake)
    monkeypatch.setattr(extract, "analyze_video", _fake_analyze)
    videos = []
    for i in range(3):
        fp = tmp_path / f"ref{i}.mp4"
        fp.write_bytes(b"mp4")
        videos.append(fp)
    b = budget.LlmBudget(17)
    stages: list[str] = []
    res = asyncio.run(
        job.run_style_extract(
            videos, tmp_path / "style", settings={**job.DEFAULT_SETTINGS, "style_id": "e2e"}, budget=b, model_id="fake", progress=lambda i: stages.append(i["stage"])
        )
    )
    passport = res["passport"]
    assert b.used == 17  # 5 специалистов × 3 ролика + synth + blueprint
    assert passport["llm"] == {"calls_used": 17, "calls_limit": 17, "model": "fake"}
    labels = [c["label"].split(":", 1)[0] for c in fake.calls]
    assert labels[:5] == ["shots", "visual", "voice", "narrative", "patterns"] and labels[-2:] == ["synth", "blueprint"]
    assert fake.calls[0]["files"] == ["shot_01.jpg", "cuts_01.jpg"]  # shots видит шот-листы и пары склеек
    assert passport["style_id"] == "e2e" and passport["label"] == "Тёплые коты"
    assert passport["mapping"]["visual_core_en"].startswith("2D flat vector")
    assert schema.validate_passport(passport) == []
    assert schema.passport_confidence(passport, "visual.medium") == "high"
    assert {"extract", "agent", "aggregate", "synth", "blueprint", "report"} <= set(stages)
    # v2-секции
    assert len(passport["videos"]) == 3 and passport["videos"][0]["shots"][0]["what"] == "кот на крыше"
    assert passport["videos"][0]["shots"][0]["vo_text"].startswith("это короткий")
    assert passport["patterns"][0]["name"] == "jump_zoom" and passport["patterns"][0]["support"] == "3/3"
    assert passport["blueprint"]["chain"] and passport["blueprint"]["prompt_templates"]["script_writer"]
    assert passport["blueprint"]["source"] == "llm+default"
    assert schema.passport_value(passport, "linkage.pattern") == "each_phrase_new_shot"
    out = tmp_path / "style"
    assert (out / "style_passport.draft.json").is_file()
    assert (out / "variance_report.md").is_file()
    report = (out / "style_report.md").read_text(encoding="utf-8")
    assert res["report"] == report and "## 2. Разбор по роликам" in report and "jump_zoom" in report and "кот на крыше" in report
    assert (out / "analysis" / "ref0" / "q_visual.json").is_file() and (out / "analysis" / "ref0" / "q_shots.json").is_file()


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
    # без LLM: blueprint и отчёт всё равно строятся из детерминированных метрик
    bp = res["passport"]["blueprint"]
    assert bp["source"] == "default" and [n["node_type"] for n in bp["chain"]][:3] == ["topic", "plan", "script"]
    assert any(n.get("new") for n in bp["chain"]) and bp["new_nodes_needed"]
    assert "script_writer" in bp["prompt_templates"] and "{topic}" in bp["prompt_templates"]["script_writer"]
    assert res["passport"]["videos"][0]["shots"][0]["vo_text"]
    assert "## 7. Blueprint" in res["report"] and "## 3. Связка закадра и картинки" in res["report"]


# ── report / blueprint (чистые функции) ───────────────────────────────────


def test_render_report_sections_and_blueprint_templates() -> None:
    from app.services.style_agent import blueprint, report

    p = aggregate.build_passport([_video_entry(f"v{i}.mp4", dur=60, pace=150) for i in range(2)], style_id="rep")
    aggregate.apply_synth(p, _agent_reply("synth"))
    p["blueprint"] = blueprint.build_blueprint(p, _agent_reply("blueprint"))
    md = report.render_report(p)
    for h in ("## 1. Сводка стиля", "## 2. Разбор по роликам", "## 3. Связка закадра и картинки", "## 4. Сценарий и повествование", "## 5. Повторяющиеся приёмы", "## 6. Особенности", "## 7. Blueprint", "## 8. Разброс"):
        assert h in md, h
    assert "v0.mp4" in md and "кот на крыше" in md and "00:00.0" in md
    assert "jump_zoom" in md and "scale 1.0→1.15" in md
    assert "«Если {условие}, то {последствие}»" in md and "second_person" in md
    assert "Пиши короткими фразами {topic}" in md  # шаблон из ответа LLM
    # blueprint: ответ LLM поверх дефолтной цепочки, шаблоны дополняются дефолтными
    bp = p["blueprint"]
    assert bp["source"] == "llm+default" and bp["prompt_templates"]["script_writer"] == "Пиши короткими фразами {topic}"
    assert bp["prompt_templates"]["vo_tts"] and bp["prompt_templates"]["edit"]
    assert "shot_plan — нарезка на планы" in bp["new_nodes_needed"]
    tpl = blueprint.prompt_templates(p)
    assert "3" in tpl["subtitles"]  # words_per_line_median из паспорта
    assert "2D flat vector" in tpl["image_prompt"] and "{shot}" in tpl["image_prompt"]
    assert any(n["node_type"] == "shot_plan" and n["new"] for n in blueprint.default_chain(p))
    assert {"type", "role"} <= set(blueprint.node_catalog_for_prompt()[0]) and any(n.get("new") for n in blueprint.node_catalog_for_prompt())
    # отчёт по пустому паспорту не падает
    assert report.render_report(schema.empty_passport()).startswith("# Паспорт стиля")


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
    off = job.estimate(project, None, {"max_llm_calls": 4, "skip_llm": True})
    assert off["plan"]["planned_total"] == 0 and off["cost"]["calls"] == 0 and off["cost"]["usd"] == 0


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


# ── REST: /projects/{id}/style-agent (job и LLM замоканы) ─────────────────


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    return _make_api_client(tmp_path, monkeypatch)


def _make_api_client(tmp_path: Path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base
    from app.settings import settings
    from app.web.api import create_app
    from app.web.deps import get_session

    app = create_app()
    db_file = tmp_path / "style-api.db"
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "sqlite_path", db_file)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _gen():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _gen

    async def _prepare() -> int:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            p = Project(topic="style", slug="style-api", status=ProjectStatus.new, meta={})
            session.add(p)
            await session.commit()
            await session.refresh(p)
            p.data_dir.mkdir(parents=True, exist_ok=True)
            return p.id

    return app, factory, engine, _prepare


def test_style_agent_api_flow(api_client, monkeypatch, tmp_path: Path) -> None:
    from httpx import ASGITransport, AsyncClient

    app, factory, engine, prepare = api_client
    spawned: list[dict[str, Any]] = []

    def _fake_spawn(project_id: int, *, node_key, overrides=None):
        spawned.append({"project_id": project_id, "node_key": node_key, "overrides": overrides})

        class _T:
            def done(self) -> bool:
                return True

        return _T()

    monkeypatch.setattr(job, "spawn_style_extract_job", _fake_spawn)

    async def _run() -> None:
        pid = await prepare()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get(f"/api/projects/{pid}/style-agent")
            assert r.status_code == 200 and r.json()["job"]["status"] == "idle"

            r = await c.patch(f"/api/projects/{pid}/style-agent", json={"max_llm_calls": 3, "junk": 1})
            assert r.status_code == 200 and r.json()["settings"]["max_llm_calls"] == 3
            r = await c.patch(f"/api/projects/{pid}/style-agent", json={"junk": 1})
            assert r.status_code == 400

            r = await c.post(f"/api/projects/{pid}/style-agent/run")
            assert r.status_code == 400  # нет роликов

            r = await c.post(f"/api/projects/{pid}/style-agent/upload", files={"file": ("a.mp4", b"mp4data", "video/mp4")})
            assert r.status_code == 200 and r.json()["videos"] == ["a.mp4"]
            r = await c.post(f"/api/projects/{pid}/style-agent/upload", files={"file": ("x.txt", b"txt", "text/plain")})
            assert r.status_code == 400

            r = await c.post(f"/api/projects/{pid}/style-agent/estimate", json={"max_llm_calls": 5})
            assert r.status_code == 200
            est = r.json()
            assert est["plan"]["n_videos"] == 1 and est["plan"]["max_calls"] == 5 and est["cost"]["usd"] >= 0

            r = await c.post(f"/api/projects/{pid}/style-agent/run?node_key=n_style_1", json={"max_llm_calls": 4})
            assert r.status_code == 200 and r.json()["started"] is True
            assert spawned and spawned[0]["node_key"] == "n_style_1" and spawned[0]["overrides"] == {"max_llm_calls": 4}

            r = await c.get(f"/api/projects/{pid}/style-agent/passport")
            assert r.status_code == 404

            # Черновик кладём напрямую (как это сделал бы job) и утверждаем через API
            async with factory() as s:
                p = await s.get(Project, pid)
                job.write_passport(p, _approved_passport())
            roots = apply.MaterializeRoots(prompts=tmp_path / "p", frame_styles=tmp_path / "f")
            monkeypatch.setattr(apply.MaterializeRoots, "default", classmethod(lambda cls: roots))

            r = await c.get(f"/api/projects/{pid}/style-agent/passport")
            assert r.status_code == 200 and r.json()["passport"]["style_id"] == "warm_cats"

            bad = dict(_approved_passport())
            bad["style_id"] = "Bad!"
            r = await c.put(f"/api/projects/{pid}/style-agent/passport", json={"passport": bad})
            assert r.status_code == 400

            edited = _approved_passport()
            edited["label"] = "Коты (правка)"
            r = await c.put(f"/api/projects/{pid}/style-agent/passport", json={"passport": edited})
            assert r.status_code == 200

            r = await c.post(f"/api/projects/{pid}/style-agent/approve", json={})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["report"]["materialized"] is True and "visual_style" in body["report"]["blocks"]
            assert body["snapshot"]["passport"]["hasApproved"] is True
            assert (tmp_path / "p" / "styles" / "warm_cats.json").is_file()

            r = await c.get(f"/api/projects/{pid}/style-agent/report")
            assert r.status_code == 200 and r.json()["apply_report"]["style_id"] == "warm_cats"
            # style_report.md не записан job'ом → отчёт рендерится из паспорта на лету
            assert "## 1. Сводка стиля" in r.json()["report"] and "warm_cats" in r.json()["report"]
            r = await c.get(f"/api/projects/{pid}/style-agent/report?fmt=md")
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
            assert r.text.startswith("# Паспорт стиля")

            async with factory() as s:
                p = await s.get(Project, pid)
                assert p.prompt_overrides["style_profile"] == "warm_cats"
                assert p.meta["style_agent"]["passport"]["approvedAt"]
                assert p.meta["node_step_params"]["plan"]["duration_seconds"] == 60

            r = await c.delete(f"/api/projects/{pid}/style-agent/refs/a.mp4")
            assert r.status_code == 200 and r.json()["videos"] == []

    try:
        asyncio.run(_run())
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


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
