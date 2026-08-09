"""Разворот camera shot_plan в явные шоты + сцены по битам SET.

Camera часто пишет один JSON-бит с лестницей ``VLS→MS→CU`` и ``набор: SET_01``.
Это УЖЕ описание сцены из нескольких визуальных кадров. Сборщик обязан:
1) развернуть лестницу/SET в список шотов;
2) склеить VO-Frame пайплайна в сцену так, чтобы на бит было ≥2 Frame
   (если в лестнице/SET ≥2 шота) — сцена ≠ 1 Frame.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from loguru import logger

from app.models import Frame
from app.project_root import find_project_root
from app.services.scene_design.chronology import _norm, frame_offsets
from app.services.scene_design.context_builder import frame_seconds

_LADDER_SPLIT = re.compile(r"\s*(?:→|↔|->|⇒)\s*")
_SET_HEADER = re.compile(
    r"^##\s+(SET_\d+)\b[^\n]*?\[\s*(\d+)\s*[–\-]\s*(\d+)\s*с\s*\|\s*"
    r"(\d+)\s*[–\-]\s*(\d+)\s*кадр",
    re.MULTILINE | re.IGNORECASE,
)
_SET_HEADER_LOOSE = re.compile(
    r"^##\s+(SET_\d+)\b[^\n]*?(\d+)\s*[–\-]\s*(\d+)\s*кадр",
    re.MULTILINE | re.IGNORECASE,
)


def parse_krupnost_ladder(raw: str) -> list[str]:
    """``VLS→CU`` / ``MCU left↔MCU right→CU`` → список шагов крупности."""
    text = (raw or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in _LADDER_SPLIT.split(text) if p.strip()]
    # Убрать хвосты вроде "left" отдельно не надо — "MCU left" ок как шаг.
    return parts if parts else [text]


def load_set_shot_counts(catalog_path: Path | None = None) -> dict[str, tuple[int, int]]:
    """SET_xx → (min_shots, max_shots) из заголовков camera_sets.md."""
    path = catalog_path or (
        find_project_root() / "prompts" / "scene_design" / "camera_sets.md"
    )
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    out: dict[str, tuple[int, int]] = {}
    for m in _SET_HEADER.finditer(text):
        sid = m.group(1).upper()
        a, b = int(m.group(4)), int(m.group(5))
        out[sid] = (min(a, b), max(a, b))
    for m in _SET_HEADER_LOOSE.finditer(text):
        sid = m.group(1).upper()
        if sid in out:
            continue
        a, b = int(m.group(2)), int(m.group(3))
        out[sid] = (min(a, b), max(a, b))
    return out


def required_shots_for_beat(
    shot: dict[str, Any],
    set_counts: dict[str, tuple[int, int]],
) -> int:
    ladder = parse_krupnost_ladder(str(shot.get("крупность") or ""))
    n_ladder = len(ladder) if ladder else 1
    nab = str(shot.get("набор") or "").strip().upper()
    if nab in ("", "NONE", "NULL", "NONE."):
        nab = ""
    n_set = set_counts.get(nab, (0, 0))[0] if nab else 0
    return max(n_ladder, n_set, 1)


def expand_shot_plan_rows(
    shots: list[dict[str, Any]],
    set_counts: dict[str, tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    """Один бит с лестницей → несколько строк shot_plan (по шагу)."""
    set_counts = set_counts if set_counts is not None else load_set_shot_counts()
    expanded: list[dict[str, Any]] = []
    for beat_i, sh in enumerate(shots):
        if not isinstance(sh, dict):
            continue
        ladder = parse_krupnost_ladder(str(sh.get("крупность") or ""))
        if not ladder:
            ladder = ["MS"]
        need = required_shots_for_beat(sh, set_counts)
        # Если SET требует больше шагов, чем стрелок — добиваем последней крупностью.
        while len(ladder) < need:
            ladder.append(ladder[-1])
        moves = parse_krupnost_ladder(str(sh.get("движение") or ""))
        for step_i, kr in enumerate(ladder):
            row = copy.deepcopy(sh)
            row["крупность"] = kr
            row["shot_index"] = step_i + 1
            row["shots_in_beat"] = len(ladder)
            row["beat_index"] = beat_i + 1
            row["beat_цитата"] = sh.get("цитата")
            if moves:
                row["движение"] = moves[min(step_i, len(moves) - 1)]
            expanded.append(row)
    return expanded


def _frame_row(fr: Frame) -> dict[str, Any]:
    sec, _src = frame_seconds(fr)
    return {
        "uuid": fr.uuid,
        "number": fr.number,
        "закадр": (fr.voiceover_text or "").strip(),
        "время_сек": sec,
    }


def _beat_vo_spans(
    shots: list[dict[str, Any]], full_vo: str
) -> list[tuple[int, int]]:
    """[start, end) для каждого бита по цитате → до следующей цитаты."""
    vo_norm = _norm(full_vo)
    starts: list[int] = []
    for sh in shots:
        q = _norm(str(sh.get("цитата") or ""))
        if not q:
            starts.append(-1)
            continue
        idx = vo_norm.find(q)
        starts.append(idx)
    spans: list[tuple[int, int]] = []
    n = len(starts)
    for i, st in enumerate(starts):
        if st < 0:
            spans.append((-1, -1))
            continue
        end = len(vo_norm)
        for j in range(i + 1, n):
            if starts[j] > st:
                end = starts[j]
                break
        spans.append((st, end))
    return spans


def assign_frames_to_camera_beats(
    frames: list[Frame],
    shots: list[dict[str, Any]],
    full_vo: str,
) -> list[list[Frame]]:
    """Каждый camera-бит → список Frame, чей закадр попал в span цитаты."""
    if not shots:
        return [[f for f in frames if f.uuid]]
    offsets = frame_offsets(frames, full_vo)
    spans = _beat_vo_spans(shots, full_vo)
    buckets: list[list[Frame]] = [[] for _ in shots]
    orphan: list[Frame] = []
    for fr in frames:
        if not fr.uuid:
            continue
        off = offsets.get(fr.uuid)
        if off is None:
            orphan.append(fr)
            continue
        placed = False
        for i, (lo, hi) in enumerate(spans):
            if lo < 0:
                continue
            if lo <= off < hi:
                buckets[i].append(fr)
                placed = True
                break
        if not placed:
            orphan.append(fr)
    # Сироты — к ближайшему непустому биту слева, иначе в первый.
    for fr in orphan:
        off = offsets.get(fr.uuid) if fr.uuid else None
        target = 0
        if off is not None:
            for i, (lo, hi) in enumerate(spans):
                if lo >= 0 and lo <= off:
                    target = i
        buckets[target].append(fr)
    for b in buckets:
        b.sort(key=lambda f: f.number or 0)
    return buckets


def merge_beats_for_multi_shot(
    shots: list[dict[str, Any]],
    buckets: list[list[Frame]],
    need: list[int],
) -> tuple[list[dict[str, Any]], list[list[Frame]], list[int]]:
    """Биты с лестницей/SET ≥2 шота не могут остаться с 1 Frame — поглощаем следующих."""
    out_shots: list[dict[str, Any]] = []
    out_buckets: list[list[Frame]] = []
    out_need: list[int] = []
    i = 0
    n = len(shots)
    while i < n:
        frs = list(buckets[i])
        req = int(need[i])
        sh = shots[i]
        j = i + 1
        # Пока camera требует несколько шотов, а VO-Frame мало — забираем следующие биты.
        target_frames = max(req, 2) if req >= 2 else 1
        while req >= 2 and len(frs) < target_frames and j < n:
            frs.extend(buckets[j])
            # Лестницу ведущего бита не размываем — это его SET; req остаётся от лидера.
            j += 1
        # Если всё ещё 1 Frame при req≥2 — всё равно тащим ещё бит (если есть).
        while req >= 2 and len(frs) < 2 and j < n:
            frs.extend(buckets[j])
            j += 1
        frs.sort(key=lambda f: f.number or 0)
        # Дедуп uuid
        seen: set[str] = set()
        uniq: list[Frame] = []
        for f in frs:
            if not f.uuid or f.uuid in seen:
                continue
            seen.add(f.uuid)
            uniq.append(f)
        if uniq:
            out_shots.append(sh)
            out_buckets.append(uniq)
            out_need.append(req)
        i = max(j, i + 1)
    return out_shots, out_buckets, out_need


def _pick_unique_quote(
    text: str,
    full_vo: str,
    used: set[str],
    *,
    from_end: bool,
) -> str:
    """Цитата из текста кадра: есть в закадре, не совпадает с уже взятыми."""
    vo_norm = _norm(full_vo)
    words = (text or "").strip().split()
    if not words:
        return ""
    # Растим окно, пока цитата не станет уникальной среди used.
    for n in range(min(6, len(words)), len(words) + 1):
        chunk = " ".join(words[-n:] if from_end else words[:n])
        cn = _norm(chunk)
        if cn and cn in vo_norm and cn not in used:
            used.add(cn)
            return chunk
    # Весь текст кадра — последний шанс.
    whole = " ".join(words)
    wn = _norm(whole)
    if wn and wn in vo_norm and wn not in used:
        used.add(wn)
        return whole
    # Fallback: уникальный хвост/голова из полного закадра вокруг вхождения.
    idx = vo_norm.find(wn) if wn else -1
    if idx < 0:
        idx = 0
    vo_words = full_vo.split()
    # Грубо: берём сдвиг по словам, пока не уникально.
    for extra in range(1, 12):
        if from_end:
            chunk = " ".join(vo_words[max(0, len(vo_words) - 8 - extra) :])
        else:
            chunk = " ".join(vo_words[: 8 + extra])
        cn = _norm(chunk)
        if cn and cn in vo_norm and cn not in used:
            used.add(cn)
            return chunk
    used.add(wn or "scene")
    return whole or "scene"


def rebuild_scenes_from_camera(
    frames: list[Frame],
    assembly_input: dict[str, Any],
    full_vo: str,
    *,
    set_counts: dict[str, tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Пересобрать scenes_chrono + shot_plan_chrono от camera-битов (не от action 1:1)."""
    set_counts = set_counts if set_counts is not None else load_set_shot_counts()
    shots = [
        s
        for s in (assembly_input.get("shot_plan_chrono") or [])
        if isinstance(s, dict)
    ]
    if not shots:
        logger.warning("camera_expand: нет shot_plan — scenes_chrono не трогаем")
        return assembly_input

    need = [required_shots_for_beat(s, set_counts) for s in shots]
    buckets = assign_frames_to_camera_beats(frames, shots, full_vo)
    shots, buckets, need = merge_beats_for_multi_shot(shots, buckets, need)

    expanded = expand_shot_plan_rows(shots, set_counts)
    scenes: list[dict[str, Any]] = []
    scene_i = 0
    used_quotes: set[str] = set()
    for beat_i, (sh, frs, req) in enumerate(zip(shots, buckets, need)):
        if not frs:
            continue
        scene_i += 1
        kids = [_frame_row(f) for f in frs]
        sw = _pick_unique_quote(
            frs[0].voiceover_text or str(sh.get("цитата") or ""),
            full_vo,
            used_quotes,
            from_end=False,
        )
        ew = _pick_unique_quote(
            frs[-1].voiceover_text or sw,
            full_vo,
            used_quotes,
            from_end=True,
        )
        if not ew:
            ew = sw
        ladder = parse_krupnost_ladder(str(sh.get("крупность") or ""))
        scenes.append(
            {
                "id_scene": f"scene_{scene_i:02d}",
                "start_words": sw,
                "end_words": ew,
                "время_сек": round(sum(float(k["время_сек"]) for k in kids), 1),
                "кадры": kids,
                "кадров": len(kids),
                "набор": sh.get("набор"),
                "camera_beat": beat_i + 1,
                "camera_shots_required": req,
                "camera_ladder": ladder,
                "мотив": sh.get("мотив") or "",
                "структура_сцены": "continuity",
                "тип_стыка": sh.get("тип_стыка") or "action",
                "переход_в_сцену": sh.get("переход") or "cut",
                "цепь_действия": [],
                "смысл_сцены": str(sh.get("мотив") or "")[:200],
            }
        )

    out = copy.deepcopy(assembly_input)
    out["scenes_chrono"] = scenes
    out["shot_plan_chrono"] = expanded
    out["shot_plan_beats"] = shots
    out["camera_expand_report"] = {
        "beats_in": len(assembly_input.get("shot_plan_chrono") or []),
        "beats_out": len(shots),
        "scenes": len(scenes),
        "expanded_shots": len(expanded),
        "single_frame_scenes": sum(1 for s in scenes if s["кадров"] < 2),
        "multi_frame_scenes": sum(1 for s in scenes if s["кадров"] >= 2),
        "need_ge2_but_got1": sum(
            1
            for s in scenes
            if int(s.get("camera_shots_required") or 1) >= 2 and s["кадров"] < 2
        ),
    }
    logger.info(
        "camera_expand: in_beats={} out_scenes={} expanded_shots={} multi={} single={} stuck1={}",
        out["camera_expand_report"]["beats_in"],
        len(scenes),
        len(expanded),
        out["camera_expand_report"]["multi_frame_scenes"],
        out["camera_expand_report"]["single_frame_scenes"],
        out["camera_expand_report"]["need_ge2_but_got1"],
    )
    return out
