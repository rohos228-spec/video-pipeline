"""Материализация утверждённого паспорта в артефакты Studio.

Создаёт:
- ``prompts/blocks/<category>/<style_id>.md`` — блоки v2 (visual_style,
  lighting, negative, anim_negative, camera_motion, composition, voice_tone,
  script_voice_tone, plan_voice_tone, narrative_structure,
  script_narrative_structure, forbidden_phrases, character_anatomy);
- ``prompts/styles/<style_id>.json`` — пресет стиля (blocks + vars);
- ``templates/node_groups/script_frames_qc/styles/style_<style_id>.md`` —
  style lock для script_frames_qc (≤ ``frame_style.max_style_chars``);
- патч ``Project.prompt_overrides`` (style_profile, use_blocks_v2) и
  ``meta.node_step_params`` (длительность, параметры split/audio/assemble).

Существующие файлы бэкапятся в ``*.bak-<ts>``. Корни путей переопределяемы
(тесты пишут во временную папку).
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.style_agent import schema
from app.services.style_agent.schema import is_usable, passport_value

FRAME_STYLE_LOCK_MAX = 1800  # с запасом до frame_style.max_style_chars() (4677)
_HEX_LINE_MAX = 6


@dataclass
class MaterializeRoots:
    prompts: Path
    frame_styles: Path
    backups: bool = True
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    backed_up: list[str] = field(default_factory=list)

    @classmethod
    def default(cls) -> "MaterializeRoots":
        from app.project_root import find_project_root
        from app.services import frame_style as fs

        root = find_project_root()
        return cls(prompts=root / "prompts", frame_styles=fs.styles_dir())


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _write(roots: MaterializeRoots, path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and roots.backups:
        bak = path.with_name(path.name + f".bak-{_ts()}")
        shutil.copyfile(path, bak)
        roots.backed_up.append(str(bak))
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    roots.written.append(str(path))


def _as_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [s.strip() for s in re.split(r"[;,\n]", val) if s.strip()]
    return []


def _neg_line(items: list[str]) -> str:
    return "--no " + ", ".join(dict.fromkeys(items)) if items else ""


# ── тексты блоков из паспорта ────────────────────────────────────────────


def render_blocks(passport: dict[str, Any]) -> dict[str, str]:
    """category → текст блока. Пустые/низкоуверенные поля пропускаются."""
    m = dict(passport.get("mapping") or {})
    pv = lambda p, d=None: passport_value(passport, p, d)  # noqa: E731
    out: dict[str, str] = {}

    core = str(m.get("visual_core_en") or "").strip()
    if core:
        neg = _as_list(m.get("negative_en")) or _as_list(pv("visual.forbidden"))
        parts = [core]
        if neg:
            parts.append("Не " + ", не ".join(neg[:8]) + ".")
            parts.append(_neg_line(neg))
        out["visual_style"] = "\n\n".join(parts)

    light = str(m.get("lighting_en") or "").strip()
    if light:
        out["lighting"] = light

    neg = _as_list(m.get("negative_en"))
    if neg:
        out["negative"] = _neg_line(neg)

    aneg = _as_list(m.get("anim_negative_en"))
    if aneg:
        out["anim_negative"] = "Avoid: " + ", ".join(aneg) + "."

    cam = str(m.get("camera_motion_en") or "").strip()
    if cam:
        out["camera_motion"] = cam

    comp = str(m.get("composition_en") or "").strip()
    if comp:
        out["composition"] = comp

    fr = pv("visual.framing_mix")
    if isinstance(fr, dict) and fr and is_usable(passport, "visual.framing_mix"):
        out["camera_framing"] = "Framing mix: " + ", ".join(
            f"{k} {int(round(float(v) * 100))}%" for k, v in sorted(fr.items(), key=lambda kv: -float(kv[1]))
        ) + "."

    anat = str(m.get("character_anatomy_en") or "").strip()
    if anat:
        out["character_anatomy"] = anat

    world = pv("visual.world")
    if world and is_usable(passport, "visual.world"):
        out["world"] = str(world)

    voice = str(m.get("voice_tone_ru") or "").strip()
    if voice:
        out["voice_tone"] = voice
        out["script_voice_tone"] = voice
        out["plan_voice_tone"] = voice

    narr = str(m.get("narrative_ru") or "").strip()
    if narr:
        out["narrative_structure"] = narr
        out["script_narrative_structure"] = narr

    phrases = _as_list(m.get("forbidden_phrases_ru"))
    if phrases:
        out["forbidden_phrases"] = "Запрещённые штампы (не использовать):\n" + "\n".join(f"- {p}" for p in phrases)
    return out


def render_frame_style(passport: dict[str, Any]) -> str:
    """STYLE / Final style lock / Negative для script_frames_qc."""
    m = dict(passport.get("mapping") or {})
    lock = str(m.get("frame_style_lock_en") or m.get("visual_core_en") or "").strip()
    if not lock:
        return ""
    neg = _as_list(m.get("negative_en")) or _as_list(passport_value(passport, "visual.forbidden")) or []
    pal = passport_value(passport, "visual.palette_hex") or []
    pal_s = f" Palette: {', '.join(pal[:_HEX_LINE_MAX])}." if pal else ""
    text = (
        f"STYLE: {lock}{pal_s} One unified frame. Improve only inside this style, never change medium.\n\n"
        f"Final style lock: {lock[:600]}."
    )
    if neg:
        text += " Negative: " + ", ".join(neg[:14]) + "."
    return text[:FRAME_STYLE_LOCK_MAX]


def render_preset(passport: dict[str, Any], blocks_written: dict[str, str], style_id: str) -> dict[str, Any]:
    pv = lambda p, d=None: passport_value(passport, p, d)  # noqa: E731
    vars_: dict[str, Any] = {}
    dur = pv("format.duration_s")
    if dur and is_usable(passport, "format.duration_s"):
        sec = int(round(float(dur)))
        vars_["VIDEO_DURATION_SEC"] = sec
        cps = pv("voice.chars_per_sec")
        rate = float(cps) if cps and is_usable(passport, "voice.chars_per_sec") else 14.0
        ratio = pv("voice.speech_ratio")
        speaking = sec * (float(ratio) if ratio and is_usable(passport, "voice.speech_ratio") else 0.9)
        chars = int(round(speaking * rate))
        vars_["VOICEOVER_MIN_CHARS"] = max(100, int(chars * 0.92))
        vars_["VOICEOVER_MAX_CHARS"] = max(vars_["VOICEOVER_MIN_CHARS"] + 50, int(chars * 1.08))
    asp = pv("format.aspect")
    if asp and is_usable(passport, "format.aspect"):
        vars_["ASPECT_RATIO_VIDEO"] = str(asp)
    return {
        "label": passport.get("label") or style_id,
        "description": f"Паспорт стиля {style_id} (style_agent, {passport.get('sources', {}).get('count', 0)} роликов)",
        "blocks": {cat: style_id for cat in blocks_written},
        "vars": vars_,
        "style_passport": {"style_id": style_id, "schema": passport.get("$schema")},
    }


def node_step_params_patch(passport: dict[str, Any]) -> dict[str, Any]:
    """Патч ``meta.node_step_params`` по измеренным полям."""
    pv = lambda p, d=None: passport_value(passport, p, d)  # noqa: E731
    patch: dict[str, Any] = {}
    dur = pv("format.duration_s")
    if dur and is_usable(passport, "format.duration_s"):
        patch["plan"] = {"duration_seconds": int(round(float(dur)))}
    shot = pv("motion.shot_len_s")
    cps = pv("voice.chars_per_sec")
    if shot and is_usable(passport, "motion.shot_len_s"):
        rate = float(cps) if cps and is_usable(passport, "voice.chars_per_sec") else 14.0
        avg = float(shot) * rate
        patch["split"] = {
            "cell_min_chars": max(40, int(round(avg * 0.7))),
            "cell_max_chars": max(80, int(round(avg * 1.4))),
        }
    assemble: dict[str, Any] = {}
    lvl = pv("music.level_vs_voice_db")
    if pv("music.present") and lvl is not None and is_usable(passport, "music.level_vs_voice_db"):
        # −20 dB → ≈ 20 %, −10 dB → ≈ 45 %, 0 dB → 70 %
        assemble["bgm_level"] = int(max(5, min(90, 70 + float(lvl) * 2.5)))
    subs = pv("motion.subtitles_enabled")
    if subs is not None and is_usable(passport, "motion.subtitles_enabled"):
        assemble["subtitles_enabled"] = bool(subs)
    if assemble:
        patch["assemble"] = assemble
    audio: dict[str, Any] = {}
    for key, src in (("stability", "voice.tts_stability"), ("similarity_boost", "voice.tts_similarity_boost")):
        val = pv(src)
        if val is not None and is_usable(passport, src):
            audio[key] = round(float(val), 2)
    hint = pv("voice.voice_name_hint")
    if hint and is_usable(passport, "voice.voice_name_hint"):
        audio["voice_hint"] = str(hint)
    if audio:
        patch["audio"] = audio
    return patch


def materialize(
    passport: dict[str, Any],
    *,
    roots: MaterializeRoots | None = None,
    style_id: str | None = None,
) -> dict[str, Any]:
    """Записать файлы; вернуть отчёт (что создано, что пропущено, патчи для project)."""
    problems = schema.validate_passport(passport)
    sid = schema.slugify_style_id(style_id or str(passport.get("style_id") or ""))
    if problems and not sid:
        raise ValueError("; ".join(problems))
    roots = roots or MaterializeRoots.default()

    blocks = render_blocks(passport)
    for category, text in blocks.items():
        _write(roots, roots.prompts / "blocks" / category / f"{sid}.md", text)
    if not blocks:
        roots.skipped.append("blocks: нет пригодных полей (visual_core_en пуст)")

    frame_style_text = render_frame_style(passport)
    frame_style_name = ""
    if frame_style_text:
        from app.services import frame_style as fs

        cap = fs.max_style_chars()
        if len(frame_style_text) > cap:
            frame_style_text = frame_style_text[: cap - 1]
        frame_style_name = f"style_{sid}"
        _write(roots, roots.frame_styles / f"{frame_style_name}.md", frame_style_text)
    else:
        roots.skipped.append("frame_style: нет style lock")

    preset = render_preset(passport, blocks, sid)
    _write(roots, roots.prompts / "styles" / f"{sid}.json", json.dumps(preset, ensure_ascii=False, indent=2))

    nsp = node_step_params_patch(passport)
    music_core = str((passport.get("mapping") or {}).get("music_suno_core_en") or "").strip()
    report = {
        "style_id": sid,
        "at": datetime.now(timezone.utc).isoformat(),
        "blocks": sorted(blocks),
        "preset": str(roots.prompts / "styles" / f"{sid}.json"),
        "frame_style": frame_style_name,
        "written": list(roots.written),
        "backed_up": list(roots.backed_up),
        "skipped": list(roots.skipped),
        "prompt_overrides_patch": {"style_profile": sid, "use_blocks_v2": True},
        "node_step_params_patch": nsp,
        "music_suno_core_en": music_core,
        "passport_problems": problems,
        "next_steps": [
            f"На ноде «Стиль кадров» (script_frames_qc) выберите {frame_style_name}" if frame_style_name else "",
            "Голос ElevenLabs выберите на ноде «Озвучка» по подсказке voice_hint" if nsp.get("audio") else "",
            f"Suno: ядро промпта музыки — {music_core[:120]}" if music_core else "",
        ],
    }
    report["next_steps"] = [s for s in report["next_steps"] if s]
    return report


def apply_to_project(project: Any, report: dict[str, Any]) -> None:
    """Применить патчи отчёта к ORM-проекту (prompt_overrides + meta.node_step_params)."""
    from app.services.project_meta import apply_project_meta_patch

    po = dict(project.prompt_overrides or {}) if isinstance(getattr(project, "prompt_overrides", None), dict) else {}
    po.update(report.get("prompt_overrides_patch") or {})
    project.prompt_overrides = po
    nsp_patch = report.get("node_step_params_patch") or {}
    if nsp_patch:
        meta = project.meta if isinstance(project.meta, dict) else {}
        cur = dict(meta.get("node_step_params") or {}) if isinstance(meta.get("node_step_params"), dict) else {}
        for step, bucket in nsp_patch.items():
            merged = dict(cur.get(step) or {}) if isinstance(cur.get(step), dict) else {}
            merged.update(bucket)
            cur[step] = merged
        apply_project_meta_patch(project, {"node_step_params": cur}, source="style_agent.apply")
