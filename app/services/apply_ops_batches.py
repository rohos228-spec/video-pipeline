"""Apply-ops батчи: длинный db_frames.json режется, чтобы SSE не обрывался.

Эмпирика #6 n_excel_gpt_2: 116 плотных shot_01 в одном ответе →
stream_partial, salvage ops=65, нода done. Аналитика (короткие поля)
на тех же 116 кадрах проходит целиком.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.gpt_operator_client import OperatorApiResult, run_operator_api
from app.services.node_trace import (
    Change,
    NodeRunRecorder,
    changes_to_entries,
    diff_ops,
    entry,
    snapshot,
    uuid_in_text,
)
from app.services.scene_design.camera_expand import vo_chunk_is_dangling
from app.services.scene_shot_grammar import (
    SHOT_VO_CUTAWAY_MAX,
    SHOT_VO_CUTAWAY_MIN,
    SHOT_VO_MAX,
    SHOT_VO_MIN,
    apply_grammar_to_ops,
    fill_bit_spans,
    is_cutaway_shot,
    merge_same_place_scenes,
    carry_pose,
    character_id_problems,
    ensure_character_ids,
    normalize_reply_cameras,
    reflow_vo_by_sentences,
    merge_trivial_shots,
    ensure_wide_before_dialogue,
    spread_vo_sentences,
    repair_shot_vo_lengths,
    shots_continuity_problems,
    shots_grammar_reason,
    vo_piece_ends_sentence,
    vo_sentence_problems,
)
from app.services.shot_templates import fill_kadry_from_catalog

# Плотный выход (shot_01 + главное_действие): 102+ кадров одним ответом
# рвёт SSE (~90с timeout) и подмешивает фейковые uuid. 10 пачек параллельно.
DENSE_TARGET_BATCHES = 10
DENSE_PARALLEL_MAX = 10
_DENSE_TARGET_BATCHES = DENSE_TARGET_BATCHES
_DENSE_FRAMES_PER_BATCH = 8
_DENSE_JSON_BYTES_PER_BATCH = 20_000
# Короткий выход (аналитика 54–59): 116 кадров / ~44k символов — ок.
_LIGHT_FRAMES_PER_BATCH = 120
_LIGHT_JSON_BYTES_PER_BATCH = 180_000
# Сценарист / закадр (прочие ноды): пачка = 6 ячеек VO.
VO_UNITS_PER_BATCH = 6
# Группа script_frames_qc (биты → действие → кадры-шаги → QC):
# по 8 отрезков. В полёте не больше 8 пачек, старт с разницей в несколько секунд.
# fw_frames (старые канвасы) — по 6 кадров (тяжёлые image+anim).
# Из main: соло-пачка жирного K1, автопочинка UUID, не резать пачку на salvage.
SCRIPT_FRAMES_QC_UNITS_PER_BATCH = 8
SCRIPT_FRAMES_QC_PARALLEL_BATCHES = 10
# Шоты (13.10.2026, прогон 18:37–19:25 на vibecode.moe напрямую, без relay):
# пачка из 3 ячеек на reasoning high = 40–73k символов, 5–7 мин потока;
# 34 пачки, 0 обрывов, 0 502, но 3 пачки упёрлись в стену 540 с и были
# переделаны с нуля (по 9 мин слота впустую). Итог 47 мин на 97 ячеек.
# * 3 ячейки в запросе — обрыв теряет не больше одной незакрытой операции,
#   закрытые пишутся сразу;
# * конвейер: 10 запросов в полёте без барьера «волны» (vibecode держит
#   8 параллельных потоков без 502 — проверено), старт через 3 с;
# * стена 15 мин — живой длинный ответ не выбрасываем; мёртвый сокет
#   ловит keepalive в gpt_api за ~30 с, а не стена.
# Повторы: сеть — до 3 раз та же пачка с растущей паузой (502 апстрима
# пережидаем, а не долбим); недостающие ячейки — 2 добора.
SHOTS_UNITS_PER_BATCH = 3
SHOTS_PARALLEL = 16  # 2026-10-15: все пачки сразу (38 ячеек / 3 = 13)
SHOTS_STAGGER_SEC = 3.0
SHOTS_PACK_TIMEOUT_S = 900.0
SHOTS_NET_RETRIES = 3
SHOTS_MISSING_ROUNDS = 2
# Пауза перед повтором пачки после сетевой/LLM ошибки, по номеру попытки.
SHOTS_NET_RETRY_BACKOFF_S = (20.0, 60.0, 120.0)
# Площадка: весь набор тремя пачками, старт через 30 с.
ACTION_TARGET_BATCHES = 3
ACTION_STAGGER_SEC = 4.0  # 2026-10-15: было 30 с, пользователь: стаггер 2–5 с
FW_FRAMES_PER_BATCH = 6
VO_PARALLEL_MAX = 8
FW_FRAMES_PARALLEL_BATCHES = 8
VO_STAGGER_SEC = 5.0
# Жёсткий потолок отправки: и первая волна, и разрезанные половины.
PACK_IN_FLIGHT_MAX = 8
PACK_SEND_GAP_S = 5.0
SHOT_VO_MIN_CHARS = SHOT_VO_MIN
SHOT_VO_MAX_CHARS = SHOT_VO_MAX
# Жирная ячейка кадров: длинная цепь действия → соло-пачка (не схлопывать в 1–5).
HEAVY_ACTION_STEPS = 12
HEAVY_ACTION_CHARS = 1200
HEAVY_VO_CHARS = 5000
_SHOTS_FOOTER_KINDS = frozenset(
    {"shots_coverage", "shots", "shots_qc", "qc_shots"}
)

_COMPLETE_ATTRS_DENSE = ("main_action", "shot01_description")
_IMG_SKIP_KEYS = ("image_prompt", "промт_картинки")
_ANIM_SKIP_KEYS = ("animation_prompt", "промт_видео")
_ACTION_SKIP_KEYS = ("shot01_action", "main_action", "действие")
SKIP_PROMPTS_AND_ACTION = "prompts_and_action"
# Добор меню съёмки: крупность + движение + набор (по 6).
SKIP_CAMERA_MENU = "camera_menu"
CAMERA_MENU_UNITS_PER_BATCH = 6
# Стена на пачку = GPT_TIMEOUT_S. 90с резало script/shots на Салтыковой.
_SIZE_SKIP_KEYS = ("крупность", "size", "shot_size")
_MOVE_SKIP_KEYS = ("движение", "движение_камеры", "move", "shot_move")
_SET_SKIP_KEYS = ("набор", "set", "shot_set")

ApplyFn = Callable[[dict[str, Any]], Awaitable[None]]
ProgressFn = Callable[[str], Awaitable[None]]


def pack_call_timeout_s(footer_kind: str | None = None) -> float:
    """Стена на одну пачку: тройной GPT-таймаут, не короче 180 с.

    ``footer_kind`` не меняет стену: шоты и остальные ноды ждут одно и то же.
    """
    del footer_kind
    from app.settings import settings

    wall = float(getattr(settings, "gpt_timeout_s", 600.0) or 600.0)
    return max(180.0, wall * 3.0)


def shots_short_packs(footer_kind: str | None) -> bool:
    """Шоты: не резать пачку дальше и не слать её целиком по три раза."""
    return (footer_kind or "").strip().lower() in {"shots_coverage", "shots"}


class _PackSendGate:
    """Не больше in_flight запросов этого прогона, старт с разницей в gap_s."""

    def __init__(self, gap_s: float, in_flight: int | None = None) -> None:
        cap = PACK_IN_FLIGHT_MAX
        if in_flight is not None and int(in_flight) > 0:
            cap = max(1, min(PACK_IN_FLIGHT_MAX, int(in_flight)))
        self.sem = asyncio.Semaphore(cap)
        self.lock = asyncio.Lock()
        self.next_at = 0.0
        self.gap_s = max(0.0, float(gap_s))


_pack_gate: contextvars.ContextVar[_PackSendGate | None] = contextvars.ContextVar(
    "pack_send_gate",
    default=None,
)


async def _acquire_pack_send_slot() -> None:
    gate = _pack_gate.get()
    if gate is None:
        return
    await gate.sem.acquire()
    try:
        while True:
            async with gate.lock:
                now = asyncio.get_running_loop().time()
                wait = gate.next_at - now
                if wait <= 0:
                    gate.next_at = now + gate.gap_s
                    return
            await asyncio.sleep(wait)
    except BaseException:
        gate.sem.release()
        raise


def _release_pack_send_slot() -> None:
    gate = _pack_gate.get()
    if gate is not None:
        gate.sem.release()


def frames_per_batch(
    *,
    n_frames: int,
    json_bytes: int,
    dense: bool,
    skip_if_field: str | None = None,
    target_batches: int | None = None,
) -> int:
    n = max(int(n_frames), 0)
    if n <= 0:
        return 1
    if target_batches and int(target_batches) > 0:
        from math import ceil

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
    # Соло: VO>=5000 ИЛИ длинная цепь действия. attrs/кадры/площадка
    # в len(str(frame)) не считаем. Жирные — любая позиция, не только первая.
    heavy: list[Any] = []
    light: list[Any] = []
    for fr in frames:
        if isinstance(fr, dict) and _frame_is_heavy_shots_cell(fr):
            heavy.append(fr)
        else:
            light.append(fr)
    packs: list[list[Any]] = [[fr] for fr in heavy]
    packs.extend([light[i : i + size] for i in range(0, len(light), size)])
    return packs


def _frame_vo_chars(frame: dict[str, Any]) -> int:
    """Длина закадра, без attrs/кадры/площадка."""
    return len(str(frame.get("voiceover_text") or frame.get("закадр") or ""))


def _frame_main_action(frame: dict[str, Any]) -> str:
    """главное_действие ячейки (top-level или attrs)."""
    if not isinstance(frame, dict):
        return ""
    action = str(
        frame.get("главное_действие")
        or frame.get("main_action")
        or frame.get("shot01_action")
        or ""
    ).strip()
    if action:
        return action
    attrs = frame.get("attrs") if isinstance(frame.get("attrs"), dict) else {}
    return str(
        attrs.get("главное_действие")
        or attrs.get("main_action")
        or attrs.get("shot01_action")
        or ""
    ).strip()


def _frame_action_steps(frame: dict[str, Any]) -> int:
    """Сколько видимых шагов действия (0 если пусто/не разобрали)."""
    action = _frame_main_action(frame)
    if not action:
        return 0
    try:
        from app.services.scene_shot_grammar import count_action_steps

        return int(count_action_steps(action) or 0)
    except Exception:
        return 0


def _frame_expected_shots(frame: dict[str, Any]) -> int:
    """Ожидаемое N кадров: min(шаги, VO-cap). Для footer и heavy."""
    action = _frame_main_action(frame)
    vo = str(frame.get("voiceover_text") or frame.get("закадр") or "")
    try:
        from app.services.scene_shot_grammar import expected_shots_for_cell

        return int(expected_shots_for_cell(action, vo) or 0)
    except Exception:
        return _frame_action_steps(frame)


def _frame_is_heavy_shots_cell(frame: dict[str, Any]) -> bool:
    """Длинная цепь / большой VO-cap → соло-пачка (GPT схлопывает)."""
    if not isinstance(frame, dict):
        return False
    if _frame_vo_chars(frame) >= HEAVY_VO_CHARS:
        return True
    action = _frame_main_action(frame)
    if len(action) >= HEAVY_ACTION_CHARS:
        return True
    if _frame_action_steps(frame) >= HEAVY_ACTION_STEPS:
        return True
    # VO-capacity expected N тоже жирный: GPT иначе отдаёт 1–5 вместо ~N
    return _frame_expected_shots(frame) >= HEAVY_ACTION_STEPS


def _unit_is_heavy(unit: list[dict[str, Any]]) -> bool:
    return any(_frame_is_heavy_shots_cell(f) for f in unit if isinstance(f, dict))


def _vo_unit_key(frame: dict[str, Any]) -> str:
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
    light_units: list[list[dict[str, Any]]] = []
    # Соло: VO>=5000 или длинная цепь действия — любая позиция в списке.
    for unit in units:
        vo_len = sum(_frame_vo_chars(f) for f in unit)
        if vo_len >= HEAVY_VO_CHARS or _unit_is_heavy(unit):
            packs.append(unit)
        else:
            light_units.append(unit)
    for i in range(0, len(light_units), unit_size):
        pack: list[dict[str, Any]] = []
        for unit in light_units[i : i + unit_size]:
            pack.extend(unit)
        if pack:
            packs.append(pack)
    return packs or [list(frames)]


def split_into_fixed_units(
    frames: list[dict[str, Any]], unit_size: int
) -> list[list[dict[str, Any]]]:
    """Пачки по N ячеек закадра. Жирную ячейку не выносим в соло."""
    if not frames:
        return []
    unit_size = max(1, int(unit_size))
    units = group_vo_units(frames)
    if len(units) <= 1:
        return [frames[i : i + unit_size] for i in range(0, len(frames), unit_size)]
    packs: list[list[dict[str, Any]]] = []
    for i in range(0, len(units), unit_size):
        pack: list[dict[str, Any]] = []
        for unit in units[i : i + unit_size]:
            pack.extend(unit)
        if pack:
            packs.append(pack)
    return packs


def split_into_n_packs(frames: list[dict[str, Any]], n: int) -> list[list[dict[str, Any]]]:
    """Ровно n пачек. VO-ячейка (родитель+шоты) не режется пополам.
    Без HEAVY-solo: иначе 68 юнитов раздуваются до ~34 пачек.
    """
    if not frames:
        return []
    n = max(1, int(n))
    units = group_vo_units(frames)
    if len(units) > 1:
        n = min(n, len(units))
        per = max(1, (len(units) + n - 1) // n)
        packs: list[list[dict[str, Any]]] = []
        for i in range(0, len(units), per):
            pack: list[dict[str, Any]] = []
            for unit in units[i : i + per]:
                pack.extend(unit)
            if pack:
                packs.append(pack)
        return packs
    n = min(n, len(frames))
    per = max(1, (len(frames) + n - 1) // n)
    return split_frames(frames, per)


def split_into_n_packs(frames: list[dict[str, Any]], n: int) -> list[list[dict[str, Any]]]:
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
    return len(
        "".join(c for c in (text or "") if unicodedata.category(c) != "Mn")
    )


def _shot_vo_chunk(shot: dict[str, Any]) -> str:
    return str(shot.get("закадр") or shot.get("voiceover_text") or "").strip()


def _is_special_short_vo(chunk: str, plan: str) -> bool:
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
    cell_n = _vo_visible_len(cell_vo)
    if cell_n <= SHOT_VO_MIN_CHARS and len(shots) <= 1:
        return None
    for shot in shots:
        chunk = _shot_vo_chunk(shot)
        if not chunk:
            continue  # пустая перебивка — норма
        sid = str(shot.get("id") or "")
        plan = str(shot.get("план") or "")
        if vo_chunk_is_dangling(chunk):
            return (
                f"uuid {uid[:8]}: кадр {sid or '?'} обрубок закадра "
                f"({chunk[-24:]!r})"
            )
        words = re.findall(r"[^\W\d_]+", chunk, flags=re.UNICODE)
        covers_cell = " ".join(chunk.split()) == " ".join((cell_vo or "").split())
        if (
            1 <= len(words) <= 2
            and not _is_special_short_vo(chunk, plan)
            and not covers_cell
            and not is_cutaway_shot(shot)
        ):
            n = _vo_visible_len(chunk)
            return (
                f"uuid {uid[:8]}: кадр {sid or '?'} закадр {n} симв. "
                "(1–2 слова — только титр/имя/ударная деталь/перебивка)"
            )
        n = _vo_visible_len(chunk)
        cutaway = is_cutaway_shot(shot)
        if n > SHOT_VO_MAX_CHARS:
            return (
                f"uuid {uid[:8]}: кадр {sid or '?'} закадр {n} симв. "
                f"(разрежь, норма {SHOT_VO_MIN_CHARS}–{SHOT_VO_MAX_CHARS})"
            )
        if n < SHOT_VO_CUTAWAY_MIN and not cutaway:
            return (
                f"uuid {uid[:8]}: кадр {sid or '?'} закадр {n} симв. "
                f"(<{SHOT_VO_CUTAWAY_MIN} только у перебивки)"
            )
        if n < SHOT_VO_MIN_CHARS and not cutaway:
            return (
                f"uuid {uid[:8]}: кадр {sid or '?'} закадр {n} симв. "
                f"(норма {SHOT_VO_MIN_CHARS}–{SHOT_VO_MAX_CHARS}; "
                f"перебивка {SHOT_VO_CUTAWAY_MIN}–{SHOT_VO_CUTAWAY_MAX})"
            )
    return None


def _place_attested_in_vo(place: str, vo: str) -> bool:
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



def infer_bit_verb(bit: dict[str, Any], vo: str = "") -> str:
    """Real verb from bit sense; not default говорит."""
    change = str(bit.get("изменение") or "").strip()
    anchor = str(bit.get("якорь") or "").strip()
    blob = f"{change} {anchor} {vo}".casefold()
    patterns = (
        (r"родил", "родился"),
        (r"умер|смерт", "умер"),
        (r"женил|вышел замуж", "женился"),
        (r"арестова|задержа", "арестовали"),
        (r"убий|убил", "убил"),
        (r"искал|розыск|ищут", "ищут"),
        (r"нашли|найден", "нашли"),
        (r"бежал|бежит", "бежал"),
        (r"вош[её]л|вход", "вошёл"),
        (r"выш[её]л", "вышел"),
        (r"открыл|открыва", "открыл"),
        (r"закрыл", "закрыл"),
        (r"положил|клад", "положил"),
        (r"достал|вынул", "достал"),
        (r"сказал|говор|произн", "говорит"),
        (r"спросил|спрашив", "спрашивает"),
        (r"ответил|отвеча", "отвечает"),
        (r"смотр|гляд", "смотрит"),
        (r"писал|пишет", "пишет"),
        (r"читал|читает", "читает"),
        (r"жил|живут", "жил"),
        (r"работал", "работал"),
        (r"вернул", "вернулся"),
        (r"направил", "направили"),
        (r"изменил|меняет|стало", "меняется"),
    )
    for rx, verb in patterns:
        if re.search(rx, blob, re.IGNORECASE):
            return verb
    m = re.search(r"[→\-]\s*([^\s,;:]+)", change)
    if m:
        token = m.group(1).strip(" «»\"'")
        if token and not token.isdigit():
            return token[:40]
    words = re.findall(r"[^\W\d_]+", change, flags=re.UNICODE)
    for w in words:
        low = w.casefold()
        if len(w) >= 4 and low not in {"пусто", "затем", "после", "когда", "этот", "этой"}:
            if re.search(r"(л|ла|ли|ет|ит|ал|ил|ут|ют|ся)$", low):
                return w
    return "происходит"


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
                    "глагол": infer_bit_verb({"изменение": "смысл из закадра", "якорь": str(raw)}, vo),
                    "изменение": "смысл из закадра",
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
                    "глагол": infer_bit_verb({"изменение": "смысл из закадра", "якорь": str(bit)}, vo),
                    "изменение": "смысл из закадра",
                    "якорь": _snap_anchor_to_vo(str(bit), vo),
                }
                fixed += 1
                continue
            if not str(bit.get("глагол") or "").strip():
                bit["глагол"] = infer_bit_verb(bit, vo)
                fixed += 1
            if not str(bit.get("изменение") or "").strip():
                bit["изменение"] = "смысл из закадра"
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
    """Пустой закадр перебивки — норма. Хвост ячейки кладём на покрывающий кадр."""
    from app.services.scene_shot_grammar import _assign_vo_by_steps

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
        dict_shots = [s for s in shots if isinstance(s, dict)]
        glued = " ".join(_shot_vo_chunk(s) for s in dict_shots if _shot_vo_chunk(s))
        cell = " ".join((cell_vo or "").split())
        if not cell:
            continue
        if glued and " ".join(glued.split()) == cell:
            continue
        pieces = _assign_vo_by_steps(
            cell_vo, [str(s.get("действие") or "") for s in dict_shots]
        )
        for shot, piece in zip(dict_shots, pieces, strict=False):
            if _shot_vo_chunk(shot) != (piece or "").strip():
                shot["закадр"] = piece
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
            # глагол рядом с якорем; якорь — дословный кусок VO, глагол в якорь не подставлять
            if not str(bit.get("глагол") or "").strip():
                bit["глагол"] = infer_bit_verb(bit, vo)
            anchor = str(bit.get("якорь") or "").strip()
            if not anchor:
                snapped = _snap_anchor_to_vo("", vo)
                if snapped:
                    bit["якорь"] = snapped
                    anchor = snapped
                else:
                    return f"uuid {uid[:8]}: бит {i} без якоря из закадра"
            anchor_cf = str(bit.get("якорь") or "").strip().casefold()
            if anchor_cf and anchor_cf not in vo_cf:
                anchor_words = [w for w in re.findall(r"[^\W\d_]+", anchor_cf, flags=re.UNICODE) if len(w) >= 3]
                if not any(w in vo_cf for w in anchor_words):
                    return f"uuid {uid[:8]}: якорь бита {i} не из закадра"
        filled = fill_bit_spans(vo, raw)
        if "биты" in fields:
            fields["биты"] = filled
        elif "bits" in fields:
            fields["bits"] = filled
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
        merged = merge_same_place_scenes(text)
        if merged != text:
            if "главное_действие" in fields:
                fields["главное_действие"] = merged
            elif "main_action" in fields:
                fields["main_action"] = merged
            else:
                fields["главное_действие"] = merged


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
        from app.services.scene_plan import is_reverse_shot_pair
        if len(group) >= 2 and all(
            is_reverse_shot_pair(a, b) for a, b in zip(group, group[1:])
        ):
            continue  # встречные планы диалога (пара СРЕДНЕ-КРУПНЫХ с плеча) — норма
        if plan.startswith("общ") or len(group) >= 3:
            return (
                f"uuid {uid[:8]}: {len(group)} кадров «{place}» все план "
                f"{plan} — нужна лестница шаблона "
                "(ОБЩИЙ → СРЕДНИЙ → ДЕТАЛЬ/КРУПНЫЙ)"
            )
    return None


def _frame_plan(fr: dict[str, Any] | None) -> Any:
    if not fr:
        return None
    raw = fr.get("площадка")
    if raw is None and isinstance(fr.get("attrs"), dict):
        raw = fr["attrs"].get("площадка")
    return raw


def normalize_action_plan_ops(ops: list[Any]) -> int:
    """fw_action: площадка ячейки → канон (зоны/проходы/люди по компасу)."""
    from app.services.scene_plan import apply_scene_plan

    n = 0
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        key = next(
            (k for k in ("площадка", "план_площадки", "space_plan") if k in fields),
            None,
        )
        if key is None:
            continue
        plan, _ = apply_scene_plan([], fields.pop(key))
        fields["площадка"] = plan
        n += 1
    return n


_PLAN_CONTEXT_CELLS = 2


def apply_scene_plan_ops(
    ops: list[Any],
    frames: list[dict[str, Any]],
    *,
    issues_out: list[dict[str, Any]] | None = None,
    decisions_out: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[str]]:
    """Кадры ячейки через план площадки: состояния, порог, путь, экран.

    Пишет fields.площадка (канон + выведенное кодом) и ``раскладка`` в
    каждый кадр. Возвращает (исправлено, не исправлено).
    ``issues_out`` / ``decisions_out`` — для дневника: проблемы площадки и
    решения СТАРТ/КОНЕЦ с ``frame_uuid``.
    """
    from app.services.scene_plan import (
        FREEZE_DECISIONS,
        apply_scene_plan_cells,
        plan_fix_notes,
        plan_hard_reasons,
        with_plan_notes,
    )

    op_by_uid: dict[str, dict[str, Any]] = {}
    for op in ops or []:
        if not isinstance(op, dict) or not isinstance(op.get("fields"), dict):
            continue
        shots = op["fields"].get("кадры")
        uid = str(op.get("frame_uuid") or "").strip()
        if uid and isinstance(shots, list) and shots:
            op_by_uid[uid] = op
    if not op_by_uid:
        return [], []
    # Соседние ячейки чанка — контекст (дверь открыта там? где стоял герой?).
    parents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fr in frames or []:
        if not isinstance(fr, dict) or _is_child_frame(fr):
            continue
        uid = str(fr.get("uuid") or "").strip()
        if uid and uid not in seen:
            seen.add(uid)
            parents.append(fr)
    near = {
        j
        for i, fr in enumerate(parents)
        if str(fr.get("uuid") or "").strip() in op_by_uid
        for j in range(i - _PLAN_CONTEXT_CELLS, i + _PLAN_CONTEXT_CELLS + 1)
    }
    cells: list[dict[str, Any]] = []
    for i, fr in enumerate(parents):
        if i not in near:
            continue
        uid = str(fr.get("uuid") or "").strip()
        op = op_by_uid.get(uid)
        if op is not None:
            fields = op["fields"]
            raw = fields.get("площадка")
            cells.append({
                "key": uid,
                "shots": fields["кадры"],
                "plan": raw if raw is not None else _frame_plan(fr),
                "owned": True,
            })
            continue
        shots = _frame_shots(fr)
        if shots:
            cells.append(
                {"key": uid, "shots": shots, "plan": _frame_plan(fr), "owned": False}
            )
    for uid, op in op_by_uid.items():
        if uid not in seen:
            fields = op["fields"]
            cells.append({
                "key": uid,
                "shots": fields["кадры"],
                "plan": fields.get("площадка"),
                "owned": True,
            })
    decisions: list[dict[str, Any]] = []
    token = FREEZE_DECISIONS.set(decisions)
    try:
        base, issues_by = apply_scene_plan_cells(cells)
    finally:
        FREEZE_DECISIONS.reset(token)
    if decisions_out is not None:
        decisions_out.extend(
            {**d, "frame_uuid": d.pop("cell")} for d in decisions if d.get("cell") in op_by_uid
        )
    fixed: list[str] = []
    hard: list[str] = []
    for cell in cells:
        if not cell["owned"]:
            continue
        uid = cell["key"]
        issues = issues_by.get(uid, [])
        if issues_out is not None:
            issues_out.extend(
                {
                    "frame_uuid": uid,
                    "кадр": it.get("кадр") or 0,
                    "id": str(it.get("id") or ""),
                    "вид": str(it.get("вид") or ""),
                    "текст": str(it.get("текст") or ""),
                    "исправлено": bool(it.get("исправлено")),
                }
                for it in issues
            )
        op_by_uid[uid]["fields"]["площадка"] = with_plan_notes(
            base, cell["plan"], issues, cell["shots"]
        )
        fixed.extend(f"uuid {uid[:8]} {t}" for t in plan_fix_notes(issues))
        hard.extend(f"uuid {uid[:8]} {t}" for t in plan_hard_reasons(issues))
    return fixed, hard


def _is_child_frame(fr: dict[str, Any]) -> bool:
    if str(fr.get("coverage_role") or "") == "child":
        return True
    cs = fr.get("camera_subdivide")
    if not isinstance(cs, dict) and isinstance(fr.get("attrs"), dict):
        cs = fr["attrs"].get("camera_subdivide")
    if not isinstance(cs, dict):
        return False
    kind = str(cs.get("coverage_kind") or "").strip().lower()
    return str(cs.get("role") or "") == "shot" or kind == "child"


def _frame_shots(fr: dict[str, Any]) -> list[dict[str, Any]]:
    raw = fr.get("кадры")
    if raw is None and isinstance(fr.get("attrs"), dict):
        raw = fr["attrs"].get("кадры")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [s for s in raw if isinstance(s, dict)]


def shots_coverage_ops_reason(
    ops: list[Any],
    frames: list[dict[str, Any]],
) -> str | None:
    """Кадры = шаги, что иллюстрируют смысл закадра; без равномерной нарезки."""
    apply_grammar_to_ops(ops, frames)
    repair_shot_vo_ops(ops, frames)
    by_uid_pre = _frames_by_uuid(frames)
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields") or {}
        shots = fields.get("кадры") or fields.get("shots")
        if not isinstance(shots, list):
            continue
        uid = str(op.get("frame_uuid") or "").strip()
        vo = _frame_vo(by_uid_pre.get(uid) or {})
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            chunk = _shot_vo_chunk(shot)
            if vo_chunk_is_dangling(chunk):
                sid = str(shot.get("id") or "")
                return (
                    f"uuid {uid[:8]}: кадр {sid or '?'} обрубок закадра "
                    f"({chunk[-24:]!r})"
                )
        # Чиним НА МЕСТЕ: раньше сюда шла копия списка — удаления/склейки
        # терялись, а перенесённый текст оставался и в источнике (дубли).
        notes = repair_shot_vo_lengths(shots, vo)
        if notes:
            logger.info(
                "repair_shot_vo_lengths uuid={}: {}",
                uid[:8],
                "; ".join(notes[:6]),
            )
    by_uid = _frames_by_uuid(frames)
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields") or {}
        shots = fields.get("кадры") or fields.get("shots")
        if not isinstance(shots, list):
            uid = str(op.get("frame_uuid") or "")
            return f"uuid {uid[:8]}: пустые кадры"
        uid = str(op.get("frame_uuid") or "")
        vo = _frame_vo(by_uid.get(uid) or {})
        bad = shots_grammar_reason(
            [s for s in shots if isinstance(s, dict)], vo, uid
        )
        if bad:
            return bad
        bad_len = _shot_vo_len_reason(
            [s for s in shots if isinstance(s, dict)], vo, uid
        )
        if bad_len:
            return bad_len
    return None


def prompts_ops_reason(
    ops: list[Any],
    frames: list[dict[str, Any]],
) -> str | None:
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


def _shots_step_count_note(frames: list[dict[str, Any]] | None) -> str:
    """Жирная цепь: сказать модели сколько шагов, не схлопывать в 1–5."""
    lines: list[str] = []
    for fr in frames or []:
        if not isinstance(fr, dict):
            continue
        uid = str(fr.get("uuid") or "")[:8]
        if not uid:
            continue
        steps = _frame_action_steps(fr)
        if steps <= 0:
            continue
        lines.append(
            f"uuid {uid}: ≈ {steps} шагов, не схлопывай в 1–5. "
            "Потолок 50 шагов. 26–80 — длина закадра ОДНОГО кадра."
        )
    if not lines:
        return ""
    return "\n" + "\n".join(lines) + "\n"


def _batch_footer(
    batch_i: int,
    split_level: int,
    n: int,
    *,
    footer_kind: str | None = None,
    used_senses: list[str] | None = None,
    used_places: list[str] | None = None,
    frames: list[dict[str, Any]] | None = None,
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
            "объектов {{порядок, изменение, якорь}}. "
            "Один бит = одна мысль или одно важное событие "
            "(умер, женился и т.п.), не 1 слоган на всю ячейку. "
            "изменение: кто/что → что происходит → другие персонажи → год → место. "
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
            "И fields.площадка — зоны (предметы по сторонам: север/юг/запад/"
            "восток/центр, двери с состоянием), проходы (из → в через дверь), "
            "люди в начале. "
            "Не пиши закадр и биты. JSON apply-ops, без прозы.\n"
        )
    if kind in {"shots_coverage", "shots"}:
        expect_lines: list[str] = []
        for fr in frames or []:
            if not isinstance(fr, dict):
                continue
            uid = str(fr.get("uuid") or "").strip()
            expected = _frame_expected_shots(fr)
            steps = _frame_action_steps(fr)
            vo_n = _frame_vo_chars(fr)
            if uid and expected > 0:
                expect_lines.append(
                    f"- uuid {uid[:8]}: СДЕЛАЙ ≈{expected} кадров "
                    f"(VO-capacity; steps={steps}, VO≈{vo_n} симв.) — "
                    f"не 1–5 обобщений; каждый закадр 26–80"
                )
        expect_block = ""
        if expect_lines:
            expect_block = (
                "Ожидаемое число кадров по uuid (VO-capacity expected N):\n"
                + "\n".join(expect_lines[:12])
                + "\n"
            )
        solo_note = ""
        if n == 1:
            solo_note = (
                "СОЛО-ЯЧЕЙКА: это единственный uuid в пачке — не схлопывай в 1–5. "
                "Верни ≈N кадров по VO-capacity выше.\n"
            )
        return (
            f"\n# BATCH call={batch_i} split={split_level} "
            f"(кадры-шаги, пачка {batch_i}, "
            f"{SCRIPT_FRAMES_QC_PARALLEL_BATCHES} параллельно)\n"
            f"В db_frames.json только этот кусок: {n} ячеек закадра.\n"
            "Верни ops ровно по каждому uuid: fields.кадры.\n"
            "Один кадр = один видимый шаг действия + кусок закадра. "
            "Закадр режь ТОЛЬКО по концу предложения (. ! ? …): кусок = одно или несколько ЦЕЛЫХ предложений, цель 26–80, длинное предложение — целиком в один кадр, остальные кадры шага пустые. Внутри предложения нет ни реплики, ни пустого кадра. Диалог ставь только на стыке предложений закадра: весь закадр → диалог или диалог → закадр. Заметил/увидел/обнаружил → следующим кадром вставка ДЕТАЛЬ/КРУПНЫЙ того, на что он смотрит (POV), без людей. Поза переходит в следующий кадр: сидит/присел/лежит — так и пиши в промте, пока не встал. Персонаж из базы — всегда с id из `персонажи` ячейки (вариант образа: c05/c06 вместо c01), `говорит` = id; роли без id (Следователь, Задержанный) не подменяй персонажами базы. Вышел из здания — дальше снаружи; снова внутри только через кадр входа. "
            "Кроме кадров с репликой (поле `реплика` не пустое): там `закадр` пустой, кусок закадра идёт на соседний кадр без реплики. "
            "Диалог: установочный ОБЩИЙ до первой реплики; каждый кадр реплики — `с плеча` через плечо слушающего на говорящего, у соседних реплик разных людей один план (обычно СРЕДНЕ-КРУПНЫЙ); монолог — КРУПНЫЙ фронт.\n"
            "Поле объект: место|тело|двое|предмет|лицо|взгляд. "
            "Камеру (план, линза_мм, ракурс, движение) можно не писать — "
            "код подставит из таблицы. "
            "Не вали весь VO в один кадр, но предложение не дроби никогда. "
            "Склейка непустых закадр = весь voiceover_text. "
            "СТАРТ/КОНЕЦ только если точность жеста обязательна "
            "(открыл/положил/достал улику); иначе одно действие-процесс. "
            "По площадке ячейки: зона, камера {где, смотрит}, люди "
            "{кто, где, лицом, движется}, меняет {предмет, состояние} — "
            "лево/право и раскладку посчитает код. "
            "Не пиши биты и главное_действие. JSON apply-ops, без прозы.\n"
            f"{_shots_step_count_note(frames)}"
        )

    if kind in {"shots_qc", "qc_shots"}:
        return (
            f"\n# BATCH call={batch_i} split={split_level} "
            f"(QC полей кадров, пачка {batch_i})\n"
            f"В db_frames.json только этот кусок: {n} ячеек закадра.\n"
            "Чини только нарушителей: fields.кадры. "
            "Проверь: картина кадра = смысл его закадра; склейка непустых = весь текст; "
            "число кадров ≈ шагам действие; закадр только целыми предложениями (цель 26–80), не рвать предложение; заметил → вставка объекта; поза и id персонажа в промте; нет скачков внутри/снаружи; перебивка 10–30 или короткий эмо/реакция (можно пустой); "
            "нет микрожестов без закадра; СТАРТ/КОНЕЦ только у точных жестов; "
            "объект enum, parent_id на одном месте, зоны из площадки, "
            "одну сторону оси у двоих, направление бега по экрану. "
            "Не пиши промт_картинки и промт_видео. Пустые ops = ок, если всё чисто. "
            "JSON apply-ops, без прозы.\n"
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


@dataclass
class PassCtx:
    """Для логов: те же ``[#проект] node call L`` что и раньше."""

    project_id: int
    node_key: str
    call: int
    level: int


@dataclass
class CodePassesResult:
    ops: list[dict[str, Any]]
    entries: list[dict[str, Any]] = field(default_factory=list)


class PassStopError(RuntimeError):
    """Проверка кода остановила пачку. Текст — как раньше; + дневник до остановки."""

    def __init__(self, message: str, *, rule: str, diary: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.rule = rule
        self.diary = diary


SHOTS_GATE_KINDS = frozenset({"shots_coverage", "shots"})
SHOTS_GATE_RULE = "R-SHOTS-GATE"


class ShotsGateError(RuntimeError):
    """Ответ «Шотов» не прошёл гейт (промты / закадр) — пачку не пишем."""

    def __init__(self, message: str, *, problems: list[tuple[str, str]], bad_chunk: list | None = None) -> None:
        super().__init__(message)
        self.problems = problems
        self.bad_chunk = bad_chunk


class OutrightJunkError(RuntimeError):
    """В ответе нет самой работы: пустые кадры, пустая площадка или пустые биты."""


_SHOT_PAYLOAD_KEYS = (
    "действие",
    "action",
    "закадр",
    "voiceover_text",
    "промт_картинки",
    "image_prompt",
    "промт_старт",
    "промт_видео",
    "реплика",
    "объект",
    "план",
)


def _shot_has_payload(shot: dict[str, Any]) -> bool:
    return any(str(shot.get(key) or "").strip() for key in _SHOT_PAYLOAD_KEYS)


def outright_junk_reason(
    op: dict[str, Any],
    kind: str,
    frame: dict[str, Any] | None = None,
) -> str | None:
    """Выкинуть ячейку можно только если в ней нет самой работы.

    Кривой формат, короткий закадр, нет одного промта — не брак, это пишется.
    """
    fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
    if kind in {"shots_coverage", "shots"}:
        raw = fields.get("кадры")
        if not isinstance(raw, list):
            raw = fields.get("shots")
        shots = [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []
        if not any(_shot_has_payload(s) for s in shots):
            return "нет кадров"
        return None
    if kind in {"shots_qc", "qc_shots"}:
        raw = fields.get("кадры") if "кадры" in fields else fields.get("shots")
        if isinstance(raw, list):
            shots = [s for s in raw if isinstance(s, dict)]
            if not any(_shot_has_payload(s) for s in shots):
                return "нет кадров"
        return None
    if kind in {"action_chain", "main_action"}:
        text = str(
            fields.get("главное_действие") or fields.get("main_action") or ""
        ).strip()
        plan = (
            fields.get("площадка")
            or fields.get("план_площадки")
            or fields.get("space_plan")
        )
        has_plan = (isinstance(plan, (dict, list)) and bool(plan)) or (
            isinstance(plan, str) and bool(plan.strip())
        )
        changes = fields.get("меняет")
        has_changes = (isinstance(changes, (dict, list)) and bool(changes)) or (
            isinstance(changes, str) and bool(changes.strip())
        )
        if not text and not has_plan and not has_changes:
            return "пустой ответ"
        return None
    if kind in {"bits", "script_beats"}:
        raw = fields.get("биты") if "биты" in fields else fields.get("bits")
        vo = _frame_vo(frame) if frame is not None else "x"
        if not vo and raw == []:
            return None
        if not isinstance(raw, list) or not raw:
            return "пустые биты"
        return None
    return None


def _is_empty_parent_id(value: Any) -> bool:
    return value is None or str(value).strip().lower() in {"", "null", "none"}


def shots_reply_gate_problems(
    ops: list[Any],
    frames: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str]]:
    """Гейт ответа «Шотов» после всех проходов кода: (uuid, что не так).

    Главный кадр ячейки (первый с пустым ``parent_id``) — нужен
    ``промт_картинки``. Дочерний (``parent_id`` есть) — нужен
    ``промт_старт``. Главный следующей сцены в той же ячейке (S2-K1…,
    ``parent_id`` пустой, не первый) — ``промт_старт`` или
    ``промт_картинки``.

    Закадр (промт §4): пустой закадр у кадра — норма (пауза между кусками,
    перебивка, стык сцен). Гейт требует, чтобы склейка НЕпустых кусков
    ячейки = закадр ячейки (пробелы нормализуются), кусок не длиннее 80 и
    не короче 10 (короче 10 — только перебивка). ``frames`` — кадры пачки
    (вход), без них сверка склейки пропускается.
    Op без кадров — тоже брак. Пустой список = пачку можно писать.
    """
    by_uid = _frames_by_uuid(frames or [])
    problems: list[tuple[str, str]] = []
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        uid = str(op.get("frame_uuid") or "").strip()
        fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
        raw = fields.get("кадры")
        if not isinstance(raw, list):
            raw = fields.get("shots")
        shots = [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []
        if not shots:
            problems.append((uid, "нет кадров"))
            continue
        master = next(
            (i for i, s in enumerate(shots) if _is_empty_parent_id(s.get("parent_id"))),
            0,
        )
        cell_vo = " ".join(_frame_vo(by_uid.get(uid)).split()) if uid in by_uid else ""
        cell_n = _vo_visible_len(cell_vo)
        pieces: list[str] = []
        for i, shot in enumerate(shots):
            sid = str(shot.get("id") or shot.get("id_shot") or f"кадр {i + 1}").strip()
            img = str(shot.get("промт_картинки") or shot.get("image_prompt") or "").strip()
            start = str(shot.get("промт_старт") or "").strip()
            if i == master:
                if not img:
                    problems.append((uid, f"{sid}: главный кадр без промт_картинки"))
            elif _is_empty_parent_id(shot.get("parent_id")):
                if not (start or img):
                    problems.append((uid, f"{sid}: главный кадр сцены без промт_старт"))
            elif not start:
                problems.append((uid, f"{sid}: дочерний кадр без промт_старт"))
            chunk = " ".join(_shot_vo_chunk(shot).split())
            if not chunk:
                continue  # пустой закадр — норма
            pieces.append(chunk)
            n = _vo_visible_len(chunk)
            whole = vo_piece_ends_sentence(chunk)
            if n > SHOT_VO_MAX_CHARS and not whole:
                problems.append((uid, f"{sid}: закадр {n} симв. > {SHOT_VO_MAX_CHARS}"))
            elif (
                not whole
                and n < SHOT_VO_CUTAWAY_MIN
                and cell_n >= SHOT_VO_CUTAWAY_MIN
                and not is_cutaway_shot(shot)
            ):
                problems.append(
                    (uid, f"{sid}: закадр {n} симв. < {SHOT_VO_CUTAWAY_MIN} (не перебивка)")
                )
        if uid in by_uid:
            glued = " ".join(pieces)
            if cell_vo and not glued:
                problems.append((uid, "закадр ячейки не разложен ни в один кадр"))
            elif glued != cell_vo:
                problems.append((uid, "склейка закадра кадров ≠ закадр ячейки"))
        if uid in by_uid:
            problems.extend((uid, w) for w in shots_dialogue_problems(shots, by_uid[uid].get("речь")))
        problems.extend((uid, w) for w in vo_sentence_problems(shots, cell_vo))
        problems.extend((uid, w) for w in shots_continuity_problems(shots))
        cards = _GATE_CHARACTERS.get()
        if cards:
            problems.extend((uid, w) for w in character_id_problems(shots, cards, (by_uid.get(uid) or {}).get("персонажи")))
    return problems


import contextvars as _cv

# база персонажей для гейта/фикса id (ставит нода Кадры перед пачками)
_GATE_CHARACTERS: "_cv.ContextVar[list[dict[str, Any]]]" = _cv.ContextVar("_GATE_CHARACTERS", default=[])


def set_shot_characters(cards: list[dict[str, Any]] | None) -> None:
    _GATE_CHARACTERS.set(list(cards or []))


_REPLY_SPLIT = re.compile(r"\s+—\s+(?=[^:.—«»]{1,40}:)")


def scene_replies(speech: Any) -> list[tuple[str, str]]:
    """``речь`` Каркаса → [(кто, текст)] для диалог/монолог, иначе []."""
    if isinstance(speech, str):
        with suppress(json.JSONDecodeError):
            speech = json.loads(speech)
    if not isinstance(speech, dict):
        return []
    if str(speech.get("тип") or "").strip() not in ("диалог", "монолог"):
        return []
    out: list[tuple[str, str]] = []
    for part in _REPLY_SPLIT.split(str(speech.get("текст") or "").strip()):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([^:«»]{1,40}):\s*(.*)$", part)
        out.append((m.group(1).strip(), m.group(2).strip()) if m else ("", part))
    return out


def _reply_norm(text: Any) -> str:
    return " ".join(str(text or "").replace("«", "").replace("»", "").split()).strip().casefold()


def shots_dialogue_problems(shots: list[dict[str, Any]], speech: Any) -> list[str]:
    """Гейт кадров реплик (Кадры §3) — только сообщает, ничего не переписывает.

    Число кадров с ``реплика`` = числу реплик ``речь``; каждая дословно, ровно раз,
    по порядку; один говорящий на кадр; кадр реплики — ``с плеча`` или КРУПНЫЙ
    фронт; ОБЩИЙ до первой реплики; реплики разных людей подряд — встречная пара
    (оба ``с плеча``, один план).
    """
    from app.services.scene_plan import named_angle_token

    reps = scene_replies(speech)
    if not reps:
        return []
    out: list[str] = []
    rf = [(i, s) for i, s in enumerate(shots) if str(s.get("реплика") or "").strip()]

    def sid(s: dict[str, Any], i: int) -> str:
        return str(s.get("id") or f"кадр {i + 1}")

    if len(rf) != len(reps):
        out.append(f"реплик в речи {len(reps)}, кадров с репликой {len(rf)}")
    got = [_reply_norm(s.get("реплика")) for _, s in rf]
    want = [_reply_norm(t) for _, t in reps]
    for w, (who, t) in zip(want, reps):
        if got.count(w) != 1:
            out.append(f"реплика «{t[:40]}» ({who}): в кадрах {got.count(w)} раз, нужно ровно 1 дословно")
    if got != want and sorted(got) == sorted(want):
        out.append("реплики в кадрах не в порядке речи")
    for i, s in rf:
        who = str(s.get("говорит") or "").strip()
        if not who:
            out.append(f"{sid(s, i)}: реплика без «говорит»")
        elif re.search(r",|\s+и\s+|/", who):
            out.append(f"{sid(s, i)}: два говорящих «{who}»")
        if _REPLY_SPLIT.search(str(s.get("реплика") or "")):
            out.append(f"{sid(s, i)}: две реплики в одном кадре")
        ang = named_angle_token(s.get("ракурс"))
        plan = str(s.get("план") or "").strip().upper()
        if not (ang == "с плеча" or (plan == "КРУПНЫЙ" and ang == "фронт")):
            out.append(f"{sid(s, i)}: кадр реплики {plan}/{s.get('ракурс')} — нужен «с плеча» или КРУПНЫЙ фронт")
    if rf:
        first = rf[0][0]
        if not any(str(s.get("план") or "").strip().upper() == "ОБЩИЙ" for s in shots[:first]):
            out.append(f"нет установочного ОБЩИЙ до первой реплики ({sid(rf[0][1], first)})")
    for (i, a), (j, b) in zip(rf, rf[1:]):
        wa = str(a.get("говорит") or "").strip().casefold()
        wb = str(b.get("говорит") or "").strip().casefold()
        if wa and wb and wa != wb:
            same_plan = str(a.get("план") or "").strip().upper() == str(b.get("план") or "").strip().upper()
            pair = named_angle_token(a.get("ракурс")) == named_angle_token(b.get("ракурс")) == "с плеча"
            if not (pair and same_plan):
                out.append(f"{sid(a, i)}→{sid(b, j)}: разные говорящие, не встречная пара (оба «с плеча», один план)")
    return out


def _is_reply_shot(shot: dict[str, Any]) -> bool:
    return bool(str(shot.get("реплика") or "").strip())


def rebalance_shot_vo(shots: list[Any]) -> list[str]:
    """Детерминированно, без новых слов: (1) закадр с кадра реплики — на соседний кадр без реплики;
    (2) кусок >80 — режем по границе слова, хвост/голову на соседний кадр. Только если склейка
    непустых кусков не меняется (между донором и приёмником нет непустого закадра) и кусок ≤80.
    Иначе оставляем как есть (неизбежно) — гейт сообщит."""
    rows = [s for s in shots if isinstance(s, dict)]
    notes: list[str] = []

    def vo(s: dict[str, Any]) -> str:
        return " ".join(str(s.get("закадр") or "").split())

    def glued() -> str:
        return " ".join(vo(s) for s in rows if vo(s))

    before = glued()

    def neighbours(i: int) -> list[tuple[int, bool]]:
        """(j, before_i) — ближайшие кадры без реплики слева/справа; между ними только пустой закадр."""
        out = []
        for step in (-1, 1):
            j = i + step
            while 0 <= j < len(rows):
                if not _is_reply_shot(rows[j]):
                    out.append((j, step < 0))
                    break
                if vo(rows[j]):
                    break
                j += step
        return out

    # 0) перелив по кадрам без реплики: куски реплик — в предыдущий такой кадр, >80 — слова
    #    переливаются в следующий/предыдущий кадр без реплики (порядок слов тот же)
    slots = [i for i, s in enumerate(rows) if not _is_reply_shot(s)]
    need = any(_is_reply_shot(s) and vo(s) for s in rows) or any(
        _vo_visible_len(vo(s)) > SHOT_VO_MAX_CHARS for s in rows
    )
    if need and slots:
        texts: list[list[str]] = [[] for _ in slots]
        cur = 0
        for i, s in enumerate(rows):
            if i in slots:
                cur = slots.index(i)
            texts[cur].extend(vo(s).split())

        def ln(ws: list[str]) -> int:
            return _vo_visible_len(" ".join(ws))

        for a in range(len(texts) - 1):
            while ln(texts[a]) > SHOT_VO_MAX_CHARS and len(texts[a]) > 1:
                texts[a + 1].insert(0, texts[a].pop())
        for a in range(len(texts) - 1, 0, -1):
            while ln(texts[a]) > SHOT_VO_MAX_CHARS and len(texts[a]) > 1:
                texts[a - 1].append(texts[a].pop(0))
        if all(ln(t) <= SHOT_VO_MAX_CHARS for t in texts):
            for i, s in enumerate(rows):
                if i not in slots and vo(s):
                    notes.append(f"{s.get('id')}: закадр снят с кадра реплики")
                    s["закадр"] = ""
            for a, i in enumerate(slots):
                new = " ".join(texts[a])
                if new != vo(rows[i]):
                    rows[i]["закадр"] = new
                    notes.append(f"{rows[i].get('id')}: закадр перелит")
    # 1) реплика без закадра
    for i, s in enumerate(rows):
        if not (_is_reply_shot(s) and vo(s)):
            continue
        piece = vo(s)
        for j, left in neighbours(i):
            tgt = vo(rows[j])
            new = f"{tgt} {piece}".strip() if left else f"{piece} {tgt}".strip()
            if _vo_visible_len(new) <= SHOT_VO_MAX_CHARS:
                rows[j]["закадр"] = new
                s["закадр"] = ""
                notes.append(f"{s.get('id')}: закадр снят с кадра реплики → {rows[j].get('id')}")
                break
    # 2) >80 — по границе слова в соседний кадр
    for i, s in enumerate(rows):
        piece = vo(s)
        if _vo_visible_len(piece) <= SHOT_VO_MAX_CHARS:
            continue
        words = piece.split()
        done = False
        cands = []
        for j in (i + 1, i - 1):
            if 0 <= j < len(rows):
                cands.append(j)
        for j in sorted(cands, key=lambda j: (_is_reply_shot(rows[j]), -1 if j > i else 0)):
            tgt = vo(rows[j])
            for cut in range(len(words) - 1, 0, -1):
                head, tail = " ".join(words[:cut]), " ".join(words[cut:])
                if j > i:
                    keep, move, new = head, tail, f"{tail} {tgt}".strip()
                else:
                    keep, move, new = tail, head, f"{tgt} {head}".strip()
                if _vo_visible_len(keep) <= SHOT_VO_MAX_CHARS and _vo_visible_len(new) <= SHOT_VO_MAX_CHARS:
                    s["закадр"], rows[j]["закадр"] = keep, new
                    notes.append(f"{s.get('id')}: закадр {_vo_visible_len(piece)}>80 разрезан → {rows[j].get('id')}")
                    done = True
                    break
            if done:
                break
    if glued() != before:  # страховка: склейка обязана остаться дословной
        raise RuntimeError("rebalance_shot_vo изменил склейку закадра")
    # R-VO-SENTENCE: после переливов по словам — собрать куски в целые предложения
    notes.extend(reflow_vo_by_sentences(rows, before))
    return notes


def rebalance_shot_vo_ops(ops: list[Any]) -> int:
    n = 0
    for op in ops or []:
        fields = op.get("fields") if isinstance(op, dict) else None
        shots = (fields or {}).get("кадры") if isinstance(fields, dict) else None
        if isinstance(shots, list):
            snap = json.loads(json.dumps(shots, ensure_ascii=False))
            try:
                n += len(rebalance_shot_vo(shots))
            except RuntimeError:
                shots[:] = snap
    return n
def pass_stop_rule(exc: BaseException | None) -> str | None:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        rule = getattr(exc, "rule", None)
        if isinstance(exc, PassStopError) and isinstance(rule, str) and rule:
            return rule
        exc = exc.__cause__ or exc.__context__
    return None


_RULE_DIARY_RULES_MAX = 12
_RULE_DIARY_NOTE_MAX = 200


def rule_diary_payload(
    entries: list[dict[str, Any]] | None,
    *,
    call: int,
    level: int,
    stop_exc: BaseException | None = None,
) -> dict[str, Any]:
    import re as _re

    counts: dict[str, int] = {}
    rules: dict[str, int] = {}
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        kind = str(e.get("kind") or "info")[:16]
        counts[kind] = counts.get(kind, 0) + 1
        rule = str(e.get("rule") or "")[:40]
        if rule:
            rules[rule] = rules.get(rule, 0) + 1
    top = dict(sorted(rules.items(), key=lambda kv: (-kv[1], kv[0]))[:_RULE_DIARY_RULES_MAX])
    stop_rule = pass_stop_rule(stop_exc)
    if stop_rule is None and stop_exc is not None:
        raw = getattr(stop_exc, "rule", None)
        if isinstance(raw, str) and _re.fullmatch(r"R-[A-Z0-9-]{1,40}", raw):
            stop_rule = raw
    payload: dict[str, Any] = {
        "call": int(call), "level": int(level), "counts": counts, "rules": top,
        "stop_rule": stop_rule,
    }
    if stop_exc is not None:
        payload["note"] = " ".join(str(stop_exc).split())[:_RULE_DIARY_NOTE_MAX]
    return payload


async def _emit_rule_diary(
    entries: list[dict[str, Any]] | None,
    *,
    call: int,
    level: int,
    stop_exc: BaseException | None = None,
) -> None:
    if not entries and stop_exc is None:
        return
    try:
        from app.services.observation import record_fact

        await record_fact("rule_diary", rule_diary_payload(
            entries, call=call, level=level, stop_exc=stop_exc))
    except Exception:
        logger.debug("rule_diary fact skipped", exc_info=True)


def _shot_rows(fields: dict[str, Any]) -> list[dict[str, Any]]:
    raw = fields.get("кадры")
    if not isinstance(raw, list):
        raw = fields.get("shots")
    if not isinstance(raw, list):
        return []
    return [dict(s) if isinstance(s, dict) else {"значение": s} for s in raw]


def _frame_shot_rows(frame: dict[str, Any]) -> list[dict[str, Any]]:
    raw = frame.get("кадры")
    if not isinstance(raw, list):
        attrs = frame.get("attrs")
        raw = attrs.get("кадры") if isinstance(attrs, dict) else None
    if not isinstance(raw, list):
        return []
    return [dict(s) if isinstance(s, dict) else {"значение": s} for s in raw]


def coalesce_cell_shot_ops(
    ops: list[Any],
    frames: list[dict[str, Any]] | None = None,
    *,
    patch: bool,
) -> list[dict[str, Any]]:
    """Несколько ops на один uuid не затирают кадры[].

    Обычная нода кадров склеивает списки подряд. QC (``patch``) правит
    кадр по id поверх списка, который уже есть у ячейки.
    """
    existing = {
        str(fr.get("uuid") or "").strip(): _frame_shot_rows(fr)
        for fr in frames or []
        if str(fr.get("uuid") or "").strip()
    }
    order: list[str] = []
    merged: dict[str, dict[str, Any]] = {}
    for op in ops:
        if not isinstance(op, dict):
            continue
        uid = str(op.get("frame_uuid") or "").strip()
        if not uid:
            continue
        fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
        incoming = _shot_rows(fields)
        if uid not in merged:
            order.append(uid)
            merged[uid] = {
                "fields": {},
                "shots": [dict(s) for s in existing.get(uid, [])] if patch else [],
            }
        slot = merged[uid]
        for key, val in fields.items():
            if key in {"кадры", "shots"}:
                continue
            slot["fields"][key] = val
        if patch:
            index = {
                str(s.get("id") or ""): i
                for i, s in enumerate(slot["shots"])
                if str(s.get("id") or "")
            }
            for shot in incoming:
                sid = str(shot.get("id") or "")
                if sid and sid in index:
                    slot["shots"][index[sid]] = shot
            # QC вернул меньше хороших: не держим пустые оболочки от старого списка.
            if incoming:
                from app.services.scene_shot_grammar import is_cutaway_shot

                existing_empty = sum(
                    1
                    for s in slot["shots"]
                    if isinstance(s, dict)
                    and not str(s.get("закадр") or "").strip()
                    and not is_cutaway_shot(s)
                )
                if existing_empty and len(incoming) < len(slot["shots"]):
                    # REPLACE: QC-список — источник истины
                    slot["shots"] = [dict(s) for s in incoming]
                else:
                    slot["shots"] = [
                        s
                        for s in slot["shots"]
                        if not isinstance(s, dict)
                        or str(s.get("закадр") or "").strip()
                        or is_cutaway_shot(s)
                    ]
        else:
            slot["shots"].extend(incoming)
    out: list[dict[str, Any]] = []
    for uid in order:
        slot = merged[uid]
        fields = dict(slot["fields"])
        if slot["shots"]:
            fields["кадры"] = slot["shots"]
        out.append({"frame_uuid": uid, "fields": fields})
    return out


def run_code_passes(
    ops: list[Any],
    chunk: list[dict[str, Any]],
    *,
    kind: str,
    all_frames: list[dict[str, Any]] | None = None,
    ctx: PassCtx | None = None,
) -> CodePassesResult:
    """Все правки программы после ответа GPT, по порядку. Пишет дневник.

    Один и тот же код для живой ноды и для ``scripts/replay_node.py``.
    """
    from app.services.db_apply import remap_frame_number_uuids, repair_near_miss_frame_uuids
    from app.services.node_rules import PASS_RULES, PLAN_FIELD_RULES, PLAN_ISSUE_RULES

    ctx = ctx or PassCtx(0, "", 0, 1)
    project_id, node_key, my_i, level = ctx.project_id, ctx.node_key, ctx.call, ctx.level
    entries: list[dict[str, Any]] = []
    ops = list(ops or [])

    known_list = [
        str(fr.get("uuid") or "").strip()
        for fr in chunk
        if str(fr.get("uuid") or "").strip()
    ]

    def _traced(name: str, fn: Callable[[], Any]) -> Any:
        before = snapshot(ops)
        out = fn()
        entries.extend(
            changes_to_entries(diff_ops(before, ops), rule=PASS_RULES[name], pass_name=name)
        )
        return out

    def _warn(name: str, reason: str) -> None:
        entries.append(
            entry(
                rule=PASS_RULES[name],
                pass_name=name,
                kind="warn",
                frame_uuid=uuid_in_text(reason, known_list),
                note=reason,
            )
        )

    def _stop(name: str, reason: str) -> PassStopError:
        return PassStopError(
            f"excel_gpt node={node_key}: L{level} call {my_i} {reason}",
            rule=PASS_RULES[name],
            diary=list(entries),
        )

    # Автопочинка UUID: remap по номеру кадра + repair near-miss (опечатки hex)
    known = set(known_list)
    num_to_uuid: dict[int, str] = {}
    for fr in chunk:
        n = fr.get("number") or fr.get("номер")
        u = str(fr.get("uuid") or "").strip()
        if n is not None and u:
            with suppress(ValueError, TypeError):
                num_to_uuid[int(n)] = u
    uids_before = [
        str(op.get("frame_uuid") or "").strip() if isinstance(op, dict) else None
        for op in ops
    ]
    if num_to_uuid:
        remap_frame_number_uuids(ops, num_to_uuid)
    if known_list:
        repair_near_miss_frame_uuids(ops, known_list, max_distance=2)
    for was, op in zip(uids_before, ops, strict=False):
        now = str(op.get("frame_uuid") or "").strip() if isinstance(op, dict) else None
        if was is not None and now != was:
            entries.append(
                entry(
                    rule=PASS_RULES["uuid"],
                    pass_name="uuid",
                    frame_uuid=now or "",
                    field_name="frame_uuid",
                    before=was,
                    after=now,
                )
            )

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
            entries.append(
                entry(
                    rule=PASS_RULES["uuid"],
                    pass_name="uuid",
                    kind="warn",
                    frame_uuid=uid,
                    note="uuid не из этой пачки — ops выброшен",
                )
            )
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
    if kind in {"bits", "script_beats"}:
        repaired = _traced("bits_repair", lambda: repair_bits_ops(ops, chunk))
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
            _warn("bits_check", bad_bits)
            logger.warning(
                "[#{}] apply_ops batched node={!r}: call {} L{} "
                "биты: замечание, пишем: {}",
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
            raise _stop("analytics_check", collapsed)
    if kind in {"action_chain", "main_action"}:
        _traced("action_format", lambda: auto_repair_action_chain_ops(ops, chunk))
        n_plan = _traced("action_plan", lambda: normalize_action_plan_ops(ops))
        if n_plan < len(ops):
            logger.info(
                "[#{}] apply_ops batched node={!r}: call {} площадка "
                "{}/{} (остальное выведет код на кадрах)",
                project_id,
                node_key,
                my_i,
                n_plan,
                len(ops),
            )
        repaired = _traced("action_repair", lambda: repair_action_ops(ops))
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
            _warn("action_check", bad_action)
            logger.warning(
                "[#{}] apply_ops batched node={!r}: call {} L{} "
                "действие: замечание, пишем: {}",
                project_id,
                node_key,
                my_i,
                level,
                bad_action,
            )
    if kind in _SHOTS_FOOTER_KINDS:
        before_ops = len(ops)
        ops = coalesce_cell_shot_ops(
            ops,
            chunk,
            patch=kind in {"shots_qc", "qc_shots"},
        )
        if len(ops) != before_ops:
            logger.info(
                "[#{}] apply_ops batched node={!r}: call {} склеил "
                "ops одной ячейки {} → {} (кадры не затирают друг друга)",
                project_id,
                node_key,
                my_i,
                before_ops,
                len(ops),
            )
        repaired_parents = _traced("shot_parent", lambda: repair_same_place_shot_parents(ops))
        if repaired_parents:
            logger.info(
                "[#{}] apply_ops batched node={!r}: parent_id проставлен "
                "по таблице у {} кадров (GPT оставил null)",
                project_id,
                node_key,
                repaired_parents,
            )
        before = _count_shots(ops)
        _traced("shot_grammar", lambda: apply_grammar_to_ops(ops, chunk))
        filled = max(0, _count_shots(ops) - before)
        if filled:
            logger.info(
                "[#{}] apply_ops batched node={!r}: грамматика кадров "
                "дописала +{} шагов из главное_действие",
                project_id,
                node_key,
                filled,
            )
        _traced("shot_parent_again", lambda: repair_same_place_shot_parents(ops))
        repaired_vo = _traced("shot_vo_fill", lambda: repair_shot_vo_ops(ops, chunk))
        plan_before = snapshot(ops)
        plan_issues: list[dict[str, Any]] = []
        plan_decisions: list[dict[str, Any]] = []
        plan_fixed, plan_hard = apply_scene_plan_ops(
            ops,
            all_frames or chunk,
            issues_out=plan_issues,
            decisions_out=plan_decisions,
        )
        entries.extend(
            _scene_plan_entries(
                diff_ops(plan_before, ops),
                plan_issues,
                plan_decisions,
                pass_rule=PASS_RULES["scene_plan"],
                field_rules=PLAN_FIELD_RULES,
                issue_rules=PLAN_ISSUE_RULES,
            )
        )
        if plan_fixed:
            logger.info(
                "[#{}] apply_ops batched node={!r}: call {} площадка "
                "починила ×{}: {}",
                project_id,
                node_key,
                my_i,
                len(plan_fixed),
                "; ".join(plan_fixed[:6]),
            )
        if plan_hard:
            logger.warning(
                "[#{}] apply_ops batched node={!r}: call {} L{} "
                "площадка: {}",
                project_id,
                node_key,
                my_i,
                level,
                "; ".join(plan_hard[:6]),
            )
        if repaired_vo:
            logger.info(
                "[#{}] apply_ops batched node={!r}: call {} "
                "дописал пустой закадр ×{}",
                project_id,
                node_key,
                my_i,
                repaired_vo,
            )
        # Soft underproduction WARN (GPT≥1 < expected): не stop — уже оставили кадры.
        if kind not in {"shots_qc", "qc_shots"}:
            for op in ops:
                if not isinstance(op, dict):
                    continue
                fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
                warn = str(
                    op.pop("_shots_under_warn", None)
                    or fields.pop("_shots_under_warn", None)
                    or ""
                ).strip()
                if warn:
                    _warn("shots_check", warn)
                    logger.warning(
                        "[#{}] apply_ops batched node={!r}: call {} L{} {}",
                        project_id,
                        node_key,
                        my_i,
                        level,
                        warn,
                    )
        # Неполный GPT → без молчаливого fill. Одна ячейка / все плохие → stop.
        # Несколько ячеек и часть ок → оставляем хорошие, плохие уйдут в missing/retry.
        if kind not in {"shots_qc", "qc_shots"}:
            incomplete_notes: list[str] = []
            incomplete_uids: list[str] = []
            for op in ops:
                if not isinstance(op, dict):
                    continue
                fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
                note = str(fields.pop("_shots_incomplete", None) or "").strip()
                if note:
                    uid = str(op.get("frame_uuid") or "").strip()
                    incomplete_notes.append(note)
                    if uid:
                        incomplete_uids.append(uid)
            if incomplete_notes:
                bad = set(incomplete_uids)
                good_ops = [
                    op
                    for op in ops
                    if isinstance(op, dict)
                    and str(op.get("frame_uuid") or "").strip() not in bad
                ]
                if good_ops and len(chunk) > 1 and bad:
                    ops = good_ops
                    for note in incomplete_notes:
                        _warn(
                            "shots_check",
                            f"isolate incomplete (retry отдельно): {note}",
                        )
                    logger.warning(
                        "[#{}] apply_ops batched node={!r}: call {} L{} "
                        "неполные uuid {} — пишем остальные, добор отдельно",
                        project_id,
                        node_key,
                        my_i,
                        level,
                        ",".join(u[:8] for u in incomplete_uids[:8]),
                    )
                else:
                    raise _stop("shots_check", incomplete_notes[0])
        else:
            for op in ops:
                if isinstance(op, dict):
                    op.pop("_shots_under_warn", None)
                    if isinstance(op.get("fields"), dict):
                        op["fields"].pop("_shots_incomplete", None)
                        op["fields"].pop("_shots_under_warn", None)
        # shots_coverage_ops_reason сам ещё раз гоняет грамматику — это тоже правка.
        bad_shots = _traced("shots_check", lambda: shots_coverage_ops_reason(ops, chunk))
        # shots_coverage снова зовёт apply_grammar — снимаем internal markers,
        # иначе db_apply падает на неизвестном поле _shots_under_warn.
        for op in ops:
            if not isinstance(op, dict):
                continue
            # soft WARNs, которые grammar повесил повторно на op (до strip)
            warn2 = str(op.pop("_shots_under_warn", None) or "").strip()
            fields = op.get("fields") if isinstance(op.get("fields"), dict) else None
            if isinstance(fields, dict):
                if not warn2:
                    warn2 = str(fields.pop("_shots_under_warn", None) or "").strip()
                else:
                    fields.pop("_shots_under_warn", None)
                # incomplete уже обработан выше; не тащим в DB
                fields.pop("_shots_incomplete", None)
            if warn2:
                _warn("shots_check", warn2)
                logger.warning(
                    "[#{}] apply_ops batched node={!r}: call {} L{} {}",
                    project_id,
                    node_key,
                    my_i,
                    level,
                    warn2,
                )
        if bad_shots:
            # Soft: action-step undercount WARN уже не в bad_shots как hard;
            # hard stop только zero/empty catastrophic.
            if ("нужен добор" in bad_shots or "не вернул кадры" in bad_shots) and (
                "WARN underproduction" not in bad_shots
            ):
                # если в тексте всё же soft — не stop
                raise _stop("shots_check", bad_shots)
            _warn("shots_check", bad_shots)
            logger.warning(
                "[#{}] apply_ops batched node={!r}: call {} L{} "
                "кадры: замечание, пишем: {}",
                project_id,
                node_key,
                my_i,
                level,
                bad_shots,
            )
        # coverage/VO-repair может добавить оболочки без раскладки —
        # R-LAYOUT ещё раз после coverage (идемпотентно).
        plan_before2 = snapshot(ops)
        plan_issues2: list[dict[str, Any]] = []
        plan_decisions2: list[dict[str, Any]] = []
        plan_fixed2, plan_hard2 = apply_scene_plan_ops(
            ops,
            all_frames or chunk,
            issues_out=plan_issues2,
            decisions_out=plan_decisions2,
        )
        entries.extend(
            _scene_plan_entries(
                diff_ops(plan_before2, ops),
                plan_issues2,
                plan_decisions2,
                pass_rule=PASS_RULES["scene_plan"],
                field_rules=PLAN_FIELD_RULES,
                issue_rules=PLAN_ISSUE_RULES,
            )
        )
        if plan_fixed2:
            logger.info(
                "[#{}] apply_ops batched node={!r}: call {} "
                "R-LAYOUT после coverage ×{}: {}",
                project_id,
                node_key,
                my_i,
                len(plan_fixed2),
                "; ".join(plan_fixed2[:6]),
            )
        if plan_hard2:
            logger.warning(
                "[#{}] apply_ops batched node={!r}: call {} L{} "
                "площадка после coverage: {}",
                project_id,
                node_key,
                my_i,
                level,
                "; ".join(plan_hard2[:6]),
            )
    if kind == "shots_coverage":
        # закадр: снять с кадров реплик, разрезать >80 в соседний кадр (склейка дословная)
        # R-MERGE-TRIVIAL (2026-10-15): склеить тривиально продолжающиеся кадры
        _traced("shot_vo_fill", lambda: [merge_trivial_shots((o.get("fields") or {}).get("кадры") or []) for o in ops if isinstance(o, dict)])
        _traced("shot_vo_fill", lambda: rebalance_shot_vo_ops(ops))
        # R-WIDE-BEFORE-DIALOG (2026-10-15): общий план перед диалогом
        _traced("shot_vo_fill", lambda: [ensure_wide_before_dialogue((o.get("fields") or {}).get("кадры") or []) for o in ops if isinstance(o, dict)])
        # R-VO-SPREAD (2026-10-15): несколько предложений в одном кадре — разнести по кадрам
        _traced("shot_vo_fill", lambda: [spread_vo_sentences((o.get("fields") or {}).get("кадры") or []) for o in ops if isinstance(o, dict)])
        if _GATE_CHARACTERS.get():
            _ids_by_uid = {str(r.get("uuid") or "").strip(): r.get("персонажи") for r in chunk}
            pass
        # R-REPLY-CAM / R-POSE-CARRY (c62): после всех правок плана/30° — камера реплик по §3, поза в промт
        _traced("shot_vo_fill", lambda: [(normalize_reply_cameras((o.get("fields") or {}).get("кадры") or []), carry_pose((o.get("fields") or {}).get("кадры") or [])) for o in ops if isinstance(o, dict)])
        if _GATE_CHARACTERS.get():
            _traced("shot_vo_fill", lambda: [ensure_character_ids((o.get("fields") or {}).get("кадры") or [], _GATE_CHARACTERS.get(), _ids_by_uid.get(str(o.get("frame_uuid") or "").strip())) for o in ops if isinstance(o, dict)])
    if kind in {"prompts", "img"}:
        bad_prompts = prompts_ops_reason(ops, chunk)
        if bad_prompts:
            raise _stop("prompts_check", bad_prompts)
    return CodePassesResult(ops=ops, entries=entries)


def _count_shots(ops: list[Any]) -> int:
    n = 0
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields") or {}
        shots = fields.get("кадры") or fields.get("shots")
        if isinstance(shots, list):
            n += len(shots)
    return n


def _scene_plan_entries(
    changes: list[Change],
    issues: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    *,
    pass_rule: str,
    field_rules: dict[str, str],
    issue_rules: dict[str, str],
) -> list[dict[str, Any]]:
    """Правки площадки: «что поменялось» + «почему» из заметок площадки."""
    out: list[dict[str, Any]] = []
    for c in changes:
        base = c.field.split(".", 1)[0]
        rule = pass_rule if base == "площадка" else field_rules.get(base, pass_rule)
        out.extend(changes_to_entries([c], rule=rule, pass_name="scene_plan"))
    for it in issues:
        rule = issue_rules.get(it["вид"], pass_rule)
        shot = int(it.get("кадр") or 0) or None
        hits = [
            e
            for e in out
            if e["frame_uuid"] == it["frame_uuid"]
            and e["shot"] == shot
            and e["rule"] == rule
            and not e["note"]
        ]
        if not hits:
            hits = [
                e
                for e in out
                if e["frame_uuid"] == it["frame_uuid"]
                and e["shot"] == shot
                and e["rule"] == pass_rule
                and e["field"] in ("действие", "кадр")
                and not e["note"]
            ][:1]
        for e in hits:
            e["rule"] = rule
            e["note"] = it["текст"]
        if not hits:
            out.append(
                entry(
                    rule=rule,
                    pass_name="scene_plan",
                    kind="fix" if it["исправлено"] else "warn",
                    frame_uuid=it["frame_uuid"],
                    shot=shot,
                    shot_id=it.get("id") or "",
                    note=it["текст"],
                )
            )
    for d in decisions:
        shot = int(d.get("кадр") or 0) or None
        value = "да" if d.get("точность") else "нет"
        hits = [
            e
            for e in out
            if e["frame_uuid"] == d["frame_uuid"]
            and e["shot"] == shot
            and e["field"] == "точность"
        ]
        for e in hits:
            e["note"] = d["почему"]
        if not hits:
            out.append(
                entry(
                    rule=field_rules["точность"],
                    pass_name="scene_plan",
                    kind="info",
                    frame_uuid=d["frame_uuid"],
                    shot=shot,
                    shot_id=d.get("id") or "",
                    field_name="точность",
                    after=value,
                    note=d["почему"],
                )
            )
    return out


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
    shots_gate: bool | None = None,
) -> OperatorApiResult:
    """Один GPT-вызов на пачку. Ошибка внутри пачки → 2, затем 4.

    ``shots_gate`` (по умолчанию — для ``shots_coverage``/``shots``): замечания
    (нет промта, длина закадра, склейка) пишутся в лог, ответ с кадрами
    записывается. Выкидывается только откровенный брак: нет кадров, пустая
    площадка без действия, пустые биты. Такой ответ один раз повторяется,
    второй брак — ячейка пропускается, старые данные остаются.

    ``chunk_size`` — заранее резать pending (script_frames_qc: 8
    отрезков закадра). ``chunk_by_vo_unit`` — пачка = N ячеек закадра
    (родитель + его шоты вместе), не N строк кадров. В полёте не больше
    8 пачек, между отправками несколько секунд. Ошибка одной пачки
    её пропускает и не перезапускает ноду.
    Без chunk_size — ``target_batches`` или все pending.
    """
    from app.services.adaptive_llm_batches import next_split_level, split_in_half

    all_frames = list(db_ctx.get("frames") or [])
    pending = select_frames_for_batches(
        all_frames,
        dense=dense,
        skip_if_field=skip_if_field,
        force_full=force_full,
    )
    # 2026-10-11: one-shot targeted rerun of specific cells (data/_only_cells_<node>.json = [uuid,...]).
    try:
        import json as _j, os as _os
        _oc = _os.path.join("data", f"_only_cells_{node_key}.json")
        if _os.path.exists(_oc):
            _want = set(_j.load(open(_oc, encoding="utf-8")))
            _os.replace(_oc, _oc + ".used")
            pending = [f for f in all_frames if str(f.get("uuid") or "") in _want]
            logger.warning("[#{}] apply_ops only_cells node={}: {} cells", project_id, node_key, len(pending))
    except Exception as _e:  # noqa: BLE001
        logger.warning("only_cells filter failed: {}", _e)
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
    if shots_short_packs(footer_kind):
        packs = split_into_fixed_units(pending, size or SHOTS_UNITS_PER_BATCH)
        if not (parallel_max and int(parallel_max) > 0):
            parallel_max = SHOTS_PARALLEL
        if stagger_sec is None:
            stagger_sec = SHOTS_STAGGER_SEC
    elif n_target:
        packs = split_into_n_packs(pending, n_target)
    elif size and chunk_by_vo_unit:
        packs = split_vo_units(pending, size)
    elif size:
        packs = split_frames(pending, size)
    else:
        packs = [pending]
    # В полёте не больше 8. Между отправками несколько секунд.
    # Разрезанные половины идут через тот же семафор.
    asked = int(parallel_max) if parallel_max and int(parallel_max) > 0 else PACK_IN_FLIGHT_MAX
    wave_n = min(PACK_IN_FLIGHT_MAX, max(1, asked), max(1, len(packs)))
    # Боевой вызов приходит с 5 с. Тест может передать свою короткую паузу.
    if stagger_sec is None:
        delay_s = PACK_SEND_GAP_S
    else:
        delay_s = max(0.0, float(stagger_sec))
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

    recorder = NodeRunRecorder.start(
        project_dir,
        node_key,
        kind=(footer_kind or "").strip().lower(),
        all_frames=all_frames,
    )
    merged_ops: list[dict[str, Any]] = []
    replies: list[str] = []
    last_paths: list[Path] = [ctx_path]
    call_i = 0
    kind_key = (footer_kind or "").strip().lower()
    gate_on = (
        bool(shots_gate) if shots_gate is not None else kind_key in SHOTS_GATE_KINDS
    )
    skipped_packs: list[dict[str, Any]] = []
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
            frames=chunk,
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
        if allow_empty_ops:
            api_kw["allow_empty_ops"] = True
        pack_timeout = pack_call_timeout_s(footer_kind)
        await _acquire_pack_send_slot()
        try:
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
        finally:
            _release_pack_send_slot()
        ops = []
        if isinstance(res.apply_ops, dict):
            ops = list(res.apply_ops.get("ops") or [])

        ops_gpt = snapshot(ops)
        try:
            passes = run_code_passes(
                ops,
                chunk,
                kind=(footer_kind or "").strip().lower(),
                all_frames=all_frames,
                ctx=PassCtx(project_id, node_key, my_i, level),
            )
        except Exception as exc:
            recorder.record_call(
                call=my_i,
                level=level,
                chunk=chunk,
                reply_text=res.reply_text or "",
                ops_gpt=ops_gpt,
                ops_final=None,
                entries=list(getattr(exc, "diary", []) or [])
                + [entry(rule=str(getattr(exc, "rule", "") or ""), pass_name="stop",
                         kind="stop", note=str(exc)[:500])],
                error=str(exc),
            )
            raise
        ops = passes.ops
        by_frame = {
            str(fr.get("uuid") or "").strip(): fr
            for fr in chunk
            if isinstance(fr, dict)
        }
        kept_ops: list[Any] = []
        junk_notes: list[tuple[str, str]] = []
        for op in ops:
            if not isinstance(op, dict):
                continue
            uid = str(op.get("frame_uuid") or "").strip()
            why = outright_junk_reason(op, kind_key, by_frame.get(uid))
            if why:
                junk_notes.append((uid, why))
            else:
                kept_ops.append(op)
        if junk_notes:
            note = "; ".join(f"{u[:8]} {w}" for u, w in junk_notes[:8])
            logger.warning(
                "[#{}] excel_gpt node={}: L{} call {} откровенный брак, "
                "не пишем ({}): {}",
                project_id,
                node_key,
                level,
                my_i,
                len(junk_notes),
                note[:500],
            )
        ops = kept_ops
        gate_problems = shots_reply_gate_problems(ops, chunk) if gate_on and ops else []
        if gate_problems:
            note = "; ".join(f"{u[:8]} {w}" for u, w in gate_problems[:8])
            logger.warning(
                "[#{}] excel_gpt node={}: L{} call {} гейт шотов {}: "
                "{} замечаний, пишем: {}",
                project_id,
                node_key,
                level,
                my_i,
                SHOTS_GATE_RULE,
                len(gate_problems),
                note[:500],
            )
        recorder.record_call(
            call=my_i,
            level=level,
            chunk=chunk,
            reply_text=res.reply_text or "",
            ops_gpt=ops_gpt,
            ops_final=ops,
            entries=passes.entries,
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
                if not res.empty_ops_accepted:
                    raise RuntimeError('QC empty result not accepted by the operator contract')
                from app.services.observation import record_fact, _attempt
                ctx = _attempt.get()
                if ctx and ctx.scope.mode == 'managed' and (ctx.scope.gaps or
                        ctx.scope.execution_id in ctx.observer.failures or
                        ctx.scope.execution_id in ctx.observer.commit_uncertainty):
                    raise RuntimeError('managed QC no-op blocked: mandatory audit unavailable')
                await record_fact('runtime_check', {'check': 'qc_noop', 'performed': True,
                    'status': 'passed', 'empty_ops_allowed': True, 'response_complete': True,
                    'required_checks': ['strict_json_ops_array', 'completed_response', 'trusted_empty_ops_policy'],
                    'semantic_quality_verified': False},
                    request_id=(getattr(res, 'request_id', None) or (ctx.last_request_id if ctx else None)))
                if ctx and ctx.scope.mode == 'managed' and (ctx.scope.gaps or
                        ctx.scope.execution_id in ctx.observer.failures or
                        ctx.scope.execution_id in ctx.observer.commit_uncertainty):
                    raise RuntimeError('managed QC no-op blocked: mandatory audit unavailable')
                async with meta_lock:
                    last_paths = list(res.output_paths or []) or [batch_path]
                    replies.append(res.reply_text or '')
                return []
            why = "; ".join(f"{u[:8]} {w}" for u, w in junk_notes[:6]) or "без ops"
            raise OutrightJunkError(
                f"excel_gpt node={node_key}: L{level} call {my_i} "
                f"откровенный брак ({why})"
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

    def _skip_pack(chunk: list[dict[str, Any]], level: int, reason: str) -> None:
        uids = [str(fr.get("uuid") or "").strip() for fr in chunk]
        skipped_packs.append({"level": level, "uuids": uids, "reason": reason[:1000]})
        recorder.record_skip(uuids=uids, reason=reason)
        logger.error(
            "[#{}] apply_ops node={!r}: L{} пачка ({} ячеек) пропущена после "
            "повтора — старые данные оставлены: {}",
            project_id,
            node_key,
            level,
            len(uids),
            reason[:300],
        )

    async def _gate_retry_once(
        chunk: list[dict[str, Any]], level: int, first: ShotsGateError
    ) -> None:
        """Гейт шотов (2026-10-11): только бракованные ячейки, по одной, параллельно (5с), до 3 раундов."""
        todo = list(getattr(first, "bad_chunk", None) or chunk)
        reasons: dict[str, str] = {}
        for rnd in range(1, 4):
            logger.warning(
                "[#{}] apply_ops node={!r}: L{} гейт шотов → раунд {} повтор {} ячеек по одной (параллельно)",
                project_id, node_key, level, rnd, len(todo),
            )

            async def _cell(fr: dict[str, Any]) -> str | None:
                try:
                    miss = await _one_chunk([fr], level)
                    return None if not miss else "нет ops"
                except ShotsGateError as e:
                    return str(e)
                except Exception as e:  # noqa: BLE001
                    return f"{type(e).__name__}: {e}"

            res = await asyncio.gather(*[_stag(k * 5.0, _cell(fr)) for k, fr in enumerate(todo)])
            nxt = []
            for fr, r in zip(todo, res):
                if r is not None:
                    reasons[str(fr.get("uuid") or "")] = r
                    nxt.append(fr)
            todo = nxt
            if not todo:
                return
        for fr in todo:
            _skip_pack([fr], level, reasons.get(str(fr.get("uuid") or ""), str(first)))

    async def _run_adaptive(
        chunk: list[dict[str, Any]],
        level: int,
        under_retries: int = 2,
        junk_left: int = 1,
    ) -> None:
        try:
            missing = await _one_chunk(chunk, level)
        except OutrightJunkError as exc:
            if junk_left > 0:
                logger.warning(
                    "[#{}] apply_ops node={!r}: L{} откровенный брак → "
                    "повтор пачки 1 раз ({})",
                    project_id,
                    node_key,
                    level,
                    str(exc)[:160],
                )
                await asyncio.sleep(1.0)
                await _run_adaptive(
                    chunk, level, under_retries=under_retries, junk_left=0
                )
                return
            _skip_pack(chunk, level, str(exc))
            return
        except ShotsGateError as exc:
            await _gate_retry_once(chunk, level, exc)
            return
        except Exception as exc:
            if allow_empty_ops:
                raise
            nxt = next_split_level(level)
            keep_pack = shots_short_packs(footer_kind)
            if nxt is None or len(chunk) <= 1 or keep_pack:
                # Retry up to under_retries on severe undercount/PassStop.
                msg = str(exc)
                under = (
                    "GPT кадров" in msg
                    or "underproduction" in msg
                    or "ожидаемых" in msg
                    or "нужен добор" in msg
                    or "не покрыл шаги" in msg
                )
                if under_retries > 0 and under:
                    left = under_retries - 1
                    logger.warning(
                        "[#{}] apply_ops node={!r}: L{} undercount PassStop → "
                        "retry (left={}) ({})",
                        project_id,
                        node_key,
                        level,
                        left,
                        msg[:160],
                    )
                    await asyncio.sleep(1.0)
                    await _run_adaptive(chunk, level, under_retries=left)
                    return
                # Молчание одной пачки не валит шаг: иначе soft retry
                # перезапускает всю ноду с первой пачки.
                timed_out = (
                    isinstance(exc, RuntimeError) and "timeout" in msg.lower()
                )
                _net = timed_out or type(exc).__name__ in (
                    "GptApiError",
                    "ReadTimeout",
                    "ConnectError",
                    "RemoteProtocolError",
                    "TimeoutError",
                )
                if _net:
                    _tries = getattr(_run_adaptive, "_net_tries", {})
                    _run_adaptive._net_tries = _tries
                    _k = tuple(str(fr.get("uuid") or "") for fr in chunk)
                    _tries[_k] = _tries.get(_k, 0) + 1
                    # Шоты: путь рвётся часто, но каждый повтор дешевле
                    # (целые ops уже записаны) — даём 3 попытки, как всем.
                    _cap = SHOTS_NET_RETRIES if keep_pack else 3
                    if _tries[_k] <= _cap:
                        if keep_pack:
                            _bo = SHOTS_NET_RETRY_BACKOFF_S[
                                min(_tries[_k], len(SHOTS_NET_RETRY_BACKOFF_S)) - 1
                            ]
                        else:
                            _bo = 20.0
                        logger.warning("[#{}] apply_ops node={!r}: L{} net/LLM fail ({}) → retry {}/{} in {:.0f}s", project_id, node_key, level, msg[:120], _tries[_k], _cap, _bo)
                        await asyncio.sleep(_bo)
                        await _run_adaptive(chunk, level, under_retries=under_retries)
                        return
                    _skip_pack(chunk, level, f"LLM/сеть после {_cap} повторов: {msg[:200]}")
                    return
                if isinstance(exc, PassStopError):
                    raise
                logger.error(
                    "[#{}] apply_ops node={!r}: L{} пачка упала, "
                    "ноду не перезапускаем: {}",
                    project_id,
                    node_key,
                    level,
                    msg[:300],
                )
                _skip_pack(chunk, level, msg)
                return
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
            await asyncio.gather(*[_stag(k * 5.0, _run_adaptive(part, nxt, under_retries=2)) for k, part in enumerate(parts)])  # 2026-10-11 parallel
            return
        if not missing:
            return
        # валил всю пачку (7/8 → RuntimeError → soft retry всех 188).
        if len(missing) == 1:
            uid = str(missing[0].get("uuid") or "")[:8]
            if len(chunk) <= 1:
                _skip_pack(
                    chunk,
                    level,
                    f"неполный apply-ops (0/1), ноду не перезапускаем. uuid: {uid}",
                )
                return
            logger.warning(
                "[#{}] apply_ops node={!r}: L{} incomplete {}/{} → retry uuid {}",
                project_id,
                node_key,
                level,
                len(chunk) - 1,
                len(chunk),
                uid,
            )
            await _run_adaptive(
                missing,
                next_split_level(level) or level,
                under_retries=0,
                junk_left=0,
            )
            return

        # Если не хватает нескольких кадров (обрыв сокета / неполный стрим) —
        # сначала дозапрашиваем только недостающие кадры без дробления.
        # Шоты: до SHOTS_MISSING_ROUNDS доборов (обрыв на пути — норма,
        # каждый добор короче предыдущего); прочие — один добор.
        if under_retries > 0 and len(missing) > 1:
            keep_pack = shots_short_packs(footer_kind)
            next_under = min(under_retries - 1, SHOTS_MISSING_ROUNDS - 1) if keep_pack else 0
            logger.warning(
                "[#{}] apply_ops node={!r}: L{} incomplete {}/{} (сокет обрыв/salvage) "
                "→ retry missing ({} кадров) без дробления, доборов после этого: {}",
                project_id,
                node_key,
                level,
                len(chunk) - len(missing),
                len(chunk),
                len(missing),
                max(0, next_under),
            )
            await asyncio.sleep(1.0)
            await _run_adaptive(
                missing, level, under_retries=max(0, next_under), junk_left=0
            )
            return

        nxt = next_split_level(level)
        if nxt is None or shots_short_packs(footer_kind):
            why = (
                "пачку шотов не режем"
                if shots_short_packs(footer_kind)
                else "резать дальше некуда"
            )
            _skip_pack(
                missing,
                level,
                f"неполный apply-ops, {why}, ноду не перезапускаем: "
                + ", ".join(str(fr.get("uuid") or "")[:8] for fr in missing[:8]),
            )
            return
        logger.warning(
            "[#{}] apply_ops node={!r}: L{} incomplete {}/{} → split L{}",
            project_id,
            node_key,
            level,
            len(chunk) - len(missing),
            len(chunk),
            nxt,
        )
        _halves = list(split_in_half(missing))
        await asyncio.gather(*[_run_pack(k * 5.0, part) if False else _stag(k * 5.0, _run_adaptive(part, nxt, under_retries=2)) for k, part in enumerate(_halves)])

    async def _stag(delay: float, coro: Any) -> Any:
        if delay > 0:
            await asyncio.sleep(delay)
        return await coro

    async def _run_pack(delay: float, pack: list[dict[str, Any]]) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await _run_adaptive(pack, 1)

    def _log_pack_errors(results: list[Any]) -> None:
        for err in results:
            if isinstance(err, asyncio.CancelledError):
                raise err
            if isinstance(err, PassStopError):
                raise err
            if isinstance(err, BaseException):
                logger.error(
                    "[#{}] apply_ops node={!r}: пачка упала, "
                    "ноду не перезапускаем: {}",
                    project_id,
                    node_key,
                    err,
                )

    async def _run_waves() -> None:
        if len(packs) <= 1:
            for pack in packs:
                await _run_adaptive(pack, 1)
        elif shots_short_packs(footer_kind):
            # Конвейер: все пачки стоят в очереди к гейту (в полёте не больше
            # wave_n, между отправками delay_s). Без барьера «волны» —
            # медленная или повторяемая пачка не держит остальные.
            logger.info(
                "[#{}] apply_ops node={!r}: конвейер {} пачек, "
                "в полёте {}, старт через {}s",
                project_id,
                node_key,
                len(packs),
                wave_n,
                delay_s,
            )
            if on_progress is not None:
                try:
                    await on_progress(
                        f"конвейер: {len(packs)} пачек по {len(packs[0])}, "
                        f"в полёте {wave_n}"
                    )
                except Exception:
                    logger.debug("apply_ops progress callback failed", exc_info=True)
            results = await asyncio.gather(
                *[_run_pack(0.0, pack) for pack in packs],
                return_exceptions=True,
            )
            _log_pack_errors(list(results))
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
                _log_pack_errors(list(results))

    # Шоты: гейт и есть ограничитель параллельности конвейера.
    gate_token = _pack_gate.set(
        _PackSendGate(
            delay_s,
            in_flight=wave_n if shots_short_packs(footer_kind) else None,
        )
    )
    try:
        try:
            await _run_waves()
        except asyncio.CancelledError:
            recorder.finish(ok=False, error="cancelled")
            raise
        except BaseException as exc:
            # Пачка или внутренняя ошибка не должны ронять шаг:
            # soft retry заново открывает ноду с первой пачки.
            # Скопированный смысл аналитики — стоп пачки, не тихий пропуск.
            recorder.finish(ok=False, error=f"{type(exc).__name__}: {exc}")
            logger.error(
                "[#{}] apply_ops node={!r}: прогон пачек остановился, "
                "ноду не перезапускаем: {}",
                project_id,
                node_key,
                exc,
            )
            # QC-контракт и отмена должны дойти до вызывающего.
            # Обычная пачка excel по-прежнему не валит шаг.
            if allow_empty_ops or isinstance(exc, PassStopError):
                raise
        if skipped_packs:
            n_cells = sum(len(p["uuids"]) for p in skipped_packs)
            recorder.finish(
                ok=True,
                error=(
                    f"гейт шотов: пропущено пачек {len(skipped_packs)} "
                    f"({n_cells} ячеек), старые данные оставлены"
                ),
            )
        else:
            recorder.finish(ok=True)

        ctx_path.write_text(
            json.dumps(db_ctx, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        apply_ops_out: dict[str, Any] = {"ops": merged_ops}
        if skipped_packs:
            apply_ops_out["skipped_packs"] = skipped_packs
        return OperatorApiResult(
            reply_text="\n\n".join(replies),
            output_paths=last_paths,
            apply_ops=apply_ops_out,
            applied_in_runner=apply_fn is not None,
        )
    finally:
        _pack_gate.reset(gate_token)
