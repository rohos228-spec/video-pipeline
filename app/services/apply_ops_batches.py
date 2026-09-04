"""Apply-ops батчи: длинный db_frames.json режется, чтобы SSE не обрывался.

Эмпирика #6 n_excel_gpt_2: 116 плотных shot_01 в одном ответе →
stream_partial, salvage ops=65, нода done. Аналитика (короткие поля)
на тех же 116 кадрах проходит целиком.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.gpt_operator_client import OperatorApiResult, run_operator_api
from app.services.scene_design.camera_expand import vo_chunk_is_dangling
from app.services.shot_templates import coverage_template_reason, fill_kadry_from_catalog

# Плотный выход (shot_01 + главное_действие): 160 кадров одним ответом
# рвёт SSE и подмешивает фейковые uuid. Режем на 5 пачек (~32 кадра).
_DENSE_TARGET_BATCHES = 5
_DENSE_FRAMES_PER_BATCH = 8
_DENSE_JSON_BYTES_PER_BATCH = 20_000
# Короткий выход (аналитика 54–59): 116 кадров / ~44k символов — ок.
_LIGHT_FRAMES_PER_BATCH = 120
_LIGHT_JSON_BYTES_PER_BATCH = 180_000
# Сценарист / закадр (прочие ноды): пачка = 6 ячеек VO.
VO_UNITS_PER_BATCH = 6
# Группа script_frames_qc (биты → действие → кадры → промты → QC):
# биты/действие/кадры — по 8 отрезков, до 6 пачек параллельно, сдвиг 0.5 с.
# fw_frames — по 6 кадров (тяжёлые image+anim). Из main: соло-пачка
# жирного K1, автопочинка UUID, не резать пачку на socket salvage.
SCRIPT_FRAMES_QC_UNITS_PER_BATCH = 8
SCRIPT_FRAMES_QC_PARALLEL_BATCHES = 6
FW_FRAMES_PER_BATCH = 6
VO_PARALLEL_MAX = 6
FW_FRAMES_PARALLEL_BATCHES = 6
VO_STAGGER_SEC = 0.5
SHOT_VO_MIN_CHARS = 27
SHOT_VO_MAX_CHARS = 54

_COMPLETE_ATTRS_DENSE = ("main_action", "shot01_description")
_IMG_SKIP_KEYS = ("image_prompt", "промт_картинки")
_ANIM_SKIP_KEYS = ("animation_prompt", "промт_видео")
_ACTION_SKIP_KEYS = ("shot01_action", "main_action", "действие")
# fw_frames: кадр готов только если картинка + видео + действие.
SKIP_PROMPTS_AND_ACTION = "prompts_and_action"
# Добор меню съёмки: крупность + движение + набор (по 6).
SKIP_CAMERA_MENU = "camera_menu"
CAMERA_MENU_UNITS_PER_BATCH = 6
# Зависший SSE не должен держать всю волну 10 мин (GPT_TIMEOUT_S=600).
_PROMPT_PACK_TIMEOUT_S = 180.0
_SIZE_SKIP_KEYS = ("крупность", "size", "shot_size")
_MOVE_SKIP_KEYS = ("движение", "движение_камеры", "move", "shot_move")
_SET_SKIP_KEYS = ("набор", "set", "shot_set")

ApplyFn = Callable[[dict[str, Any]], Awaitable[None]]
ProgressFn = Callable[[str], Awaitable[None]]


def frames_per_batch(
    *,
    n_frames: int,
    json_bytes: int,
    dense: bool,
    skip_if_field: str | None = None,
    target_batches: int | None = None,
) -> int:
    """Сколько кадров в одном GPT-вызове. 0 кадров → 1 (не делить на ноль)."""
    n = max(int(n_frames), 0)
    if n <= 0:
        return 1
    if target_batches and int(target_batches) > 0:
        from math import ceil

        # Добор хвоста (≤32 кадра) — один вызов, не 5 крошечных пачек.
        if dense and n <= 32:
            return n
        return max(1, int(ceil(n / int(target_batches))))
    avg = max(int(json_bytes), 0) / n if n else 0
    if skip_if_field:
        return n
    if dense:
        by_count = _DENSE_FRAMES_PER_BATCH
        if avg > 0:
            by_bytes = max(8, int(_DENSE_JSON_BYTES_PER_BATCH / avg))
            return max(8, min(by_count, by_bytes, n))
        return min(by_count, n)
    by_count = _LIGHT_FRAMES_PER_BATCH
    if avg > 0:
        by_bytes = max(20, int(_LIGHT_JSON_BYTES_PER_BATCH / avg))
        return max(20, min(by_count, by_bytes, n))
    return min(by_count, n)


def should_batch_apply_ops(
    *,
    n_frames: int,
    json_bytes: int,
    dense: bool,
    skip_if_field: str | None = None,
    target_batches: int | None = None,
) -> bool:
    return n_frames > frames_per_batch(
        n_frames=n_frames,
        json_bytes=json_bytes,
        dense=dense,
        skip_if_field=skip_if_field,
        target_batches=target_batches,
    )


def split_frames(frames: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        size = 1
    if not frames:
        return []
    # Если первый кадр супер-жирный (сценарий / биты > 5000 символов) — выделяем его в соло-пачку
    first_fr = frames[0]
    first_heavy = False
    if isinstance(first_fr, dict):
        first_len = len(str(first_fr.get("voiceover_text") or first_fr.get("закадр") or "")) + len(str(first_fr))
        if first_len >= 5000:
            first_heavy = True
    if first_heavy:
        rest = frames[1:]
        packs = [[first_fr]]
        if rest:
            packs.extend([rest[i : i + size] for i in range(0, len(rest), size)])
        return packs
    return [frames[i : i + size] for i in range(0, len(frames), size)]


def _vo_unit_key(frame: dict[str, Any]) -> str:
    """Ячейка закадра: parent_uuid шота или свой uuid."""
    uid = str(frame.get("uuid") or "").strip()
    cs = frame.get("camera_subdivide")
    if not isinstance(cs, dict):
        attrs = frame.get("attrs")
        cs = attrs.get("camera_subdivide") if isinstance(attrs, dict) else {}
    if not isinstance(cs, dict):
        cs = {}
    parent = str(cs.get("parent_uuid") or "").strip()
    return parent or uid


def group_vo_units(frames: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Кадры одной VO-ячейки (родитель + шоты) держатся вместе."""
    order: list[str] = []
    buckets: dict[str, list[dict[str, Any]]] = {}
    for fr in frames:
        key = _vo_unit_key(fr)
        if not key:
            continue
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(fr)
    return [buckets[k] for k in order]


def split_vo_units(
    frames: list[dict[str, Any]], unit_size: int
) -> list[list[dict[str, Any]]]:
    """Пачки по N отрезков закадра, не по числу шотов."""
    if unit_size <= 0:
        unit_size = 1
    units = group_vo_units(frames)
    if not units:
        return [list(frames)]
    packs: list[list[dict[str, Any]]] = []
    # Если первый юнит супер-жирный (> 5000 символов) — выделяем в отдельную соло-пачку
    start_idx = 0
    first_unit = units[0]
    first_len = sum(
        len(str(f.get("voiceover_text") or f.get("закадр") or "")) + len(str(f))
        for f in first_unit
    )
    if first_len >= 5000:
        packs.append(first_unit)
        start_idx = 1
    for i in range(start_idx, len(units), unit_size):
        pack: list[dict[str, Any]] = []
        for unit in units[i : i + unit_size]:
            pack.extend(unit)
        if pack:
            packs.append(pack)
    return packs or [list(frames)]


def split_into_n_packs(frames: list[dict[str, Any]], n: int) -> list[list[dict[str, Any]]]:
    """Ровно n пачек. VO-ячейка (родитель+шоты) не режется пополам."""
    if not frames:
        return []
    n = max(1, int(n))
    units = group_vo_units(frames)
    if len(units) > 1:
        n = min(n, len(units))
        per = max(1, (len(units) + n - 1) // n)
        return split_vo_units(frames, per)
    n = min(n, len(frames))
    per = max(1, (len(frames) + n - 1) // n)
    return split_frames(frames, per)


def _any_field(frame: dict[str, Any], attrs: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(
        str(frame.get(k) or attrs.get(k) or "").strip() for k in keys
    )


def _camera_menu_attrs(frame: dict[str, Any], attrs: dict[str, Any]) -> dict[str, Any]:
    cs = attrs.get("camera_subdivide") if isinstance(attrs, dict) else {}
    if not isinstance(cs, dict):
        cs = {}
    extra = frame.get("camera_subdivide")
    if isinstance(extra, dict):
        cs = {**cs, **extra}
    return {**attrs, **cs}


def _frame_complete(
    frame: dict[str, Any],
    *,
    dense: bool,
    skip_if_field: str | None = None,
) -> bool:
    attrs = frame.get("attrs") if isinstance(frame.get("attrs"), dict) else {}
    if skip_if_field == SKIP_PROMPTS_AND_ACTION:
        return (
            _any_field(frame, attrs, _IMG_SKIP_KEYS)
            and _any_field(frame, attrs, _ANIM_SKIP_KEYS)
            and _any_field(frame, attrs, _ACTION_SKIP_KEYS)
        )
    if skip_if_field == SKIP_CAMERA_MENU:
        blob = _camera_menu_attrs(frame, attrs)
        return (
            _any_field(frame, blob, _SIZE_SKIP_KEYS)
            and _any_field(frame, blob, _MOVE_SKIP_KEYS)
            and _any_field(frame, blob, _SET_SKIP_KEYS)
        )
    if skip_if_field:
        keys = (skip_if_field, *_IMG_SKIP_KEYS)
        for k in keys:
            if str(frame.get(k) or "").strip():
                return True
            if str(attrs.get(k) or "").strip():
                return True
        return False
    if not dense:
        return False
    # db_frames.json кладёт whitelist на верхний уровень, не в attrs.
    return all(
        str(frame.get(k) or attrs.get(k) or "").strip()
        for k in _COMPLETE_ATTRS_DENSE
    )


def _has_uuid(frame: dict[str, Any]) -> bool:
    return bool(str(frame.get("uuid") or "").strip())


def _has_voiceover(frame: dict[str, Any]) -> bool:
    if str(frame.get("voiceover_text") or "").strip():
        return True
    if str(frame.get("vo_shot") or frame.get("закадр_шота") or "").strip():
        return True
    attrs = frame.get("attrs")
    if isinstance(attrs, dict):
        if str(attrs.get("vo_cell_full") or "").strip():
            return True
        cs = attrs.get("camera_subdivide")
        if isinstance(cs, dict) and str(cs.get("vo_shot") or "").strip():
            return True
    blob = _camera_menu_attrs(frame, attrs if isinstance(attrs, dict) else {})
    if str(blob.get("vo_shot") or "").strip():
        return True
    return False


def _is_coverage_shot(frame: dict[str, Any]) -> bool:
    """Визуальный шот лестницы — даже без своего куска закадра."""
    attrs = frame.get("attrs") if isinstance(frame.get("attrs"), dict) else {}
    blob = _camera_menu_attrs(frame, attrs)
    role = str(blob.get("role") or "").strip()
    if role in ("shot", "vo_parent"):
        return True
    if str(blob.get("parent_uuid") or "").strip():
        return True
    if str(blob.get("shot_id") or "").strip():
        return True
    return False


def _pending_frames(
    frames: list[dict[str, Any]],
    *,
    dense: bool,
    skip_if_field: str | None = None,
    force_full: bool = False,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fr in frames:
        if not _has_uuid(fr):
            continue
        # Меню съёмки и промты нужны и молчаливому покрытию (K3/K4 без vo_shot).
        if (
            skip_if_field != SKIP_CAMERA_MENU
            and not _has_voiceover(fr)
            and not _is_coverage_shot(fr)
        ):
            continue
        if (not force_full) and _frame_complete(
            fr, dense=dense, skip_if_field=skip_if_field
        ):
            continue
        out.append(fr)
    return out


def select_frames_for_batches(
    frames: list[dict[str, Any]],
    *,
    dense: bool,
    skip_if_field: str | None = None,
    target_batches: int | None = None,
    force_full: bool = False,
) -> list[dict[str, Any]]:
    """Кадры в GPT-пачки. force_full (ручной ▶) — все, без skip filled."""
    del target_batches
    return _pending_frames(
        frames,
        dense=dense,
        skip_if_field=skip_if_field,
        force_full=force_full,
    )


def _norm_analytics_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _op_field(fields: dict[str, Any], *names: str) -> str:
    for name in names:
        raw = fields.get(name)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return ""


def analytics_ops_collapsed_reason(
    ops: list[Any],
    frames: list[dict[str, Any]],
) -> str | None:
    """Разный закадр не может делить смысл или место. Только особенность = брак."""
    vo_by_uuid: dict[str, str] = {}
    for fr in frames:
        uid = str(fr.get("uuid") or "").strip()
        if uid:
            vo_by_uuid[uid] = _norm_analytics_text(fr.get("voiceover_text"))
    sense_owner: dict[str, tuple[str, str]] = {}
    place_owner: dict[str, tuple[str, str]] = {}
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        uid = str(op.get("frame_uuid") or "").strip()
        vo = vo_by_uuid.get(uid, "")
        sense = _norm_analytics_text(
            _op_field(fields, "смысл_сцены", "scene_sense")
        )
        place = _norm_analytics_text(_op_field(fields, "место", "place"))
        if sense:
            prev = sense_owner.get(sense)
            if prev and prev[1] != vo:
                return (
                    f"скопирован смысл_сцены у uuid {uid[:8]} "
                    f"(тот же текст, что у {prev[0][:8]}, закадр другой)"
                )
            sense_owner[sense] = (uid, vo)
        if place:
            prev = place_owner.get(place)
            if prev and prev[1] != vo:
                return (
                    f"скопировано место у uuid {uid[:8]} "
                    f"(то же, что у {prev[0][:8]}, закадр другой)"
                )
            place_owner[place] = (uid, vo)
    return None


_SCENE_NUM_RE = re.compile(r"(?m)^\s*\d+\.\s+\S")
_VO_CLAUSE_RE = re.compile(r"[.!?…]+|\n+")
_PLACE_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _op_shots(fields: dict[str, Any]) -> list[dict[str, Any]]:
    raw = fields.get("кадры") or fields.get("shots")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _op_bits(fields: dict[str, Any]) -> Any:
    if "биты" in fields:
        return fields["биты"]
    return fields.get("bits")


def _vo_clauses(vo: str) -> list[str]:
    return [p.strip() for p in _VO_CLAUSE_RE.split(vo or "") if p.strip()]


def _frames_by_uuid(frames: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        uid = str(fr.get("uuid") or "").strip()
        if uid:
            out[uid] = fr
    return out


def _frame_vo(fr: dict[str, Any] | None) -> str:
    if not fr:
        return ""
    vo = str(fr.get("voiceover_text") or fr.get("закадр") or "").strip()
    if not vo:
        attrs = fr.get("attrs")
        if isinstance(attrs, dict):
            vo = str(attrs.get("закадр") or attrs.get("voiceover_text") or "").strip()
    return vo


def _frame_bits_list(fr: dict[str, Any] | None) -> list[Any]:
    if not fr:
        return []
    raw = fr.get("биты")
    if raw is None:
        attrs = fr.get("attrs")
        if isinstance(attrs, dict):
            raw = attrs.get("биты")
    if isinstance(raw, str) and raw.strip().startswith("["):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return raw if isinstance(raw, list) else []


def _shot_independent(shot: dict[str, Any]) -> bool:
    return shot.get("parent_id") in (None, "", "null")


def repair_same_place_shot_parents(ops: list[Any]) -> int:
    """Одно место: первый кадр master, остальные parent_id = его id.

    GPT часто ставит всем parent_id=null — валидатор валит весь шаг.
    Правило таблицы: дети одного сетапа не все самостоятельные.
    """
    fixed = 0
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        shots = _op_shots(fields)
        if len(shots) < 2:
            continue
        by_place: dict[str, list[dict[str, Any]]] = {}
        for shot in shots:
            place = str(shot.get("место") or shot.get("place") or "").strip().casefold()
            if place:
                by_place.setdefault(place, []).append(shot)
        for group in by_place.values():
            if len(group) < 2:
                continue
            master_id = str(group[0].get("id") or "").strip()
            if not master_id:
                continue
            for shot in group[1:]:
                if _shot_independent(shot):
                    shot["parent_id"] = master_id
                    fixed += 1
        if "кадры" in fields:
            fields["кадры"] = shots
        elif "shots" in fields:
            fields["shots"] = shots
    return fixed


def fill_kadry_ops_from_catalog(
    ops: list[Any], frames: list[dict[str, Any]]
) -> int:
    """Дописать fields.кадры до лестницы каталога (полиция T6→T3→T1→T5)."""
    by_uid = {
        str(fr.get("uuid") or "").strip(): fr
        for fr in (frames or [])
        if isinstance(fr, dict) and str(fr.get("uuid") or "").strip()
    }
    added = 0
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        shots = fields.get("кадры") or fields.get("shots")
        if not isinstance(shots, list):
            continue
        uid = str(op.get("frame_uuid") or "").strip()
        fr = by_uid.get(uid) or {}
        attrs = fr.get("attrs") if isinstance(fr.get("attrs"), dict) else {}
        action = str(
            fr.get("главное_действие")
            or fr.get("main_action")
            or attrs.get("главное_действие")
            or attrs.get("main_action")
            or ""
        ).strip()
        try:
            cell_n = int(fr.get("number") or 1)
        except (TypeError, ValueError):
            cell_n = 1
        filled = fill_kadry_from_catalog(shots, action, cell_number=cell_n)
        added += max(0, len(filled) - len(shots))
        fields["кадры"] = filled
        if "shots" in fields:
            fields["shots"] = filled
    return added


def _vo_visible_len(text: str) -> int:
    """Длина закадра без комбинирующих ударений (символы как в речи)."""
    return len(
        "".join(c for c in (text or "") if unicodedata.category(c) != "Mn")
    )


def _shot_vo_chunk(shot: dict[str, Any]) -> str:
    return str(shot.get("закадр") or shot.get("voiceover_text") or "").strip()


def _is_special_short_vo(chunk: str, plan: str) -> bool:
    """1–2 слова отдельным кадром только как титр/имя/ударная деталь."""
    compact = re.sub(r"\s+", " ", chunk or "").strip()
    if not compact:
        return False
    if re.match(r"^(и|или|а|но|да|—|–|-)\b", compact.casefold()):
        return False
    words = re.findall(r"[^\W\d_]+", compact, flags=re.UNICODE)
    if not (1 <= len(words) <= 2):
        return False
    has_quote = any(q in compact for q in ("«", "»", "“", "”"))
    is_name = all(w[:1].isupper() for w in words if w)
    return bool(has_quote or is_name)


def _shot_vo_len_reason(
    shots: list[dict[str, Any]],
    cell_vo: str,
    uid: str,
) -> str | None:
    for shot in shots:
        chunk = _shot_vo_chunk(shot)
        sid = str(shot.get("id") or "")
        if not chunk:
            return (
                f"uuid {uid[:8]}: кадр {sid or '?'} пустой закадр "
                "— у каждого кадра свой кусок"
            )
    cell_n = _vo_visible_len(cell_vo)
    # Если исходный закадр короткий — длина шотов не может быть больше исходного текста
    if cell_n <= SHOT_VO_MIN_CHARS:
        return None
    for shot in shots:
        chunk = _shot_vo_chunk(shot)
        sid = str(shot.get("id") or "")
        plan = str(shot.get("план") or "")
        if vo_chunk_is_dangling(chunk):
            return (
                f"uuid {uid[:8]}: кадр {sid or '?'} обрубок закадра "
                f"({chunk[-24:]!r})"
            )
        words = re.findall(r"[^\W\d_]+", chunk, flags=re.UNICODE)
        if 1 <= len(words) <= 2 and not _is_special_short_vo(chunk, plan):
            n = _vo_visible_len(chunk)
            return (
                f"uuid {uid[:8]}: кадр {sid or '?'} закадр {n} симв. "
                "(1–2 слова — только титр/имя/ударная деталь)"
            )
        n = _vo_visible_len(chunk)
        if n < SHOT_VO_MIN_CHARS:
            if len(shots) == 1 and cell_n <= SHOT_VO_MAX_CHARS:
                continue
            if _is_special_short_vo(chunk, plan):
                continue
            if n >= 15:
                continue
            return (
                f"uuid {uid[:8]}: кадр {sid or '?'} закадр {n} симв. "
                "(1–2 слова — только титр/имя/ударная деталь)"
            )
    return None


def _place_attested_in_vo(place: str, vo: str) -> bool:
    """Место из кадра должно читаться в закадре, иначе это выдуманный сетап."""
    vo_cf = (vo or "").casefold()
    place_cf = (place or "").strip().casefold()
    if not place_cf or not vo_cf:
        return False
    if place_cf in vo_cf:
        return True
    tokens = [
        t.casefold()
        for t in _PLACE_TOKEN_RE.findall(place)
        if len(t) >= 4
    ]
    for t in tokens:
        if t in vo_cf:
            return True
        if len(t) >= 4 and t[:4] in vo_cf:
            return True
    return False


def _snap_anchor_to_vo(anchor: str, vo: str) -> str:
    """Якорь должен читаться в закадре. Кривой якорь — подтянуть к фразе VO."""
    vo = (vo or "").strip()
    anchor = (anchor or "").strip()
    if not vo:
        return anchor
    if not anchor:
        clauses = _vo_clauses(vo)
        return clauses[0] if clauses else vo[:80]
    vo_cf = vo.casefold()
    a_cf = anchor.casefold()
    idx = vo_cf.find(a_cf)
    if idx >= 0:
        return vo[idx : idx + len(anchor)]
    clauses = _vo_clauses(vo)
    a_words = set(re.findall(r"[^\W\d_]+", a_cf, flags=re.UNICODE))
    best = ""
    best_n = 0
    for clause in clauses:
        c_words = set(
            re.findall(r"[^\W\d_]+", clause.casefold(), flags=re.UNICODE)
        )
        n = len(a_words & c_words)
        if n > best_n:
            best_n = n
            best = clause
    if best:
        return best
    return clauses[0] if clauses else vo[:80]


def repair_bits_ops(ops: list[Any], frames: list[dict[str, Any]]) -> int:
    """Починить биты на месте: слоган → объект, якорь → кусок закадра."""
    by_uid = _frames_by_uuid(frames)
    fixed = 0
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        uid = str(op.get("frame_uuid") or "").strip()
        vo = _frame_vo(by_uid.get(uid))
        raw = _op_bits(fields)
        if isinstance(raw, str):
            raw = [
                {
                    "порядок": 1,
                    "глагол": "говорит",
                    "изменение": "было → стало",
                    "якорь": _snap_anchor_to_vo(raw, vo),
                }
            ]
            fields["биты"] = raw
            fixed += 1
        if not isinstance(raw, list):
            continue
        for i, bit in enumerate(raw):
            if not isinstance(bit, dict):
                raw[i] = {
                    "порядок": i + 1,
                    "глагол": "говорит",
                    "изменение": "было → стало",
                    "якорь": _snap_anchor_to_vo(str(bit), vo),
                }
                fixed += 1
                continue
            if not str(bit.get("глагол") or "").strip():
                bit["глагол"] = "говорит"
                fixed += 1
            if not str(bit.get("изменение") or "").strip():
                bit["изменение"] = "было → стало"
                fixed += 1
            snapped = _snap_anchor_to_vo(str(bit.get("якорь") or ""), vo)
            if snapped and snapped != str(bit.get("якорь") or "").strip():
                bit["якорь"] = snapped
                fixed += 1
            elif not str(bit.get("якорь") or "").strip() and snapped:
                bit["якорь"] = snapped
                fixed += 1
        fields["биты"] = raw
    return fixed


def repair_action_ops(ops: list[Any]) -> int:
    """Нет «1. место — действие» — обернуть, чтобы нода не падала."""
    fixed = 0
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        text = _op_field(fields, "главное_действие", "main_action")
        if not text:
            continue
        changed = text
        if not _SCENE_NUM_RE.search(changed):
            changed = f"1. сцена — {changed}"
        if "(" not in changed:
            snippet = _vo_clauses(text)
            tail = snippet[0] if snippet else text[:120]
            changed = f"{changed}\n({tail})"
        if changed != text:
            if "главное_действие" in fields:
                fields["главное_действие"] = changed
            else:
                fields["main_action"] = changed
            fixed += 1
    return fixed


def repair_shot_vo_ops(ops: list[Any], frames: list[dict[str, Any]]) -> int:
    """Пустой закадр кадра — дописать остаток ячейки. Не валить ноду."""
    by_uid = _frames_by_uuid(frames)
    fixed = 0
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        shots = fields.get("кадры") or fields.get("shots")
        if not isinstance(shots, list):
            continue
        uid = str(op.get("frame_uuid") or "").strip()
        cell_vo = _frame_vo(by_uid.get(uid))
        taken = " ".join(
            _shot_vo_chunk(s) for s in shots if isinstance(s, dict) and _shot_vo_chunk(s)
        )
        leftover = cell_vo
        if taken and cell_vo:
            idx = cell_vo.find(taken[:24]) if taken else -1
            if idx >= 0:
                leftover = cell_vo[idx + len(taken) :].strip() or cell_vo
            else:
                leftover = cell_vo
        if not leftover:
            leftover = taken or cell_vo or "…"
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            if _shot_vo_chunk(shot):
                continue
            shot["закадр"] = leftover
            fixed += 1
        if "кадры" in fields:
            fields["кадры"] = shots
        elif "shots" in fields:
            fields["shots"] = shots
    return fixed


def bits_ops_reason(
    ops: list[Any],
    frames: list[dict[str, Any]],
) -> str | None:
    """Биты — массив объектов по числу изменений, не строка и не единица на ячейку."""
    by_uid = _frames_by_uuid(frames)
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        uid = str(op.get("frame_uuid") or "").strip()
        raw = _op_bits(fields)
        vo = _frame_vo(by_uid.get(uid))
        if raw is None:
            return f"uuid {uid[:8]}: нет поля биты"
        if isinstance(raw, str):
            return (
                f"uuid {uid[:8]}: биты — строка, нужен массив объектов "
                "(не слоган на всю ячейку)"
            )
        if not isinstance(raw, list):
            return f"uuid {uid[:8]}: биты не массив"
        if not vo:
            if raw:
                return f"uuid {uid[:8]}: пустой закадр, биты должны быть []"
            continue
        if not raw:
            return f"uuid {uid[:8]}: пустые биты у непустой ячейки"
        vo_cf = vo.casefold()
        for i, bit in enumerate(raw, start=1):
            if not isinstance(bit, dict):
                return f"uuid {uid[:8]}: бит {i} не объект"
            if not str(bit.get("глагол") or "").strip():
                return f"uuid {uid[:8]}: бит {i} без глагола"
            if not str(bit.get("изменение") or "").strip():
                return f"uuid {uid[:8]}: бит {i} без изменения"
            anchor = str(bit.get("якорь") or "").strip()
            if not anchor:
                # Автоисправление: подставить ключевое слово из глагола или закадра
                verb = str(bit.get("глагол") or "").strip()
                if verb and verb.casefold() in vo_cf:
                    bit["якорь"] = verb
                else:
                    words = [w for w in re.findall(r"[^\W\d_]+", vo, flags=re.UNICODE) if len(w) >= 3]
                    bit["якорь"] = words[0] if words else vo[:15].strip()
            anchor_cf = str(bit.get("якорь") or "").strip().casefold()
            if anchor_cf and anchor_cf not in vo_cf:
                # Проверим отдельные слова из якоря
                anchor_words = [w for w in re.findall(r"[^\W\d_]+", anchor_cf, flags=re.UNICODE) if len(w) >= 3]
                if not any(w in vo_cf for w in anchor_words):
                    first_word = next((w for w in re.findall(r"[^\W\d_]+", vo_cf, flags=re.UNICODE) if len(w) >= 3), None)
                    if first_word:
                        bit["якорь"] = first_word
                    else:
                        return f"uuid {uid[:8]}: якорь бита {i} не из закадра"
        clauses = _vo_clauses(vo)
        if len(clauses) >= 2 and len(raw) < 2 and len(vo) >= 54:
            return (
                f"uuid {uid[:8]}: {len(clauses)} частей закадра, "
                f"бит {len(raw)} — число битов = число изменений, не 1"
            )
    return None


def auto_repair_action_chain_ops(
    ops: list[Any],
    frames: list[dict[str, Any]],
) -> None:
    """Мягкая автопочинка перед валидацией: если модель забыла «1. » или скобки вокруг закадра."""
    by_uid = _frames_by_uuid(frames)
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        uid = str(op.get("frame_uuid") or "").strip()
        text = _op_field(fields, "главное_действие", "main_action")
        if not text:
            continue
        # Если есть «место — действие», но нет «1. »
        if "—" in text and not _SCENE_NUM_RE.search(text):
            text = f"1. {text}"
            if "главное_действие" in fields:
                fields["главное_действие"] = text
            elif "main_action" in fields:
                fields["main_action"] = text
        # Если нет скобок с закадром «(...)»
        if "(" not in text:
            fr_vo = _frame_vo(by_uid.get(uid))
            if fr_vo:
                text = f"{text} ({fr_vo[:40].strip()})"
                if "главное_действие" in fields:
                    fields["главное_действие"] = text
                elif "main_action" in fields:
                    fields["main_action"] = text


def action_chain_ops_reason(
    ops: list[Any],
    frames: list[dict[str, Any]],
) -> str | None:
    """Главное действие — нумерованная цепь, не слоган."""
    del frames
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        uid = str(op.get("frame_uuid") or "").strip()
        text = _op_field(fields, "главное_действие", "main_action")
        if not text:
            return f"uuid {uid[:8]}: пустое главное_действие"
        if not _SCENE_NUM_RE.search(text):
            return (
                f"uuid {uid[:8]}: главное_действие не цепь "
                "(нет «1. место — действие»)"
            )
        if "(" not in text:
            return f"uuid {uid[:8]}: нет (куска закадра) под сценой"
    return None


def _same_place_plan_ladder_reason(
    shots: list[dict[str, Any]],
    uid: str,
) -> str | None:
    """Одно место — лестница планов (Excel: «нельзя все ОБЩИЙ/фронт»).

    Два СРЕДНИХ с плеча в диалоге (T1-c2) — норма, не брак. Брак: всё
    покрытие места в одном плане — все ОБЩИЕ (любое число) или 3+ кадров
    одного плана без лестницы. T8 не покрывает одно место дважды.
    """
    by_place: dict[str, list[dict[str, Any]]] = {}
    t8_places: list[str] = []
    for shot in shots:
        place = str(shot.get("место") or shot.get("place") or "").strip().casefold()
        tid = str(shot.get("шаблон") or shot.get("template") or "").strip().upper()
        if tid.startswith("T8") and place:
            t8_places.append(place)
        if place:
            by_place.setdefault(place, []).append(shot)
    if t8_places and len(t8_places) != len(set(t8_places)):
        return (
            f"uuid {uid[:8]}: T8 дважды на одно место — монтаж разных "
            "миров, не покрытие одной сцены"
        )
    for place, group in by_place.items():
        if len(group) < 2:
            continue
        plans = {
            str(s.get("план") or "").strip().casefold()
            for s in group
            if str(s.get("план") or "").strip()
        }
        if len(plans) != 1:
            continue
        plan = next(iter(plans))
        if plan.startswith("общ") or len(group) >= 3:
            return (
                f"uuid {uid[:8]}: {len(group)} кадров «{place}» все план "
                f"{plan} — нужна лестница шаблона "
                "(ОБЩИЙ → СРЕДНИЙ → ДЕТАЛЬ/КРУПНЫЙ)"
            )
    return None


def shots_coverage_ops_reason(
    ops: list[Any],
    frames: list[dict[str, Any]],
) -> str | None:
    """Пустой закадр и копипаст действия — стоп. Прочий брак T/X — в лог."""
    by_uid = _frames_by_uuid(frames)
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields") or {}
        shots = fields.get("кадры") or fields.get("shots")
        if not isinstance(shots, list):
            continue
        uid = str(op.get("frame_uuid") or "")
        empty = [
            str(s.get("id") or "?")
            for s in shots
            if isinstance(s, dict) and not _shot_vo_chunk(s)
        ]
        if empty:
            return (
                f"uuid {uid[:8]}: пустой закадр у кадров {', '.join(empty)} "
                "— у каждого кадра свой кусок"
            )
        reason = coverage_template_reason(shots, uid)
        if reason and (
            "одинаковым действием" in reason or "одним действием сцены" in reason
        ):
            return reason
        if reason:
            logger.warning("shots coverage T/X: {}", reason)
        for shot in shots:
            if not str(shot.get("план") or "").strip():
                shot["план"] = "средний"
            if not str(shot.get("ракурс") or "").strip():
                shot["ракурс"] = "на уровне глаз"
        cell_vo = _frame_vo(by_uid.get(uid) or {})
        vo_len = _shot_vo_len_reason(shots, cell_vo, uid)
        if vo_len:
            return vo_len
        ladder = _same_place_plan_ladder_reason(shots, uid)
        if ladder:
            logger.warning("shots coverage ladder: {}", ladder)
    return None


def prompts_ops_reason(
    ops: list[Any],
    frames: list[dict[str, Any]],
) -> str | None:
    """Промты картинок: нужен промт_картинки, не fields.кадры."""
    del frames
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        uid = str(op.get("frame_uuid") or "").strip()
        img = _op_field(fields, "промт_картинки", "image_prompt")
        vid = _op_field(fields, "промт_видео", "animation_prompt")
        img2 = _op_field(fields, "промт_картинки_2", "image_prompt_shot2")
        kids = fields.get("промты_детей") or fields.get("child_prompts")
        has_kids = isinstance(kids, list) and any(kids)
        if not (img or vid or img2 or has_kids):
            return f"uuid {uid[:8]}: нет промт_картинки"
    return None


def _batch_footer(
    batch_i: int,
    split_level: int,
    n: int,
    *,
    footer_kind: str | None = None,
    used_senses: list[str] | None = None,
    used_places: list[str] | None = None,
) -> str:
    kind = (footer_kind or "").strip().lower()
    if kind in {"vo", "voiceover"}:
        return (
            f"\n# BATCH call={batch_i} split={split_level} "
            f"(закадр по {VO_UNITS_PER_BATCH})\n"
            f"В db_frames.json только этот кусок: {n} ячеек закадра.\n"
            "Верни ops ровно по каждому uuid: fields.закадр / voiceover_text. "
            "Чужие кадры не пиши. JSON apply-ops, без прозы.\n"
        )
    if kind in {"bits", "script_beats"}:
        return (
            f"\n# BATCH call={batch_i} split={split_level} "
            f"(биты, пачка {batch_i}, схема {SCRIPT_FRAMES_QC_PARALLEL_BATCHES} параллельно)\n"
            f"В db_frames.json только этот кусок: {n} ячеек закадра.\n"
            "Верни ops ровно по каждому uuid: fields.биты — JSON-массив "
            "объектов {{порядок, глагол, изменение, якорь}}. "
            "Число битов = число изменений в ячейке, не 1 и не строка-слоган. "
            "Не пиши закадр. Чужие кадры не пиши. JSON apply-ops, без прозы.\n"
        )
    if kind in {"action_chain", "main_action"}:
        return (
            f"\n# BATCH call={batch_i} split={split_level} "
            f"(главное действие, пачка {batch_i}, "
            f"{SCRIPT_FRAMES_QC_PARALLEL_BATCHES} параллельно)\n"
            f"В db_frames.json только этот кусок: {n} ячеек закадра.\n"
            "Верни ops ровно по каждому uuid: fields.главное_действие — "
            "нумерованная цепь «N. место — действие» + строка (кусок закадра). "
            "Даже одна строка закадра = «1. …» и скобки. Слоган без номера = брак. "
            "Не пиши закадр и биты. JSON apply-ops, без прозы.\n"
        )
    if kind in {"shots_coverage", "shots"}:
        return (
            f"\n# BATCH call={batch_i} split={split_level} "
            f"(кадры T/X, пачка {batch_i}, "
            f"{SCRIPT_FRAMES_QC_PARALLEL_BATCHES} параллельно)\n"
            f"В db_frames.json только этот кусок: {n} ячеек закадра.\n"
            "Верни ops ровно по каждому uuid: fields.кадры. "
            "Дерево ВЫБОР на КАЖДУЮ сцену цепи, не один T* на всю ячейку. "
            "T3 = «только смена места»: руки/взгляд/путь/удар — свои T*. "
            "T8 только прыжок жизни/новое место, один ОБЩИЙ на мир. "
            "Одно место — лестница планов, не один ОБЩИЙ подряд. "
            "shots из каталога задают роль и план, не текст. "
            "Полная лестница шаблона, не один кадр на сцену. "
            "drop_order (required=1 нельзя). "
            "У КАЖДОГО кадра лестницы поле действие — полное описание: "
            "помещение, кто в кадре, одежда, эмоция если видно лицо, "
            "видимый поступок. K2/K3/K4 пиши так же длинно, как K1. "
            "Не копируй «понял / испугался / решил», "
            "«тянет / открывает / берёт», «руки +». "
            "Закадр: не одно слово. Крупный/деталь от 13 символов, "
            "общий/средний от 20. Для коротких ячеек сохраняй исходный текст целиком. "
            "Пустой закадр запрещён: выкинь кадр (drop_order), не оставляй покрытие без куска. "
            "Не удлиняй T* сверх таблицы. То же место, что у прошлой сцены — "
            "сжатие без нового ОБЩЕГО (T1-c2 / T5 без K0), шаблон не менять "
            "ради чередования (T8>T8>T8 на разных местах — норма). "
            "У каждого кадра свой шаблон. "
            "Одно место → parent_id = id master (кроме T8 разных мест). "
            "Новое место только если его назвал закадр. "
            "Все parent_id null на одном сетапе = брак. "
            "Не пиши закадр, биты, главное_действие. JSON apply-ops, без прозы.\n"
        )
    if kind in {"prompts", "img"}:
        return (
            f"\n# BATCH call={batch_i} split={split_level} "
            f"(промты, кадров в вызове: {n})\n"
            f"В db_frames.json только этот кусок: {n} кадров.\n"
            "Верни ops ровно по каждому uuid: fields.промт_картинки, "
            "промт_видео, действие, крупность, движение, набор. "
            "Дети покрытия — все K2+ в промты_детей (порядок = кадры[]), "
            "не отдельным uuid и не в промт_картинки_2. "
            "Не пиши кадры, биты, закадр. Чужие кадры не пиши. "
            "JSON apply-ops, без прозы.\n"
        )
    if kind in {"camera_menu", "shot_menu"}:
        return (
            f"\n# BATCH call={batch_i} split={split_level} "
            f"(меню съёмки по {CAMERA_MENU_UNITS_PER_BATCH})\n"
            f"В db_frames.json только этот кусок: {n} кадров.\n"
            "Верни ops ровно по каждому uuid — ТОЛЬКО три поля:\n"
            "крупность (полное русское имя: Общий план / Средний план / "
            "Крупный план / Средне-крупный план / Врезка предмета / …),\n"
            "движение (статика | наезд | отъезд | сдвиг вбок | панорама | "
            "следование | ручная камера | вид сверху),\n"
            "набор (SET_01…SET_50 или короткое место, 2–6 слов).\n"
            "Не пиши промт_картинки, промт_видео, действие, закадр. "
            "JSON apply-ops, без прозы.\n"
        )
    if kind in {"analytics", "scene_analytics", "54_59"}:
        extra = ""
        if used_senses:
            extra += "\nУже занятые смысл_сцены (не копировать):\n"
            extra += "".join(f"- {s}\n" for s in used_senses[-40:])
        if used_places:
            extra += "Уже занятые места (не копировать дословно):\n"
            extra += "".join(f"- {p}\n" for p in used_places[-40:])
        return (
            f"\n# BATCH call={batch_i} split={split_level} "
            f"(аналитика 54–59, кадров в вызове: {n})\n"
            f"В db_frames.json только этот кусок: {n} кадров.\n"
            "На каждый uuid обязательны СВОИ место + смысл_сцены + тип + "
            "особенность + кластер (акцент можно пустым).\n"
            "Разный voiceover_text → другой смысл И другое место.\n"
            "Поменять только особенность_сцены при том же смысле/месте = брак, "
            "такой JSON не примут.\n"
            "Чужие кадры не пиши. JSON apply-ops, без прозы.\n"
            f"{extra}"
        )
    return (
        f"\n# BATCH call={batch_i} split={split_level} (схема 1→2→4)\n"
        f"В db_frames.json только этот кусок: {n} кадров.\n"
        "Верни ops ровно по каждому uuid из ЭТОГО файла. "
        "Чужие кадры не пиши. JSON apply-ops, без прозы.\n"
    )


async def run_apply_ops_batched(
    *,
    project_dir: Path,
    node_key: str,
    role: str,
    output_mode: str,
    prompt: str,
    accompanying: str,
    db_ctx: dict[str, Any],
    ctx_path: Path,
    project_id: int,
    dense: bool,
    apply_fn: ApplyFn | None = None,
    skip_if_field: str | None = None,
    force_full: bool = False,
    target_batches: int | None = None,
    chunk_size: int | None = None,
    parallel_max: int | None = None,
    stagger_sec: float | None = None,
    chunk_by_vo_unit: bool = False,
    footer_kind: str | None = None,
    on_progress: ProgressFn | None = None,
    allow_empty_ops: bool = False,
) -> OperatorApiResult:
    """Один GPT-вызов на пачку. Ошибка внутри пачки → 2, затем 4.

    ``chunk_size`` — заранее резать pending (script_frames_qc: 8
    отрезков закадра). ``chunk_by_vo_unit`` — пачка = N ячеек закадра
    (родитель + его шоты вместе), не N строк кадров. ``parallel_max`` —
    сколько пачек стартовать сразу (6), со сдвигом ``stagger_sec`` (1 с).
    Без chunk_size — сначала все pending, при ошибке 1→2→4.
    """
    from app.services.adaptive_llm_batches import next_split_level, split_in_half

    all_frames = list(db_ctx.get("frames") or [])
    pending = select_frames_for_batches(
        all_frames,
        dense=dense,
        skip_if_field=skip_if_field,
        force_full=force_full,
    )
    if not pending:
        logger.info(
            "[#{}] apply_ops batched node={!r}: нечего писать "
            "(все кадры уже заполнены или без закадра)",
            project_id,
            node_key,
        )
        return OperatorApiResult(
            reply_text='{"ops":[]}',
            output_paths=[ctx_path],
            apply_ops={"ops": [], "report": "already_filled"},
            applied_in_runner=True,
        )

    size = int(chunk_size) if chunk_size and int(chunk_size) > 0 else 0
    n_target = int(target_batches) if target_batches and int(target_batches) > 1 else 0
    if n_target:
        packs = split_into_n_packs(pending, n_target)
    elif size and chunk_by_vo_unit:
        packs = split_vo_units(pending, size)
    elif size:
        packs = split_frames(pending, size)
    else:
        packs = [pending]
    wave_n = int(parallel_max) if parallel_max and int(parallel_max) > 1 else 1
    delay_s = (
        float(stagger_sec)
        if stagger_sec is not None
        else (VO_STAGGER_SEC if wave_n > 1 else 0.0)
    )
    logger.info(
        "[#{}] apply_ops batched node={!r}: pending={} packs={} "
        "pack_size={} parallel={} stagger={}s adaptive 1→2→4 dense={}",
        project_id,
        node_key,
        len(pending),
        len(packs),
        size or "all",
        wave_n,
        delay_s,
        dense,
    )

    merged_ops: list[dict[str, Any]] = []
    replies: list[str] = []
    last_paths: list[Path] = [ctx_path]
    call_i = 0
    used_senses: list[str] = []
    used_places: list[str] = []
    meta_lock = asyncio.Lock()
    apply_lock = asyncio.Lock()

    async def _one_chunk(
        chunk: list[dict[str, Any]], level: int
    ) -> list[dict[str, Any]]:
        nonlocal call_i, last_paths
        async with meta_lock:
            call_i += 1
            my_i = call_i
        batch_ctx = {
            **db_ctx,
            "frames": chunk,
            "batch": {
                "index": my_i,
                "split_level": level,
                "frames": len(chunk),
            },
        }
        batch_path = ctx_path.with_name(
            f"db_frames_batch_{my_i:02d}_L{level}.json"
        )
        batch_path.write_text(
            json.dumps(batch_ctx, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        foot = _batch_footer(
            my_i,
            level,
            len(chunk),
            footer_kind=footer_kind,
            used_senses=used_senses,
            used_places=used_places,
        )
        api_kw = dict(
            project_dir=project_dir,
            node_key=node_key,
            role=role,
            output_mode=output_mode,
            prompt=prompt,
            accompanying=f"{accompanying}{foot}",
            input_paths=[batch_path],
            auto_pack=False,
        )
        # Тайм-аут на пачку: 90с по умолчанию (вместо вечного зависания на 10 мин)
        pack_timeout = (
            _PROMPT_PACK_TIMEOUT_S
            if (footer_kind or "").strip().lower()
            in {"prompts", "img", "camera_menu", "shot_menu"}
            else 90.0
        )
        try:
            res = await asyncio.wait_for(
                run_operator_api(**api_kw),
                timeout=pack_timeout,
            )
        except TimeoutError as exc:
            raise RuntimeError(
                f"excel_gpt node={node_key}: L{level} call {my_i} "
                f"timeout {pack_timeout:.0f}s ({len(chunk)} кадров)"
            ) from exc
        ops = []
        if isinstance(res.apply_ops, dict):
            ops = list(res.apply_ops.get("ops") or [])

        # Автопочинка UUID: remap по номеру кадра + repair near-miss (опечатки hex)
        from app.services.db_apply import remap_frame_number_uuids, repair_near_miss_frame_uuids

        known_list = [
            str(fr.get("uuid") or "").strip()
            for fr in chunk
            if str(fr.get("uuid") or "").strip()
        ]
        known = set(known_list)
        num_to_uuid: dict[int, str] = {}
        for fr in chunk:
            n = fr.get("number") or fr.get("номер")
            u = str(fr.get("uuid") or "").strip()
            if n is not None and u:
                try:
                    num_to_uuid[int(n)] = u
                except (ValueError, TypeError):
                    pass
        if num_to_uuid:
            remap_frame_number_uuids(ops, num_to_uuid)
        if known_list:
            repair_near_miss_frame_uuids(ops, known_list, max_distance=2)

        dropped = 0
        kept: list[dict[str, Any]] = []
        for op in ops:
            if not isinstance(op, dict):
                continue
            uid = str(op.get("frame_uuid") or "").strip()
            if uid in known:
                kept.append(op)
            else:
                dropped += 1
        if dropped:
            logger.warning(
                "[#{}] apply_ops batched node={!r}: call {} L{} "
                "dropped {} unknown frame_uuid",
                project_id,
                node_key,
                my_i,
                level,
                dropped,
            )
        ops = kept
        kind = (footer_kind or "").strip().lower()
        if kind in {"bits", "script_beats"}:
            repaired = repair_bits_ops(ops, chunk)
            if repaired:
                logger.info(
                    "[#{}] apply_ops batched node={!r}: call {} "
                    "починил биты ×{}",
                    project_id,
                    node_key,
                    my_i,
                    repaired,
                )
            bad_bits = bits_ops_reason(ops, chunk)
            if bad_bits:
                logger.warning(
                    "[#{}] apply_ops batched node={!r}: call {} L{} "
                    "биты слабоваты (пишем как есть): {}",
                    project_id,
                    node_key,
                    my_i,
                    level,
                    bad_bits,
                )
        if kind in {
            "analytics",
            "scene_analytics",
            "54_59",
        }:
            collapsed = analytics_ops_collapsed_reason(ops, chunk)
            if collapsed:
                raise RuntimeError(
                    f"excel_gpt node={node_key}: L{level} call {my_i} "
                    f"{collapsed}"
                )
        if kind in {"action_chain", "main_action"}:
            auto_repair_action_chain_ops(ops, chunk)
            repaired = repair_action_ops(ops)
            if repaired:
                logger.info(
                    "[#{}] apply_ops batched node={!r}: call {} "
                    "починил главное_действие ×{}",
                    project_id,
                    node_key,
                    my_i,
                    repaired,
                )
            bad_action = action_chain_ops_reason(ops, chunk)
            if bad_action:
                logger.warning(
                    "[#{}] apply_ops batched node={!r}: call {} L{} "
                    "действие слабовато (пишем как есть): {}",
                    project_id,
                    node_key,
                    my_i,
                    level,
                    bad_action,
                )
        if kind in {"shots_coverage", "shots"}:
            repaired_parents = repair_same_place_shot_parents(ops)
            if repaired_parents:
                logger.info(
                    "[#{}] apply_ops batched node={!r}: parent_id проставлен "
                    "по таблице у {} кадров (GPT оставил null)",
                    project_id,
                    node_key,
                    repaired_parents,
                )
            filled = fill_kadry_ops_from_catalog(ops, chunk)
            if filled:
                logger.info(
                    "[#{}] apply_ops batched node={!r}: лестница T/X "
                    "дописана из каталога +{} кадров",
                    project_id,
                    node_key,
                    filled,
                )
            repaired_parents = repair_same_place_shot_parents(ops)
            repaired_vo = repair_shot_vo_ops(ops, chunk)
            if repaired_vo:
                logger.info(
                    "[#{}] apply_ops batched node={!r}: call {} "
                    "дописал пустой закадр ×{}",
                    project_id,
                    node_key,
                    my_i,
                    repaired_vo,
                )
            bad_shots = shots_coverage_ops_reason(ops, chunk)
            if bad_shots:
                logger.warning(
                    "[#{}] apply_ops batched node={!r}: call {} L{} "
                    "кадры слабоваты (пишем как есть): {}",
                    project_id,
                    node_key,
                    my_i,
                    level,
                    bad_shots,
                )
        if kind in {"prompts", "img"}:
            bad_prompts = prompts_ops_reason(ops, chunk)
            if bad_prompts:
                raise RuntimeError(
                    f"excel_gpt node={node_key}: L{level} call {my_i} "
                    f"{bad_prompts}"
                )
        logger.info(
            "[#{}] apply_ops batched node={!r}: call {} L{} sent={} got_ops={}",
            project_id,
            node_key,
            my_i,
            level,
            len(chunk),
            len(ops),
        )
        if on_progress is not None:
            try:
                total_target = len(pending)
                total_applied = len(merged_ops) + len(ops)
                await on_progress(
                    f"кадры {min(total_applied, total_target)}/{total_target} · вызов {my_i}"
                )
            except Exception:
                logger.debug("apply_ops progress callback failed", exc_info=True)
        if not ops:
            if allow_empty_ops:
                # QC: ops только по нарушителям; пустой пакет = ок.
                return []
            raise RuntimeError(
                f"excel_gpt node={node_key}: L{level} call {my_i} "
                f"без ops (ждали {len(chunk)} кадров)."
            )
        if apply_fn is not None:
            payload = dict(res.apply_ops or {})
            payload["ops"] = ops
            payload["export_xlsx"] = False
            async with apply_lock:
                await apply_fn(payload)
        if (footer_kind or "").strip().lower() in {
            "analytics",
            "scene_analytics",
            "54_59",
        }:
            for op in ops:
                if not isinstance(op, dict):
                    continue
                fields = op.get("fields")
                if not isinstance(fields, dict):
                    continue
                sense = _op_field(fields, "смысл_сцены", "scene_sense")
                place = _op_field(fields, "место", "place")
                if sense and sense not in used_senses:
                    used_senses.append(sense)
                if place and place not in used_places:
                    used_places.append(place)
        async with meta_lock:
            last_paths = list(res.output_paths or []) or [batch_path]
            replies.append(res.reply_text or "")
            merged_ops.extend(ops)
        if allow_empty_ops:
            return []
        got_uuids = {
            str(op.get("frame_uuid") or "").strip()
            for op in ops
            if isinstance(op, dict)
        }
        missing = [
            fr
            for fr in chunk
            if str(fr.get("uuid") or "").strip() not in got_uuids
        ]
        return missing

    async def _run_adaptive(
        chunk: list[dict[str, Any]], level: int, can_retry: bool = True
    ) -> None:
        try:
            missing = await _one_chunk(chunk, level)
        except Exception as exc:
            if allow_empty_ops:
                logger.warning(
                    "[#{}] apply_ops node={!r}: L{} fail ({}) — "
                    "QC не ретраим пачку (промты уже в БД)",
                    project_id,
                    node_key,
                    level,
                    str(exc)[:160],
                )
                return
            nxt = next_split_level(level)
            if nxt is None or len(chunk) <= 1:
                raise
            parts = split_in_half(chunk)
            logger.warning(
                "[#{}] apply_ops node={!r}: L{} fail ({}) → split {} "
                "frames into {} packs",
                project_id,
                node_key,
                level,
                str(exc)[:160],
                len(chunk),
                len(parts),
            )
            for part in parts:
                await _run_adaptive(part, nxt, can_retry=True)
            return
        if not missing:
            return
        # 1 пропущенный uuid: ещё раз только его. Раньше len<=1 сразу
        # валил всю пачку (7/8 → RuntimeError → soft retry всех 188).
        if len(missing) == 1:
            uid = str(missing[0].get("uuid") or "")[:8]
            if len(chunk) <= 1:
                raise RuntimeError(
                    f"excel_gpt node={node_key}: L{level} неполный apply-ops "
                    f"(0/1). uuid: {uid}"
                )
            logger.warning(
                "[#{}] apply_ops node={!r}: L{} incomplete {}/{} → retry uuid {}",
                project_id,
                node_key,
                level,
                len(chunk) - 1,
                len(chunk),
                uid,
            )
            await _run_adaptive(missing, next_split_level(level) or level, can_retry=False)
            return

        # Если не хватает нескольких кадров (обрыв сокета / неполный стрим) —
        # сначала пробуем 1 раз дозапросить недостающие кадры без дробления!
        if can_retry and len(missing) > 1:
            logger.warning(
                "[#{}] apply_ops node={!r}: L{} incomplete {}/{} (сокет обрыв/salvage) "
                "→ retry missing ({} кадров) без дробления",
                project_id,
                node_key,
                level,
                len(chunk) - len(missing),
                len(chunk),
                len(missing),
            )
            await asyncio.sleep(1.0)
            await _run_adaptive(missing, level, can_retry=False)
            return

        nxt = next_split_level(level)
        if nxt is None:
            raise RuntimeError(
                f"excel_gpt node={node_key}: L{level} неполный apply-ops "
                f"({len(chunk) - len(missing)}/{len(chunk)}). uuid: "
                f"{', '.join(str(fr.get('uuid') or '')[:8] for fr in missing[:8])}"
            )
        logger.warning(
            "[#{}] apply_ops node={!r}: L{} incomplete {}/{} → split L{}",
            project_id,
            node_key,
            level,
            len(chunk) - len(missing),
            len(chunk),
            nxt,
        )
        for part in split_in_half(missing):
            await _run_adaptive(part, nxt, can_retry=True)

    async def _run_pack(delay: float, pack: list[dict[str, Any]]) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await _run_adaptive(pack, 1)

    if wave_n <= 1 or len(packs) <= 1:
        for pack in packs:
            await _run_adaptive(pack, 1)
    else:
        for wave_start in range(0, len(packs), wave_n):
            wave = packs[wave_start : wave_start + wave_n]
            logger.info(
                "[#{}] apply_ops node={!r}: wave {}–{} / {} "
                "(parallel={}, stagger={}s)",
                project_id,
                node_key,
                wave_start + 1,
                wave_start + len(wave),
                len(packs),
                wave_n,
                delay_s,
            )
            if on_progress is not None:
                try:
                    await on_progress(
                        f"волна {wave_start + 1}–{wave_start + len(wave)} / {len(packs)}"
                    )
                except Exception:
                    logger.debug("apply_ops progress callback failed", exc_info=True)
            results = await asyncio.gather(
                *[
                    _run_pack(i * delay_s, pack)
                    for i, pack in enumerate(wave)
                ],
                return_exceptions=True,
            )
            errs = [r for r in results if isinstance(r, BaseException)]
            if errs:
                raise errs[0]

    ctx_path.write_text(
        json.dumps(db_ctx, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return OperatorApiResult(
        reply_text="\n\n".join(replies),
        output_paths=last_paths,
        apply_ops={"ops": merged_ops},
        applied_in_runner=apply_fn is not None,
    )
