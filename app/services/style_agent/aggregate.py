"""Агрегация устойчивого стиля по N роликам → паспорт с уверенностью.

Правила:
- числа — медиана + p10/p90; устойчиво, если IQR/медиана < порога;
- категории — голосование, устойчиво при поддержке ≥ ``min_support``;
- распределения — среднее по роликам;
- палитра — k-means (k=6) в Lab по всем цветам;
- списки — объединение с поддержкой ≥ 50 %;
- тексты — в synth (LLM) или первый непустой при ``skip_llm``.
Уверенность: N=1 → low; устойчиво & N≥3 → high; устойчиво & N=2 → medium;
иначе low (в note — разброс).
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.services.style_agent import schema
from app.services.style_agent.schema import field, get_path, set_path

DEFAULT_MIN_SUPPORT = 0.7
DEFAULT_IQR_THRESHOLD = 0.35

# путь в паспорте → откуда брать значение в профиле ролика (metrics / agent reply)
_METRIC_SOURCES: dict[str, str] = {
    "format.duration_s": "metrics.format.duration_s",
    "format.fps": "metrics.format.fps",
    "format.aspect": "metrics.format.aspect",
    "format.resolution": "metrics.format.resolution",
    "voice.pace_wpm": "metrics.speech.pace_wpm",
    "voice.chars_per_sec": "metrics.speech.chars_per_sec",
    "voice.pause_median_s": "metrics.speech.pause_median_s",
    "voice.pause_p90_s": "metrics.speech.pause_p90_s",
    "voice.speech_ratio": "metrics.speech.speech_ratio",
    "music.present": "metrics.music.present",
    "music.loudness_lufs": "metrics.music.loudness_lufs",
    "music.level_vs_voice_db": "metrics.music.level_vs_voice_db",
    "motion.shot_len_s": "metrics.motion.shot_len_s",
    "motion.shot_len_p10_s": "metrics.motion.shot_len_p10_s",
    "motion.shot_len_p90_s": "metrics.motion.shot_len_p90_s",
    "motion.static_ratio": "metrics.motion.static_ratio",
    "motion.cuts_per_min": "metrics.motion.cuts_per_min",
}
_AGENT_AXIS = {"visual": "visual", "voice": "voice", "motion": "motion", "narrative": "narrative"}
# Поля специалиста voice, которые живут в оси music/sfx паспорта.
_VOICE_REMAP = {
    "music_genre": "music.genre",
    "music_role": "music.role",
    "sfx_kinds": "sfx.kinds",
    "sfx_density": "sfx.density",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _values(videos: list[dict[str, Any]], source: str) -> list[Any]:
    out = []
    for v in videos:
        val = get_path(v, source)
        if val not in (None, "", [], {}):
            out.append(val)
    return out


def _confidence(stable: bool, n_support: int, n_total: int) -> str:
    if n_total <= 1 or n_support <= 1:
        return "low"
    if stable and n_support >= 3:
        return "high"
    if stable and n_support == 2:
        return "medium"
    return "low"


def _support(k: int, n: int) -> str:
    return f"{k}/{n}"


def aggregate_numeric(values: list[float], n_total: int, *, iqr_threshold: float) -> dict[str, Any] | None:
    nums = []
    for v in values:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            continue
    if not nums:
        return None
    med = statistics.median(nums)
    if len(nums) >= 2:
        q1 = _percentile(nums, 25)
        q3 = _percentile(nums, 75)
        iqr = q3 - q1
        rel = iqr / abs(med) if med else (0.0 if iqr == 0 else math.inf)
        stable = rel < iqr_threshold
    else:
        stable = True
        rel = 0.0
    note = ""
    if not stable:
        note = f"разброс: {round(min(nums), 2)}–{round(max(nums), 2)} (IQR/med={rel:.2f})"
    return field(
        round(med, 3),
        confidence=_confidence(stable, len(nums), n_total),
        support=_support(len(nums), n_total),
        note=note,
    ) | {"p10": round(_percentile(nums, 10), 3), "p90": round(_percentile(nums, 90), 3)}


def aggregate_categorical(values: list[Any], n_total: int, *, min_support: float) -> dict[str, Any] | None:
    norm = [str(v).strip().lower() if not isinstance(v, bool) else v for v in values if v not in (None, "")]
    if not norm:
        return None
    counts = Counter(norm)
    top, k = counts.most_common(1)[0]
    share = k / max(1, n_total)
    stable = share >= min_support
    note = "" if stable else "распределение: " + ", ".join(f"{a}×{b}" for a, b in counts.most_common())
    return field(top, confidence=_confidence(stable, k, n_total), support=_support(k, n_total), note=note)


def aggregate_dist(values: list[dict[str, Any]], n_total: int) -> dict[str, Any] | None:
    dists = [schema._normalize_dist(v) for v in values]
    dists = [d for d in dists if d]
    if not dists:
        return None
    keys = sorted({k for d in dists for k in d})
    avg = {k: round(sum(d.get(k, 0.0) for d in dists) / len(dists), 3) for k in keys}
    spread = max((max(d.get(k, 0.0) for d in dists) - min(d.get(k, 0.0) for d in dists)) for k in keys) if keys else 0.0
    stable = spread <= 0.35
    return field(avg, confidence=_confidence(stable, len(dists), n_total), support=_support(len(dists), n_total))


def aggregate_list(values: list[list[Any]], n_total: int, *, min_share: float = 0.5) -> dict[str, Any] | None:
    lists = [[str(x).strip() for x in v if str(x).strip()] for v in values if isinstance(v, list)]
    lists = [lst for lst in lists if lst]
    if not lists:
        return None
    counts: Counter[str] = Counter()
    canon: dict[str, str] = {}
    for lst in lists:
        for item in dict.fromkeys(x.lower() for x in lst):
            counts[item] += 1
            canon.setdefault(item, next(x for x in lst if x.lower() == item))
    n = len(lists)
    kept = [canon[k] for k, c in counts.most_common() if c / n >= min_share]
    if not kept:
        kept = [canon[k] for k, _ in counts.most_common(8)]
        stable = False
    else:
        stable = True
    return field(kept[:20], confidence=_confidence(stable, n, n_total), support=_support(n, n_total))


def aggregate_text(values: list[str], n_total: int) -> dict[str, Any] | None:
    texts = [str(v).strip() for v in values if str(v or "").strip()]
    if not texts:
        return None
    return field(
        texts[0],
        confidence="medium" if len(texts) >= 2 else "low",
        support=_support(len(texts), n_total),
        note="" if len(texts) == 1 else "варианты: " + " | ".join(t[:80] for t in texts[1:4]),
    )


# ── палитра: k-means в Lab (чистый Python) ────────────────────────────────


def _hex_to_rgb(h: str) -> tuple[float, float, float]:
    return int(h[1:3], 16) / 255, int(h[3:5], 16) / 255, int(h[5:7], 16) / 255


def _rgb_to_lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _lab_to_hex(lab: tuple[float, float, float]) -> str:
    L, a, b = lab
    fy = (L + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200

    def finv(t: float) -> float:
        return t**3 if t**3 > 0.008856 else (t - 16 / 116) / 7.787

    x, y, z = finv(fx) * 0.95047, finv(fy), finv(fz) * 1.08883
    r = x * 3.2406 + y * -1.5372 + z * -0.4986
    g = x * -0.9689 + y * 1.8758 + z * 0.0415
    bb = x * 0.0557 + y * -0.2040 + z * 1.0570

    def gam(c: float) -> int:
        c = max(0.0, min(1.0, c))
        c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        return int(round(max(0.0, min(1.0, c)) * 255))

    return f"#{gam(r):02X}{gam(g):02X}{gam(bb):02X}"


def kmeans_palette(hexes: list[str], k: int = 6, *, iters: int = 20) -> list[str]:
    pts = [_rgb_to_lab(_hex_to_rgb(h)) for h in hexes if schema.normalize_hex(h)]
    if not pts:
        return []
    k = min(k, len(pts))
    centers = [pts[int(i * len(pts) / k)] for i in range(k)]
    for _ in range(iters):
        buckets: list[list[tuple[float, float, float]]] = [[] for _ in range(k)]
        for p in pts:
            idx = min(range(k), key=lambda i: sum((p[j] - centers[i][j]) ** 2 for j in range(3)))
            buckets[idx].append(p)
        new_centers = []
        for i, b in enumerate(buckets):
            if b:
                new_centers.append(tuple(sum(p[j] for p in b) / len(b) for j in range(3)))  # type: ignore[misc]
            else:
                new_centers.append(centers[i])
        if new_centers == centers:
            break
        centers = new_centers  # type: ignore[assignment]
    sizes = [sum(1 for p in pts if min(range(k), key=lambda i: sum((p[j] - centers[i][j]) ** 2 for j in range(3))) == ci) for ci in range(k)]
    order = sorted(range(k), key=lambda i: -sizes[i])
    return [_lab_to_hex(centers[i]) for i in order]


def aggregate_palette(videos: list[dict[str, Any]], n_total: int) -> dict[str, Any] | None:
    hexes: list[str] = []
    used = 0
    for v in videos:
        pal = get_path(v, "agents.visual.palette_hex") or get_path(v, "metrics.palette.palette_hex") or []
        norm = [h for h in (schema.normalize_hex(x) for x in pal) if h]
        if norm:
            used += 1
            hexes.extend(norm)
    if not hexes:
        return None
    return field(kmeans_palette(hexes), confidence=_confidence(True, used, n_total), support=_support(used, n_total))


def _percentile(values: list[float], pct: float) -> float:
    vals = sorted(values)
    if not vals:
        return 0.0
    k = (len(vals) - 1) * pct / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return vals[int(k)]
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


# ── главный вход ──────────────────────────────────────────────────────────


def _agent_sources(path: str) -> str | None:
    axis, key = path.split(".", 1)
    for agent, ax in _AGENT_AXIS.items():
        if ax == axis:
            return f"agents.{agent}.{key}"
    for vkey, ppath in _VOICE_REMAP.items():
        if ppath == path:
            return f"agents.voice.{vkey}"
    return None


def _source_for(path: str) -> str | None:
    return _METRIC_SOURCES.get(path) or _agent_sources(path)


def build_passport(
    videos: list[dict[str, Any]],
    *,
    style_id: str = "",
    label: str = "",
    min_support: float = DEFAULT_MIN_SUPPORT,
    iqr_threshold: float = DEFAULT_IQR_THRESHOLD,
) -> dict[str, Any]:
    """``videos``: [{video, metrics, agents:{visual, voice, motion, narrative}}] → паспорт (без mapping)."""
    n = len(videos)
    passport = schema.empty_passport(style_id, label)
    passport["created_at"] = _utc_now()
    passport["sources"] = {
        "count": n,
        "videos": [str(v.get("video") or "") for v in videos],
        "min_support_for_stable": min_support,
        "numeric_iqr_threshold": iqr_threshold,
    }
    for path in schema.NUMERIC_PATHS:
        src = _source_for(path)
        if src:
            agg = aggregate_numeric(_values(videos, src), n, iqr_threshold=iqr_threshold)
            if agg:
                set_path(passport, path, agg)
    for path in schema.CATEGORICAL_PATHS:
        src = _source_for(path)
        if src:
            agg = aggregate_categorical(_values(videos, src), n, min_support=min_support)
            if agg:
                set_path(passport, path, agg)
    for path in schema.DIST_PATHS:
        src = _source_for(path)
        if src:
            agg = aggregate_dist(_values(videos, src), n)
            if agg:
                set_path(passport, path, agg)
    for path in schema.LIST_PATHS:
        src = _source_for(path)
        if src:
            agg = aggregate_list(_values(videos, src), n)
            if agg:
                set_path(passport, path, agg)
    for path in ("music.genre", "music.role", "sfx.density"):
        src = _source_for(path)
        if src:
            agg = aggregate_categorical(_values(videos, src), n, min_support=min_support)
            if agg:
                set_path(passport, path, agg)
    agg = aggregate_list(_values(videos, "agents.voice.sfx_kinds"), n)
    if agg:
        set_path(passport, "sfx.kinds", agg)
    for path in schema.TEXT_PATHS:
        src = _source_for(path)
        if src:
            agg = aggregate_text(_values(videos, src), n)
            if agg:
                set_path(passport, path, agg)
    pal = aggregate_palette(videos, n)
    if pal:
        set_path(passport, schema.PALETTE_PATH, pal)
    # Измеренные saturation/contrast как fallback, если visual-специалист промолчал
    for key in ("saturation", "contrast"):
        if get_path(passport, f"visual.{key}") is None:
            agg = aggregate_categorical(_values(videos, f"metrics.palette.{key}"), n, min_support=min_support)
            if agg:
                set_path(passport, f"visual.{key}", agg)
    return passport


def apply_synth(passport: dict[str, Any], synth: dict[str, Any] | None) -> dict[str, Any]:
    """Ответ synth → ``mapping`` + label/style_id (если пустые)."""
    synth = synth or {}
    mapping = dict(passport.get("mapping") or {})
    for key in schema.SYNTH_FIELDS:
        if key in ("label", "style_id", "variance_notes"):
            continue
        val = synth.get(key)
        if val not in (None, "", []):
            mapping[key] = val
    passport["mapping"] = mapping
    if not passport.get("label") and synth.get("label"):
        passport["label"] = str(synth["label"])
    if not passport.get("style_id") and synth.get("style_id"):
        passport["style_id"] = schema.slugify_style_id(str(synth["style_id"]))
    if synth.get("variance_notes"):
        passport["variance_notes"] = list(synth["variance_notes"])
    return passport


def fallback_mapping(passport: dict[str, Any]) -> dict[str, Any]:
    """Без LLM-синтеза: собрать mapping из полей паспорта (сухо, но валидно)."""
    pv = lambda p, d=None: schema.passport_value(passport, p, d)  # noqa: E731
    pal = pv("visual.palette_hex") or []
    core_parts = [
        pv("visual.medium"),
        ("palette " + ", ".join(pal[:6])) if pal else None,
        f"{pv('visual.saturation')} saturation" if pv("visual.saturation") else None,
        f"{pv('visual.contrast')} contrast" if pv("visual.contrast") else None,
        pv("visual.line"),
        pv("visual.light"),
        pv("visual.composition"),
        pv("visual.mood"),
    ]
    mapping = dict(passport.get("mapping") or {})
    mapping.setdefault("visual_core_en", ", ".join(str(p) for p in core_parts if p))
    if pv("visual.light"):
        mapping.setdefault("lighting_en", str(pv("visual.light")))
    if pv("visual.forbidden"):
        mapping.setdefault("negative_en", list(pv("visual.forbidden")))
    cam = pv("motion.camera_vocab") or {}
    if cam:
        mapping.setdefault(
            "camera_motion_en",
            "Camera: " + ", ".join(f"{k.replace('_', ' ')} {int(round(v * 100))}%" for k, v in sorted(cam.items(), key=lambda kv: -kv[1])),
        )
    fr = pv("visual.framing_mix") or {}
    asp = pv("format.aspect")
    if fr or asp:
        mapping.setdefault(
            "composition_en",
            " ".join(
                x
                for x in (
                    f"Aspect {asp}." if asp else "",
                    ("Framing mix: " + ", ".join(f"{k} {int(round(v * 100))}%" for k, v in fr.items()) + ".") if fr else "",
                )
                if x
            ),
        )
    voice_bits = [
        pv("voice.timbre"),
        pv("voice.intonation"),
        f"обращение: {pv('voice.address')}" if pv("voice.address") else None,
        f"темп ≈ {pv('voice.pace_wpm')} слов/мин" if pv("voice.pace_wpm") else None,
    ]
    if any(voice_bits):
        mapping.setdefault("voice_tone_ru", "Тон: " + "; ".join(str(b) for b in voice_bits if b) + ".")
    if pv("narrative.structure"):
        segs = pv("narrative.segments") or []
        mapping.setdefault(
            "narrative_ru",
            f"Структура: {pv('narrative.structure')}." + (f" Сегменты: {', '.join(segs)}." if segs else "") + (f" Хук: {pv('narrative.hook_type')}." if pv("narrative.hook_type") else ""),
        )
    if pv("narrative.forbidden_phrases_seed"):
        mapping.setdefault("forbidden_phrases_ru", list(pv("narrative.forbidden_phrases_seed")))
    if pv("music.present") and pv("music.genre"):
        mapping.setdefault("music_suno_core_en", f"{pv('music.genre')}, {pv('music.role') or 'bed'}, instrumental, no vocals")
    if mapping.get("visual_core_en"):
        mapping.setdefault("frame_style_lock_en", mapping["visual_core_en"][:900])
    passport["mapping"] = mapping
    return passport


def variance_report(passport: dict[str, Any]) -> str:
    """Markdown: что устойчиво, что расходится (для hitl_style и оператора)."""
    lines = [f"# Отчёт разброса — {passport.get('label') or passport.get('style_id') or 'стиль'}", ""]
    src = passport.get("sources") or {}
    lines.append(f"Роликов: {src.get('count', 0)}; порог поддержки {src.get('min_support_for_stable')}, IQR {src.get('numeric_iqr_threshold')}.")
    lines.append("")
    by_conf: dict[str, list[str]] = {"high": [], "medium": [], "low": []}
    for axis in schema.AXES:
        ax = passport.get(axis) or {}
        for key, raw in ax.items():
            if not isinstance(raw, dict):
                continue
            c = str(raw.get("confidence") or "low")
            val = raw.get("value")
            if isinstance(val, (list, dict)):
                val_s = str(val)[:80]
            else:
                val_s = str(val)[:80]
            note = f" — {raw['note']}" if raw.get("note") else ""
            by_conf.setdefault(c, []).append(f"- `{axis}.{key}` = {val_s} ({raw.get('support')}){note}")
    for c, title in (("high", "Устойчиво (high)"), ("medium", "Скорее устойчиво (medium)"), ("low", "Разброс / мало данных (low)")):
        lines.append(f"## {title}")
        lines.extend(by_conf.get(c) or ["- —"])
        lines.append("")
    notes = passport.get("variance_notes") or []
    if notes:
        lines.append("## Заметки синтеза")
        lines.extend(f"- {n}" for n in notes)
        lines.append("")
    llm_info = passport.get("llm") or {}
    lines.append(f"LLM: {llm_info.get('calls_used', 0)}/{llm_info.get('calls_limit', 0)} вызовов, модель {llm_info.get('model') or '—'}.")
    return "\n".join(lines)
