"""Схема паспорта стиля v1 и контракты ответов специалистов.

Паспорт — JSON: каждое листовое поле обёрнуто в
``{"value", "confidence", "support", "note"}``. Специалисты отвечают
плоскими JSON по своей анкете; код валидирует ключи и типы (пустой список
проблем = ок), как в ``ai_result_io.text_job``.
"""

from __future__ import annotations

import re
from typing import Any

PASSPORT_SCHEMA = "style_passport/v2"
PASSPORT_SCHEMA_V1 = "style_passport/v1"
ACCEPTED_SCHEMAS = (PASSPORT_SCHEMA, PASSPORT_SCHEMA_V1)

CONFIDENCE_LEVELS = ("high", "medium", "low")

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# ── Анкеты специалистов: ключ → тип ────────────────────────────────────────
# Типы: str | num | bool | list[str] | dist (dict[str, float] сумма ≈ 1)
#       | hexlist | obj

VISUAL_FIELDS: dict[str, str] = {
    "medium": "str",
    "palette_hex": "hexlist",
    "saturation": "str",  # low | medium | high
    "contrast": "str",  # low | medium | high
    "line": "str",
    "light": "str",
    "composition": "str",
    "framing_mix": "dist",  # close / medium / full
    "subjects": "str",
    "world": "str",
    "text_in_image": "bool",
    "forbidden": "list",
    "mood": "str",
}

VOICE_FIELDS: dict[str, str] = {
    "language": "str",
    "gender": "str",  # male | female | mixed | none
    "age_band": "str",
    "timbre": "str",
    "intonation": "str",
    "address": "str",
    "hook_first_3s": "str",
    "cta": "str",
    "delivery_markers": "list",
    "music_genre": "str",
    "music_role": "str",
    "sfx_kinds": "list",
    "sfx_density": "str",  # none | sparse | medium | dense
    "voice_name_hint": "str",
    "tts_stability": "num",
    "tts_similarity_boost": "num",
}

MOTION_FIELDS: dict[str, str] = {
    "camera_vocab": "dist",  # slow_push_in / pull_out / pan / tilt / parallax / handheld / static
    "subject_motion": "str",
    "transitions": "dist",  # cut / dissolve / whip / zoom_cut
    "subtitles_enabled": "bool",
    "subtitles_mode": "str",  # word | line | none
    "subtitles_position": "str",
    "subtitles_style": "str",
    "end_card": "bool",
    "vo_cut_sync": "str",
    "rhythm_notes": "str",
}

NARRATIVE_FIELDS: dict[str, str] = {
    "structure": "str",
    "hook_type": "str",
    "segments": "objlist",  # [{name: hook|setup|escalation|turn|payoff|cta, start, end, summary, quote}]
    "beats_per_sec": "num",
    "sentence_templates": "list",  # «X did Y, which Z-ed» — шаблоны предложений с примерами
    "rhetoric_devices": "objlist",  # [{name, example, timecode}]
    "example_hook": "str",
    "example_payoff": "str",
    "address": "str",
    "forbidden_phrases_seed": "list",
    "tone_notes": "str",
}

# Поля одного плана в ответе агента shots (все — строки, кроме i).
SHOT_ENTRY_FIELDS: tuple[str, ...] = (
    "what",
    "framing",  # close | medium | full | wide | insert | pov
    "camera",  # static | push_in | pull_out | pan | tilt | orbit | handheld | whip | crane | follow
    "subject_anim",
    "start_state",
    "end_state",
    "change",
    "transition_out",  # cut | match_cut | dissolve | whip | zoom_jump | morph | none
    "transition_note",
    "illustrates",  # что из фразы закадра показано буквально (существительное/действие)
    "text_on_screen",
    "vfx",
)

SHOTS_FIELDS: dict[str, str] = {
    "shots": "objlist",  # [{i, <SHOT_ENTRY_FIELDS>}] — i соответствует таблице планов
    "false_cuts": "numlist",  # таймкоды (с) склеек, которых на самом деле нет (вспышка/рывок)
    "camera_vocab": "dist",
    "transitions": "dist",
    "subject_motion": "str",
    "subtitles_enabled": "bool",
    "subtitles_mode": "str",
    "subtitles_position": "str",
    "subtitles_style": "str",
    "end_card": "bool",
    "vo_cut_sync": "str",
    "rhythm_notes": "str",
    "shot_construction": "str",  # общий принцип построения плана (начало → изменение → конец)
}

PATTERNS_FIELDS: dict[str, str] = {
    "patterns": "objlist",  # [{name, kind, description, frequency, examples:[tc], how_to_reproduce, signature}]
    "peculiarities": "list",
    "visual_illustrates_noun_ratio": "num",
    "linkage_notes": "str",
    "timing_notes": "str",
}

BLUEPRINT_FIELDS: dict[str, str] = {
    "chain": "objlist",  # [{node_type, label, new, inputs, outputs, params, prompt_template}]
    "prompt_templates": "obj",  # {script_writer, storyboard, image_prompt, video_prompt, vo_tts, subtitles, edit, music_sfx}
    "new_nodes_needed": "list",
    "notes": "list",
}

SYNTH_FIELDS: dict[str, str] = {
    "label": "str",
    "style_id": "str",
    "visual_core_en": "str",
    "lighting_en": "str",
    "negative_en": "list",
    "anim_negative_en": "list",
    "camera_motion_en": "str",
    "composition_en": "str",
    "character_anatomy_en": "str",
    "voice_tone_ru": "str",
    "narrative_ru": "str",
    "forbidden_phrases_ru": "list",
    "music_suno_core_en": "str",
    "frame_style_lock_en": "str",
    "variance_notes": "list",
}

AGENT_FIELDS: dict[str, dict[str, str]] = {
    "visual": VISUAL_FIELDS,
    "voice": VOICE_FIELDS,
    "motion": MOTION_FIELDS,
    "shots": SHOTS_FIELDS,
    "narrative": NARRATIVE_FIELDS,
    "patterns": PATTERNS_FIELDS,
    "synth": SYNTH_FIELDS,
    "blueprint": BLUEPRINT_FIELDS,
}

# Какие поля паспорта — числовые (агрегация медиана/IQR), какие
# категориальные (голосование), распределения, списки, тексты.
NUMERIC_PATHS: tuple[str, ...] = (
    "format.duration_s",
    "format.fps",
    "voice.pace_wpm",
    "voice.chars_per_sec",
    "voice.pause_median_s",
    "voice.pause_p90_s",
    "voice.speech_ratio",
    "voice.tts_stability",
    "voice.tts_similarity_boost",
    "music.level_vs_voice_db",
    "music.loudness_lufs",
    "motion.shot_len_s",
    "motion.shot_len_p10_s",
    "motion.shot_len_p90_s",
    "motion.static_ratio",
    "motion.cuts_per_min",
)

CATEGORICAL_PATHS: tuple[str, ...] = (
    "format.aspect",
    "format.resolution",
    "visual.medium",
    "visual.saturation",
    "visual.contrast",
    "visual.text_in_image",
    "voice.language",
    "voice.gender",
    "voice.age_band",
    "voice.address",
    "voice.cta",
    "voice.music_role",
    "voice.sfx_density",
    "voice.voice_name_hint",
    "music.present",
    "motion.subtitles_enabled",
    "motion.subtitles_mode",
    "motion.subtitles_position",
    "motion.end_card",
    "motion.vo_cut_sync",
    "narrative.hook_type",
)

DIST_PATHS: tuple[str, ...] = (
    "visual.framing_mix",
    "motion.camera_vocab",
    "motion.transitions",
)

LIST_PATHS: tuple[str, ...] = (
    "visual.forbidden",
    "voice.delivery_markers",
    "voice.sfx_kinds",
    "narrative.sentence_templates",
    "narrative.forbidden_phrases_seed",
)

# Списки объектов: объединяются по роликам (без голосования), см. aggregate_objlist.
OBJLIST_PATHS: tuple[str, ...] = (
    "narrative.segments",
    "narrative.rhetoric_devices",
)

# Числовые поля, которые даёт только агент narrative (нет в metrics).
NUMERIC_PATHS = NUMERIC_PATHS + ("narrative.beats_per_sec",)
CATEGORICAL_PATHS = CATEGORICAL_PATHS + ("narrative.address",)

TEXT_PATHS: tuple[str, ...] = (
    "visual.line",
    "visual.light",
    "visual.composition",
    "visual.subjects",
    "visual.world",
    "visual.mood",
    "voice.timbre",
    "voice.intonation",
    "voice.hook_first_3s",
    "voice.music_genre",
    "motion.subject_motion",
    "motion.subtitles_style",
    "motion.rhythm_notes",
    "narrative.structure",
    "narrative.tone_notes",
    "narrative.example_hook",
    "narrative.example_payoff",
    "motion.shot_construction",
)

PALETTE_PATH = "visual.palette_hex"

# Куда ложится ось: нужно UI (группировка полей) и materialize.
AXES: tuple[str, ...] = ("format", "visual", "voice", "music", "sfx", "motion", "narrative", "linkage", "script")


def field(
    value: Any,
    *,
    confidence: str = "low",
    support: str = "manual",
    note: str = "",
) -> dict[str, Any]:
    if confidence not in CONFIDENCE_LEVELS:
        confidence = "low"
    out: dict[str, Any] = {"value": value, "confidence": confidence, "support": support}
    if note:
        out["note"] = note
    return out


def get_path(obj: dict[str, Any], path: str) -> Any:
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def set_path(obj: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def passport_value(passport: dict[str, Any], path: str, default: Any = None) -> Any:
    """``value`` поля паспорта по пути (или default)."""
    raw = get_path(passport, path)
    if isinstance(raw, dict) and "value" in raw:
        return raw.get("value")
    return default if raw is None else raw


def passport_confidence(passport: dict[str, Any], path: str) -> str:
    raw = get_path(passport, path)
    if isinstance(raw, dict):
        c = str(raw.get("confidence") or "low")
        return c if c in CONFIDENCE_LEVELS else "low"
    return "low"


def is_usable(passport: dict[str, Any], path: str) -> bool:
    """Поле можно материализовать автоматически (high/medium и не пусто)."""
    val = passport_value(passport, path)
    if val in (None, "", [], {}):
        return False
    return passport_confidence(passport, path) in ("high", "medium")


def normalize_hex(value: Any) -> str | None:
    s = str(value or "").strip()
    if not s:
        return None
    if not s.startswith("#"):
        s = "#" + s
    if len(s) == 4:  # #abc → #aabbcc
        s = "#" + "".join(ch * 2 for ch in s[1:])
    return s.upper() if _HEX_RE.match(s) else None


def _normalize_dist(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, float] = {}
    for k, v in raw.items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f < 0:
            continue
        out[str(k).strip().lower().replace(" ", "_").replace("-", "_")] = f
    total = sum(out.values())
    if not out or total <= 0:
        return None
    return {k: round(v / total, 3) for k, v in out.items()}


def normalize_obj(raw: dict[str, Any]) -> dict[str, Any]:
    """Объект из ответа модели: ключи → snake_case, строки обрезаны, None выброшены."""
    out: dict[str, Any] = {}
    for k, v in raw.items():
        key = re.sub(r"[^a-z0-9_]+", "_", str(k).strip().lower()).strip("_")
        if not key or v is None or v == "" or v == [] or v == {}:
            continue
        if isinstance(v, str):
            out[key] = v.strip()[:1200]
        elif isinstance(v, list):
            out[key] = [(x.strip()[:400] if isinstance(x, str) else x) for x in v if x not in (None, "")][:40]
        else:
            out[key] = v
    return out


def normalize_shot_entry(raw: dict[str, Any]) -> dict[str, Any]:
    """Запись плана от агента shots: i → int, остальные известные поля → str."""
    out: dict[str, Any] = {}
    i_raw = raw.get("i", raw.get("shot", raw.get("id")))
    try:
        out["i"] = int(str(i_raw).lstrip("#")) if i_raw is not None else None
    except (TypeError, ValueError):
        out["i"] = None
    for key in SHOT_ENTRY_FIELDS:
        v = raw.get(key)
        if v in (None, "", [], {}):
            continue
        out[key] = ", ".join(str(x) for x in v) if isinstance(v, list) else str(v).strip()[:600]
    return out


def validate_agent_reply(agent: str, payload: Any) -> tuple[dict[str, Any], list[str]]:
    """Нормализовать ответ специалиста по анкете → (clean, problems).

    Неизвестные ключи отбрасываются; отсутствующие → None (специалист
    может честно не знать). Тип не сходится → проблема (повтор с фидбеком).
    """
    fields = AGENT_FIELDS.get(agent)
    if fields is None:
        return {}, [f"неизвестный агент {agent!r}"]
    if not isinstance(payload, dict):
        return {}, ["ответ не JSON-объект"]
    clean: dict[str, Any] = {}
    problems: list[str] = []
    for key, kind in fields.items():
        raw = payload.get(key)
        if raw is None or raw == "" or raw == [] or raw == {}:
            clean[key] = None
            continue
        if kind == "str":
            if isinstance(raw, (list, dict)):
                problems.append(f"{key}: ожидалась строка")
                clean[key] = None
            else:
                clean[key] = str(raw).strip()
        elif kind == "num":
            try:
                clean[key] = float(raw)
            except (TypeError, ValueError):
                problems.append(f"{key}: ожидалось число")
                clean[key] = None
        elif kind == "bool":
            if isinstance(raw, bool):
                clean[key] = raw
            elif isinstance(raw, str) and raw.strip().lower() in ("true", "false", "yes", "no", "да", "нет"):
                clean[key] = raw.strip().lower() in ("true", "yes", "да")
            else:
                problems.append(f"{key}: ожидалось true/false")
                clean[key] = None
        elif kind == "list":
            if isinstance(raw, str):
                items = [s.strip() for s in re.split(r"[;,\n]", raw) if s.strip()]
            elif isinstance(raw, list):
                items = [str(x).strip() for x in raw if str(x).strip()]
            else:
                problems.append(f"{key}: ожидался список строк")
                items = []
            clean[key] = items or None
        elif kind == "hexlist":
            items_raw = raw if isinstance(raw, list) else re.split(r"[;,\s]+", str(raw))
            hexes = [h for h in (normalize_hex(x) for x in items_raw) if h]
            if not hexes:
                problems.append(f"{key}: нужны hex-цвета вида #RRGGBB")
            clean[key] = hexes or None
        elif kind == "dist":
            dist = _normalize_dist(raw)
            if dist is None:
                problems.append(f"{key}: ожидался объект {{категория: доля}}")
            clean[key] = dist
        elif kind == "objlist":
            if isinstance(raw, dict):
                raw = [raw]
            if not isinstance(raw, list):
                problems.append(f"{key}: ожидался список объектов")
                clean[key] = None
            else:
                items_o = [normalize_obj(x) for x in raw if isinstance(x, dict)]
                if key == "shots":
                    items_o = [normalize_shot_entry(x) for x in items_o]
                    items_o = [x for x in items_o if x.get("i") is not None]
                if not items_o:
                    problems.append(f"{key}: пустой список объектов")
                clean[key] = items_o or None
        elif kind == "obj":
            if not isinstance(raw, dict):
                problems.append(f"{key}: ожидался объект")
                clean[key] = None
            else:
                clean[key] = normalize_obj(raw)
        elif kind == "numlist":
            items_n: list[float] = []
            for x in raw if isinstance(raw, list) else re.split(r"[;,\s]+", str(raw)):
                try:
                    items_n.append(float(str(x).replace(",", ".")))
                except (TypeError, ValueError):
                    continue
            clean[key] = items_n or None
        else:
            clean[key] = raw
    required_any = [k for k, v in clean.items() if v is not None]
    if not required_any:
        problems.append("все поля анкеты пустые")
    return clean, problems


def empty_passport(style_id: str = "", label: str = "") -> dict[str, Any]:
    return {
        "$schema": PASSPORT_SCHEMA,
        "style_id": style_id,
        "label": label,
        "created_at": "",
        "sources": {"count": 0, "videos": [], "min_support_for_stable": 0.7, "numeric_iqr_threshold": 0.35},
        "format": {},
        "visual": {},
        "voice": {},
        "music": {},
        "sfx": {},
        "motion": {},
        "narrative": {},
        "mapping": {},
        # v2
        "videos": [],
        "linkage": {},
        "script": {},
        "patterns": [],
        "peculiarities": [],
        "blueprint": {},
        "llm": {"calls_used": 0, "calls_limit": 0, "model": ""},
    }


def validate_passport(passport: Any) -> list[str]:
    """Структурная проверка паспорта перед approve/materialize (v1 и v2)."""
    problems: list[str] = []
    if not isinstance(passport, dict):
        return ["паспорт не объект"]
    if passport.get("$schema") not in ACCEPTED_SCHEMAS:
        problems.append(f"$schema должен быть {PASSPORT_SCHEMA} (или {PASSPORT_SCHEMA_V1})")
    for key, typ in (("videos", list), ("patterns", list), ("peculiarities", list), ("blueprint", dict), ("linkage", dict), ("script", dict)):
        if key in passport and passport.get(key) is not None and not isinstance(passport.get(key), typ):
            problems.append(f"{key}: ожидался {'список' if typ is list else 'объект'}")
    sid = str(passport.get("style_id") or "").strip()
    if not re.match(r"^[a-z0-9_\-]{2,64}$", sid):
        problems.append("style_id: латиница/цифры/_/- (2–64 символа)")
    for axis in AXES:
        ax = passport.get(axis)
        if ax is None:
            continue
        if not isinstance(ax, dict):
            problems.append(f"{axis}: ожидался объект")
            continue
        for key, raw in ax.items():
            if not isinstance(raw, dict) or "value" not in raw:
                problems.append(f"{axis}.{key}: поле должно быть {{value, confidence, support}}")
                continue
            if str(raw.get("confidence") or "low") not in CONFIDENCE_LEVELS:
                problems.append(f"{axis}.{key}: confidence ∉ {CONFIDENCE_LEVELS}")
    return problems


def slugify_style_id(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    base = re.sub(r"_+", "_", base)[:48]
    return base or "style"
