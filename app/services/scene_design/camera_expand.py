"""Разворот camera SET/лестницы: много кадров на одну VO-ячейку.

Модель:
- Ячейка закадра + аудиометки = временной диапазон текста (не режем текст).
- К одной ячейке может относиться много Frame (шоты SET).
- Сцена ≠ ячейка: сцена — смысловой кусок с таймингом озвучки; в ней может
  быть много кадров. Многокадровая сцена ок, если её время = озвучке отрезка.

При дроблении: полный ``voiceover_text`` остаётся только у родителя; дети —
визуальные шоты с пустым закадром и долей duration.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.project_root import find_project_root
from app.services.db_v2 import insert_frame_after
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
_ATTR_KEY = "camera_subdivide"
_MIN_SEC_PER_SHOT = 2.0


def parse_krupnost_ladder(raw: str) -> list[str]:
    """``VLS→CU`` / ``MCU left↔MCU right→CU`` → список шагов крупности."""
    text = (raw or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in _LADDER_SPLIT.split(text) if p.strip()]
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
    *,
    duration_sec: float = 0.0,
) -> int:
    ladder = parse_krupnost_ladder(str(shot.get("крупность") or ""))
    n_ladder = len(ladder) if ladder else 1
    nab = str(shot.get("набор") or "").strip().upper()
    if nab in ("", "NONE", "NULL", "NONE."):
        nab = ""
    n_set = set_counts.get(nab, (0, 0))[0] if nab else 0
    need = max(n_ladder, n_set, 1)
    # Если GPT сдал один план на длинный VO — всё равно дробим (минимум динамики).
    if duration_sec >= 8.0 and need < 3:
        need = 3
    elif duration_sec >= 4.0 and need < 2:
        need = 2
    return need


def clamp_shots_to_duration(need: int, duration_sec: float) -> int:
    """Не плодим шоты короче ~2с — сжатие SET по каталогу."""
    need = max(1, int(need))
    if duration_sec <= 0:
        return need
    by_time = max(1, int(duration_sec // _MIN_SEC_PER_SHOT))
    return max(1, min(need, by_time))


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
        dur = float(sh.get("время_сек") or sh.get("duration_sec") or 0.0)
        need = required_shots_for_beat(sh, set_counts, duration_sec=dur)
        if len(ladder) <= 1 and need >= 3:
            ladder = ["VLS", "MS", "CU"]
        elif len(ladder) <= 1 and need >= 2:
            ladder = ["MS", "CU"]
        while len(ladder) < need:
            ladder.append(ladder[-1])
        ladder = ladder[:need]
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


def split_text_into_parts(text: str, n: int) -> list[str]:
    """Разрезать закадр на n кусков по словам (хвост забирает остаток)."""
    words = (text or "").strip().split()
    n = max(1, int(n))
    if n == 1:
        return [" ".join(words)]
    if not words:
        return [""] * n
    if len(words) < n:
        # Мало слов — первые куски по слову, пустые хвосты недопустимы: дублируем.
        parts = words + [words[-1]] * (n - len(words))
        return parts[:n]
    base, rem = divmod(len(words), n)
    parts: list[str] = []
    i = 0
    for k in range(n):
        take = base + (1 if k < rem else 0)
        parts.append(" ".join(words[i : i + take]))
        i += take
    return parts


def _frame_row(fr: Frame) -> dict[str, Any]:
    sec, _src = frame_seconds(fr)
    return {
        "uuid": fr.uuid,
        "number": fr.number,
        "закадр": (fr.voiceover_text or "").strip(),
        "время_сек": sec,
    }


def _subdivide_attr(fr: Frame) -> dict[str, Any]:
    attrs = fr.attrs if isinstance(fr.attrs, dict) else {}
    raw = attrs.get(_ATTR_KEY)
    return raw if isinstance(raw, dict) else {}


def _is_shot_child(fr: Frame) -> bool:
    meta = _subdivide_attr(fr)
    return int(meta.get("shot_index") or 1) > 1


def already_subdivided(frames: list[Frame]) -> bool:
    """True только если уже есть дочерние шоты (shot_index>1)."""
    return any(_is_shot_child(f) for f in frames)


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
    for fr in orphan:
        off = offsets.get(fr.uuid) if fr.uuid else None
        target = 0
        if off is not None:
            for i, (lo, hi) in enumerate(spans):
                if lo >= 0 and lo <= off:
                    target = i
        buckets[target].append(fr)
    for b in buckets:
        b.sort(key=lambda f: (f.sort_key is None, f.sort_key or 0.0, f.number or 0))
    return buckets


def camera_beat_for_frame(
    fr: Frame,
    shots: list[dict[str, Any]],
    spans: list[tuple[int, int]],
    offsets: dict[str, int | None],
) -> dict[str, Any] | None:
    if not fr.uuid or not shots:
        return None
    off = offsets.get(fr.uuid)
    if off is None:
        return shots[0] if shots else None
    for i, (lo, hi) in enumerate(spans):
        if lo < 0:
            continue
        if lo <= off < hi:
            return shots[i]
    best_i = 0
    for i, (lo, hi) in enumerate(spans):
        if lo >= 0 and lo <= off:
            best_i = i
    return shots[best_i]


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
    for n in range(min(6, len(words)), len(words) + 1):
        chunk = " ".join(words[-n:] if from_end else words[:n])
        cn = _norm(chunk)
        if cn and cn in vo_norm and cn not in used:
            used.add(cn)
            return chunk
    whole = " ".join(words)
    wn = _norm(whole)
    if wn and wn in vo_norm and wn not in used:
        used.add(wn)
        return whole
    vo_words = full_vo.split()
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


async def renumber_frames_by_sort_key(
    session: AsyncSession, project: Project
) -> list[Frame]:
    """number = 1..N по sort_key (insert даёт max+1 — ломает порядок)."""
    frames = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        ).scalars()
    )
    if not frames:
        return frames
    base = 10_000_000
    for i, fr in enumerate(frames):
        fr.number = base + i
    await session.flush()
    for i, fr in enumerate(frames, 1):
        fr.number = i
    await session.flush()
    return frames


async def subdivide_vo_frames_by_camera(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
    assembly_input: dict[str, Any],
    full_vo: str,
    *,
    set_counts: dict[str, tuple[int, int]] | None = None,
) -> tuple[list[Frame], dict[str, Any]]:
    """Вставить шоты SET на VO-родителя **без резки** ``voiceover_text``.

    Полный закадр и аудиометки остаются у родителя (shot_index=1).
    Дочерние Frame — только визуал: пустой закадр, доля duration, ladder step.
    """
    set_counts = set_counts if set_counts is not None else load_set_shot_counts()
    shots = [
        s
        for s in (assembly_input.get("shot_plan_chrono") or [])
        if isinstance(s, dict)
    ]
    report: dict[str, Any] = {
        "skipped": False,
        "parents": 0,
        "inserted": 0,
        "frames_before": len(frames),
        "frames_after": len(frames),
        "vo_text_untouched": True,
    }
    if already_subdivided(frames):
        report["skipped"] = True
        logger.info(
            "[#{}] camera_subdivide: already has shot children (frames={})",
            project.id,
            len(frames),
        )
        return frames, report
    if not shots:
        logger.warning("[#{}] camera_subdivide: нет shot_plan — skip", project.id)
        report["skipped"] = True
        return frames, report

    parents = [f for f in frames if f.uuid and not _is_shot_child(f)]
    parents.sort(key=lambda f: (f.sort_key is None, f.sort_key or 0.0, f.number or 0))
    offsets = frame_offsets(parents, full_vo)
    spans = _beat_vo_spans(shots, full_vo)

    inserted = 0
    for parent in reversed(parents):
        beat = camera_beat_for_frame(parent, shots, spans, offsets) or {}
        sec, _ = frame_seconds(parent)
        need = clamp_shots_to_duration(
            required_shots_for_beat(beat, set_counts, duration_sec=sec), sec
        )
        ladder = parse_krupnost_ladder(str(beat.get("крупность") or ""))
        if len(ladder) <= 1 and need >= 3:
            ladder = ["VLS", "MS", "CU"]
        elif len(ladder) <= 1 and need >= 2:
            ladder = ["MS", "CU"]
        while len(ladder) < need:
            ladder.append(ladder[-1] if ladder else "MS")
        ladder = ladder[:need]

        original_vo = parent.voiceover_text  # не трогаем содержимое
        start_ts = parent.start_ts
        end_ts = parent.end_ts
        total_sec = float(sec) if sec > 0 else float(need * _MIN_SEC_PER_SHOT)
        part_sec = round(total_sec / need, 2)
        nab = beat.get("набор")
        parent_uuid = parent.uuid

        if need <= 1:
            attrs = dict(parent.attrs or {})
            attrs[_ATTR_KEY] = {
                "role": "vo_parent",
                "parent_uuid": parent_uuid,
                "shot_index": 1,
                "shots_in_beat": 1,
                "набор": nab,
                "ladder": ladder,
                "крупность": ladder[0] if ladder else "",
            }
            parent.attrs = attrs
            continue

        children: list[Frame] = []
        after_id = parent.id
        for _ in range(need - 1):
            child = await insert_frame_after(
                session, project, after_frame_id=after_id
            )
            children.append(child)
            after_id = child.id
            inserted += 1

        group = [parent, *children]
        for i, fr in enumerate(group):
            if i == 0:
                fr.voiceover_text = original_vo
                fr.start_ts = start_ts
                fr.end_ts = end_ts
            else:
                fr.voiceover_text = ""
                fr.start_ts = None
                fr.end_ts = None
            fr.duration_seconds = part_sec
            attrs = dict(fr.attrs or {})
            attrs[_ATTR_KEY] = {
                "role": "vo_parent" if i == 0 else "shot",
                "parent_uuid": parent_uuid,
                "shot_index": i + 1,
                "shots_in_beat": need,
                "набор": nab,
                "ladder": ladder,
                "крупность": ladder[i],
            }
            fr.attrs = attrs

    await session.flush()
    ordered = await renumber_frames_by_sort_key(session, project)
    report["parents"] = len(parents)
    report["inserted"] = inserted
    report["frames_after"] = len(ordered)
    logger.info(
        "[#{}] camera_subdivide: parents={} inserted={} frames {}→{} (VO text on parent only)",
        project.id,
        report["parents"],
        inserted,
        report["frames_before"],
        report["frames_after"],
    )
    return ordered, report


def _group_frames_into_scenes(
    frames: list[Frame],
    shots: list[dict[str, Any]],
    full_vo: str,
    set_counts: dict[str, tuple[int, int]],
) -> list[tuple[list[Frame], dict[str, Any], int]]:
    """Группы кадров → (frames, camera_beat, shots_required).

    После subdivide группа = все shot с одним parent_uuid.
    Иначе — один Frame = одна сцена (камера только подсказка).
    """
    by_parent: dict[str, list[Frame]] = {}
    singles: list[Frame] = []
    for fr in frames:
        if not fr.uuid:
            continue
        meta = _subdivide_attr(fr)
        parent_u = str(meta.get("parent_uuid") or "").strip()
        if parent_u:
            by_parent.setdefault(parent_u, []).append(fr)
        else:
            singles.append(fr)

    offsets = frame_offsets(frames, full_vo)
    spans = _beat_vo_spans(shots, full_vo) if shots else []

    groups: list[tuple[list[Frame], dict[str, Any], int]] = []

    def _sort_key(f: Frame) -> tuple:
        return (f.sort_key is None, f.sort_key or 0.0, f.number or 0)

    # Родители в порядке появления.
    parent_order: list[str] = []
    seen_p: set[str] = set()
    for fr in sorted(frames, key=_sort_key):
        meta = _subdivide_attr(fr)
        pu = str(meta.get("parent_uuid") or "").strip()
        if pu and pu not in seen_p:
            seen_p.add(pu)
            parent_order.append(pu)

    for pu in parent_order:
        frs = sorted(by_parent.get(pu) or [], key=_sort_key)
        if not frs:
            continue
        lead = next(
            (f for f in frs if int(_subdivide_attr(f).get("shot_index") or 1) == 1),
            frs[0],
        )
        beat = camera_beat_for_frame(lead, shots, spans, offsets) or {}
        meta = _subdivide_attr(lead)
        req = int(meta.get("shots_in_beat") or len(frs) or 1)
        if not req:
            req = required_shots_for_beat(beat, set_counts)
        groups.append((frs, beat, req))

    for fr in sorted(singles, key=_sort_key):
        beat = camera_beat_for_frame(fr, shots, spans, offsets) or {}
        req = required_shots_for_beat(beat, set_counts) if beat else 1
        groups.append(([fr], beat, req))

    # Если групп нет — fallback: каждый кадр = сцена.
    if not groups:
        for fr in sorted(frames, key=_sort_key):
            if fr.uuid:
                groups.append(([fr], {}, 1))
    return groups


def rebuild_scenes_from_camera(
    frames: list[Frame],
    assembly_input: dict[str, Any],
    full_vo: str,
    *,
    set_counts: dict[str, tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Черновик сцен: группа шотов одного VO-родителя = сцена (иногда две).

    Текст цитат берём из полного закадра родителя (дети без VO). Сцена может
    содержать много кадров; тайминг = сумма duration шотов группы.
    """
    set_counts = set_counts if set_counts is not None else load_set_shot_counts()
    shots = [
        s
        for s in (assembly_input.get("shot_plan_chrono") or [])
        if isinstance(s, dict)
    ]
    if not shots and not already_subdivided(frames):
        logger.warning("camera_expand: нет shot_plan — scenes_chrono не трогаем")
        return assembly_input

    groups = _group_frames_into_scenes(frames, shots, full_vo, set_counts)
    expanded = expand_shot_plan_rows(shots, set_counts) if shots else []

    # uuid → полный VO родителя (у детей закадр пустой).
    vo_by_uuid = {
        str(f.uuid): (f.voiceover_text or "").strip()
        for f in frames
        if f.uuid and (f.voiceover_text or "").strip()
    }

    scenes: list[dict[str, Any]] = []
    used_quotes: set[str] = set()
    for frs, beat, _req in groups:
        if not frs:
            continue
        meta0 = _subdivide_attr(frs[0])
        parent_u = str(meta0.get("parent_uuid") or frs[0].uuid or "").strip()
        parent_vo = vo_by_uuid.get(parent_u, "")
        if not parent_vo:
            parent_vo = next(
                (
                    (f.voiceover_text or "").strip()
                    for f in frs
                    if (f.voiceover_text or "").strip()
                ),
                str(beat.get("цитата") or ""),
            )
        ladder = meta0.get("ladder") or parse_krupnost_ladder(
            str(beat.get("крупность") or "")
        )
        kids_all = [_frame_row(f) for f in frs]
        total_sec = sum(float(k["время_сек"]) for k in kids_all)
        # Длинный диапазон + ≥4 шота → две сцены внутри одного VO-тайминга.
        chunks: list[list[Frame]] = [frs]
        if len(frs) >= 4 and total_sec >= 12.0:
            mid = len(frs) // 2
            chunks = [frs[:mid], frs[mid:]]

        for chunk in chunks:
            if not chunk:
                continue
            c_kids = [_frame_row(f) for f in chunk]
            c_vo = parent_vo
            c_sw = _pick_unique_quote(
                c_vo or str(beat.get("цитата") or ""),
                full_vo,
                used_quotes,
                from_end=False,
            )
            c_ew = _pick_unique_quote(
                c_vo or c_sw, full_vo, used_quotes, from_end=True
            )
            sid = len(scenes) + 1
            scenes.append(
                {
                    "id_scene": f"scene_{sid:02d}",
                    "start_words": c_sw,
                    "end_words": c_ew or c_sw,
                    "время_сек": round(sum(float(k["время_сек"]) for k in c_kids), 1),
                    "кадры": c_kids,
                    "кадров": len(c_kids),
                    "набор": beat.get("набор") or meta0.get("набор"),
                    "camera_shots_required": max(1, len(chunk)),
                    "camera_ladder": ladder,
                    "мотив": beat.get("мотив") or "",
                    "структура_сцены": "continuity",
                    "тип_стыка": beat.get("тип_стыка") or "action",
                    "переход_в_сцену": beat.get("переход") or "cut",
                    "цепь_действия": [],
                    "смысл_сцены": str(beat.get("мотив") or "")[:200],
                }
            )

    out = copy.deepcopy(assembly_input)
    out["scenes_chrono"] = scenes
    out["shot_plan_chrono"] = expanded
    out["shot_plan_beats"] = shots
    out["camera_expand_report"] = {
        "beats_in": len(assembly_input.get("shot_plan_chrono") or []),
        "scenes": len(scenes),
        "expanded_shots": len(expanded),
        "frames": len(frames),
        "single_frame_scenes": sum(1 for s in scenes if s["кадров"] < 2),
        "multi_frame_scenes": sum(1 for s in scenes if s["кадров"] >= 2),
        "need_ge2_but_got1": sum(
            1
            for s in scenes
            if int(s.get("camera_shots_required") or 1) >= 2 and s["кадров"] < 2
        ),
    }
    logger.info(
        "camera_expand: scenes={} frames={} multi={} single={} stuck1={}",
        len(scenes),
        len(frames),
        out["camera_expand_report"]["multi_frame_scenes"],
        out["camera_expand_report"]["single_frame_scenes"],
        out["camera_expand_report"]["need_ge2_but_got1"],
    )
    return out
