"""«Улучшить сцену»: одна VO-ячейка через 3 ноды группы script_frames_qc.

fw_action → fw_shots → fw_qc. fw_script и fw_check_script не зовём.
В GPT только заказ оператора и реестр персонажей: якоря, биты, паспорт
и закадр как сюжет не входят. Закадр режет код после кадров.
Без заказа — нарезка по закадру, GPT не зовём.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.db_apply import extract_apply_ops_json
from app.services.montage_coverage_ops import (
    COVERAGE_ANGLE_CHOICES,
    COVERAGE_LIGHT_CHOICES,
    COVERAGE_MOVE_CHOICES,
    COVERAGE_PLAN_CHOICES,
    COVERAGE_STITCH_CHOICES,
    COVERAGE_VISUAL_TYPE_CHOICES,
    MAX_IMPROVE_SHOTS,
    apply_coverage_scene_action,
    canonical_stitch,
)
from app.services.montage_scene_editor import (
    anchor_positions,
    claim_scene_bits,
    normalize_anchor_rows,
    scene_anchor_bits,
    split_vo_by_anchors,
)
from app.services.prompt_library import resolve_script_frames_qc_prompt_path
from app.services.scene_plan import (
    apply_scene_plan,
    apply_scene_plan_cells,
    plan_fix_notes,
    plan_hard_reasons,
    with_plan_notes,
)
from app.services.scene_shot_grammar import (
    OBJECTS,
    action_stem,
    camera_pack,
    classify_object,
    fill_bit_spans,
    is_threshold_step,
)
from app.services.shot_templates import format_scene_chain, parse_scene_chain
from app.services.vo_shot_expand import _flag_attrs, bits_from_attrs, main_action_text

GROUP_NODES: tuple[tuple[str, str], ...] = (
    ("fw_action", "Действие сцены"),
    ("fw_shots", "Сцены → кадры"),
    ("fw_qc", "QC кадров"),
    ("characters", "Персонажи"),
)

REPORT_ATTR = "montage_improve_report"

SHOT_ROLES = ("вход", "мост", "действие", "перебивка", "реакция", "следствие")
STITCH_KEYS = tuple(key for key, _label in COVERAGE_STITCH_CHOICES)

_PLAN_ALIASES = {
    "ДЕТАЛЬНЫЙ": "ДЕТАЛЬ",
    "МАКРО": "ДЕТАЛЬ",
    "СРЕДНЕ-КРУПНЫЙ": "КРУПНЫЙ",
    "СРЕДНЕКРУПНЫЙ": "КРУПНЫЙ",
    "ПОЯСНОЙ": "СРЕДНИЙ",
    "ОБЩИЙ ПЛАН": "ОБЩИЙ",
    "ОБЩАЯ": "ОБЩИЙ",
}
_ANGLE_ALIASES = {
    "из-за плеча": "с плеча",
    "ots": "с плеча",
    "фронтально": "фронт",
    "анфас": "фронт",
    "три четверти": "3/4",
    "верхний": "сверху",
    "нижний": "снизу",
}
# Соседние ступени крупности; ОБЩИЙ ↔ ДЕТАЛЬ подряд — прыжок «не через план».
_PLAN_STEP = {"ДАЛЬНИЙ": 0, "ОБЩИЙ": 1, "СРЕДНИЙ": 2, "КРУПНЫЙ": 3, "ДЕТАЛЬ": 4}

_SUB_PLACE_RE = re.compile(r"\s*[:·]\s*")
_ENTER_RE = re.compile(
    r"(?<![0-9a-zа-яё])"
    r"(?:вош[её]л\w*|вход(?:ит|ят|я|ил\w*)|зашёл|зашел|приш[её]л\w*|приехал\w*)"
    r"(?![0-9a-zа-яё])",
    re.IGNORECASE,
)
_WALK_RE = re.compile(
    r"(?<![0-9a-zа-яё])"
    r"(?:ид[её]т|ш[её]л|бежит|бега\w*|вош[её]л\w*|выш[её]л\w*|приш[её]л\w*|крад)"
    r"(?![0-9a-zа-яё])",
    re.IGNORECASE,
)

AskFn = Callable[[str], Awaitable[str]]


def is_establishing_step(step: str) -> bool:
    """Вход в место — глагол, не «входная дверь» / «за входом»."""
    return bool(_ENTER_RE.search(_norm(step)))


def is_walk_step(step: str) -> bool:
    """Ход камеры. «прихожая» не считается ходьбой."""
    return bool(_WALK_RE.search(_norm(step)))


_PLAN_BY_STEP = {v: k for k, v in _PLAN_STEP.items()}


def _step_plan(plan: str, obj: str) -> str:
    want = {"лицо": "КРУПНЫЙ", "предмет": "ДЕТАЛЬ", "взгляд": "КРУПНЫЙ"}.get(obj, "")
    if want and want != plan:
        return want
    idx = _PLAN_STEP.get(plan, 2)
    nxt = idx + 1 if idx < 4 else idx - 1
    return _PLAN_BY_STEP.get(nxt, "КРУПНЫЙ")


def vary_adjacent_coverage(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Если все кадры одной крупности — развести. Иначе чипы на доске не меняются."""
    if len(shots) < 2:
        return shots
    plans = [s.get("план") for s in shots]
    if len({p for p in plans if p}) > 1:
        return shots
    prev: dict[str, Any] | None = None
    for shot in shots:
        if prev is not None and shot.get("план") == prev.get("план"):
            shot["план"] = _step_plan(
                str(shot.get("план") or "СРЕДНИЙ"),
                str(shot.get("объект") or ""),
            )
        prev = shot
    return shots


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split())


_WIDE_PLANS = frozenset({"ДАЛЬНИЙ", "ОБЩИЙ"})


def _base_place(shot: dict[str, Any]) -> str:
    return _norm(_SUB_PLACE_RE.split(_norm(shot.get("место")), maxsplit=1)[0]).casefold()


def wide_return_reason(
    shot: dict[str, Any], prev: dict[str, Any]
) -> str | None:
    """Шире прошлого кадра до ОБЩЕГО/ДАЛЬНЕГО без нового места — «средний → общий → средний».

    Разрешено: вход в новое место скачком (не через дверь прошлого кадра),
    стык dissolve/fade (прошло время).
    """
    plan = str(shot.get("план") or "")
    prev_plan = str(prev.get("план") or "")
    if plan not in _WIDE_PLANS or prev_plan in _WIDE_PLANS:
        return None
    if _PLAN_STEP.get(plan, 9) >= _PLAN_STEP.get(prev_plan, -1):
        return None
    if str(shot.get("стык") or "") in {"dissolve", "fade"}:
        return None
    step = _norm(shot.get("действие"))
    prev_step = _norm(prev.get("действие"))
    if is_threshold_step(prev_step):
        return "вход через дверь прошлого кадра — продолжение изнутри, не новый общий"
    new_place = _base_place(shot) and _base_place(shot) != _base_place(prev)
    if new_place or is_establishing_step(step):
        return None
    return "возврат к общему плану посреди действия без нового места"


def smooth_plan_sequence(shots: list[dict[str, Any]]) -> list[str]:
    """Кино-порядок: общий → средний → крупный; назад к общему только с причиной."""
    fixed: list[str] = []
    for i in range(1, len(shots)):
        shot, prev = shots[i], shots[i - 1]
        reason = wide_return_reason(shot, prev)
        if not reason:
            continue
        shot["план"] = "СРЕДНИЙ"
        if is_threshold_step(_norm(prev.get("действие"))):
            shot["стык"] = "cut_on_action"
        if shot.get("план") == prev.get("план") and shot.get("ракурс") == prev.get("ракурс"):
            shot["ракурс"] = "3/4" if shot.get("ракурс") != "3/4" else "фронт"
        fixed.append(f"кадр {shot.get('порядок') or i + 1}: {reason} → СРЕДНИЙ")
    return fixed


# --------------------------------------------------------------------------- #
# Промты группы
# --------------------------------------------------------------------------- #


def load_group_prompt(name: str) -> str:
    path = resolve_script_frames_qc_prompt_path(name)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def shot_budget(vo: str) -> int:
    """По умолчанию один кадр на кусок закадра — длину текста в кадры не раздуваем."""
    return 1 if _norm(vo) else 1


_ARROW_SPLIT_RE = re.compile(r"\s*→\s*|\s*->\s*")


def operator_arrow_beats(text: str) -> list[str]:
    """Шаги только по ``→``. Проза, «потом» и точки кадры не плодят."""
    parts = [p for p in _ARROW_SPLIT_RE.split(_norm(text)) if p]
    return parts if len(parts) >= 2 else []


def allot_budgets(pieces: list[str], operator_prompt: str = "") -> list[int]:
    """Один кадр на якорь. Больше — только если промт оператора сам режет шаги ``→``."""
    n = len(pieces) or 1
    out = [1] * n
    extra = len(operator_arrow_beats(operator_prompt)) - n
    i = 0
    while extra > 0 and sum(out) < MAX_IMPROVE_SHOTS:
        out[i % n] += 1
        extra -= 1
        i += 1
    return out


def _cell_block(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _compose(
    rules: str,
    directing: str,
    task: str,
    payload: dict[str, Any],
    *,
    operator_first: bool = False,
) -> str:
    task_block = "## задача оператора\n" + task.strip()
    body = "Вход — одна VO-ячейка:\n" + _cell_block(payload)
    extra = [p.rstrip() for p in (rules, directing) if p and p.strip()]
    if operator_first:
        parts = [task_block, *extra, body]
    else:
        parts = [*extra, task_block, body]
    return "\n\n".join(parts) + "\n"


SHOT_ACTION_BODY_RULES = """Каждое действие — полный кадр, не короткий глагол.
В действии этого кадра обязательно:
• место: где стоим именно в этом кадре;
• окружение: что вокруг — стены, дверь, предметы, фон этого кадра;
• мизансцена: кто слева, кто справа, кто в центре, лицом или спиной;
• субъект: кто совершает действие (код и имя из реестра) и что делает телом;
• старт: где субъект в начале кадра и что уже открыто/закрыто — ровно то,
  чем закончился прошлый кадр.
«Слева/справа» — как видно с точки этого кадра. Кадр снят с другой стороны
(изнутри дома, навстречу) — лево и право меняются местами, в кадре видно то,
что раньше было за спиной зрителя.
Нельзя писать «идёт», «хватает», «стоит» без места, раскладки и исполнителя.
Общей карточки ячейки нет — место и окружение только внутри действия кадра.
Камера, план, ракурс — отдельные поля, не в тексте действия."""


SCENE_PHYSICS_RULES = """Физика действия (важнее краткости):
1. Предмет меняет состояние только в кадре. Дверь закрыта, пока кто-то в
   кадре её не открыл. Нельзя бежать к уже открытой двери, которую не открывали.
2. Проход через дверь/порог — два кадра: снаружи он открывает дверь →
   изнутри он входит (продолжение того же движения).
3. Кто идёт или бежит, в следующем кадре уже дальше по пути (у двери,
   в коридоре, у цели) — не с той же точки и не на месте.
4. Направление держится: бежал слева направо — дальше тоже слева направо.
5. Между «снаружи» и «внутри» нет пропусков: без кадра на пороге герой
   не может оказаться в доме."""


IMPROVE_ACTION_RULES = """# Агент: главное действие
# Улучшить сцену — действие
Ответ — ТОЛЬКО JSON. В `fields` — только `главное_действие`.

Поставь сцену: видимые кадры по заказу оператора.
Не копируй заказ дословно. Столько шагов, сколько событий.

`главное_действие` — массив строк, по одной на кадр. Каждая строка — полный кадр:
["коридор редакции, слева стеллаж с папками, справа окно; c01 следователь в центре тянет папку с полки",
 "тот же коридор, слева полка, справа стол; c01 следователь справа держит газету у лица"]

""" + SHOT_ACTION_BODY_RULES + """

""" + SCENE_PHYSICS_RULES + """

Закадр, якоря и биты в заказ не входят — это не сюжет.
Не добавляй вход, мост, перебивку, реакцию, следствие ради схемы.
Кадр на пороге по правилу физики — не схема, а логика: его добавляй.
Не копируй учебные примеры. Не пиши выводы («понимает», «узнаёт»).
"""


IMPROVE_SHOTS_RULES = """# Агент: сцены → кадры
# Улучшить сцену — кадры
Ответ — ТОЛЬКО JSON apply-ops. Один op, в `fields` только `кадры`.

Один шаг `→` = один кадр, порядок тот же. Новых кадров не добавляй.
Закадр, якоря и паспорт во входе отсутствуют — не режь текст и не выдумывай кадры.
`роль` — метка покрытия, не повод выдумать кадр.
Число кадров = число шагов `главное_действие`.

Порядок планов как в кино:
1. ОБЩИЙ/ДАЛЬНИЙ — первый кадр нового места. Дальше СРЕДНИЙ → КРУПНЫЙ/ДЕТАЛЬ.
2. Назад к ОБЩЕМУ посреди действия нельзя. Можно только: герой попал в
   другое место скачком (не через дверь прошлого кадра), прошло время
   (`dissolve`/`fade`) или это финал сцены.
3. Кадр после двери/порога — СРЕДНИЙ изнутри, навстречу входящему,
   стык `cut_on_action`. Не ОБЩИЙ.
4. Идёт/бежит два кадра подряд — другой план или ракурс, `следование`;
   герой уже дальше по пути.
5. Двое или герой и цель взгляда — камера с одной стороны от линии между ними.
"""


def _loose_json_dict(text: str) -> dict[str, Any] | None:
    """apply-ops или любой JSON-объект из ответа GPT."""
    if not (text or "").strip():
        return None
    data = extract_apply_ops_json(text)
    if data:
        return data
    candidates: list[str] = [
        m.group(1) for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text)
    ]
    stripped = text.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _ops_fields(reply: str, uuid: str) -> dict[str, Any]:
    data = _loose_json_dict(reply or "")
    if not data:
        return {}
    ops = data.get("ops") or data.get("actions") or []
    fallback: dict[str, Any] = {}
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
        if not fields:
            fields = op
        op_uuid = str(op.get("frame_uuid") or op.get("uuid") or "").strip()
        if uuid and op_uuid == uuid:
            return fields
        if not fallback:
            fallback = fields
    if fallback:
        return fallback
    inner = data.get("fields")
    if isinstance(inner, dict) and (
        inner.get("главное_действие")
        or inner.get("main_action")
        or inner.get("кадры")
        or inner.get("shots")
    ):
        return inner
    scenes = data.get("scenes")
    if isinstance(scenes, list):
        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            if isinstance(scene.get("fields"), dict):
                return scene["fields"]
            if scene.get("главное_действие") or scene.get("кадры") or scene.get("shots"):
                return scene
    if (
        data.get("главное_действие")
        or data.get("main_action")
        or data.get("кадры")
        or data.get("shots")
    ):
        return data
    return {}


def _units_payload(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "n": u["n"],
            "якорь": u["якорь"],
            "изменение": u["изменение"],
            "закадр": u["закадр"],
            "бюджет_кадров": u["бюджет"],
        }
        for u in units
    ]


# --------------------------------------------------------------------------- #
# fw_script + fw_check_script — якоря = границы текста
# --------------------------------------------------------------------------- #


def check_bits(vo: str, bits: list[Any]) -> str | None:
    """Проверка битов как у fw_check_script: массив, изменение, якоря по порядку."""
    text = _norm(vo)
    if not text:
        return None
    rows = [b for b in bits or [] if isinstance(b, dict)]
    if not rows:
        return "нет битов"
    low = text.casefold()
    cursor = 0
    for i, bit in enumerate(sorted(rows, key=lambda b: int(b.get("порядок") or 0)), start=1):
        if not str(bit.get("изменение") or bit.get("глагол") or "").strip():
            return f"бит {i}: пустое изменение"
        anchor = _norm(bit.get("якорь")).casefold()
        if not anchor:
            return f"бит {i}: нет якоря"
        idx = low.find(anchor, cursor)
        if idx < 0:
            return f"бит {i}: якорь «{anchor[:40]}» не найден дословно по порядку"
        cursor = idx + len(anchor)
    return None


def anchor_units(
    vo: str, rows: list[dict[str, Any]], operator_prompt: str = ""
) -> list[dict[str, Any]]:
    """Якоря → куски закадра, как их режет доска. Ненайденный якорь — ошибка."""
    text = _norm(vo)
    ordered = sorted(
        (r for r in rows if _norm(r.get("якорь"))),
        key=lambda r: int(r.get("порядок") or 0),
    )
    texts = [_norm(r.get("якорь")) for r in ordered]
    lost = [texts[i] for i, pos in enumerate(anchor_positions(text, texts)) if pos < 0]
    if lost:
        raise RuntimeError(
            "якорь не найден по порядку в закадре сцены: "
            + "; ".join(f"«{x}»" for x in lost)
            + " — поправьте якоря: текст других якорей в сцену не берём"
        )
    pieces = split_vo_by_anchors(text, texts)
    if len(pieces) != len(texts):
        raise RuntimeError(
            f"якорей {len(texts)}, а кусков закадра {len(pieces)}: "
            "два якоря стоят в одном месте текста"
        )
    budgets = allot_budgets(pieces, operator_prompt)
    return [
        {
            "n": i + 1,
            "якорь": texts[i],
            "изменение": _norm(row.get("изменение") or row.get("глагол")),
            "закадр": pieces[i],
            "бюджет": budgets[i],
        }
        for i, row in enumerate(ordered)
    ]


def build_script_prompt(parent: Frame, vo: str, operator_prompt: str, reason: str) -> str:
    task = "Точечно: биты только этой ячейки закадра. Один op, только `биты`."
    if reason:
        task += f"\nПроверка «сценарий» вернула Не ок: {reason}. Исправь."
    if operator_prompt:
        task += f"\nПромт оператора: {operator_prompt}"
    payload = {
        "frame_uuid": str(parent.uuid or ""),
        "frame_number": int(parent.number),
        "voiceover_text": vo,
    }
    return _compose(load_group_prompt("script_writer_ru"), "", task, payload)


def _save_bits(
    frames: list[Frame], parent: Frame, members: list[Frame], rows: list[dict[str, Any]]
) -> None:
    clean = [{k: v for k, v in row.items() if k != "закадр"} for row in rows]
    claim_scene_bits(frames, parent, members, clean)


async def stage_script(
    ask: AskFn,
    parent: Frame,
    vo: str,
    operator_prompt: str,
    nodes: list[dict[str, Any]],
    *,
    frames: list[Frame] | None = None,
    members: list[Frame] | None = None,
    anchors: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Куски сцены по якорям. Якоря есть — GPT биты не пишет."""
    frames = frames if frames is not None else [parent]
    members = members if members is not None else [parent]
    given = normalize_anchor_rows(anchors) if anchors else []
    own = scene_anchor_bits(frames, parent, members)
    rows = given or own
    if rows:
        units = anchor_units(vo, rows, operator_prompt)
        _save_bits(frames, parent, members, rows)
        nodes.append(
            _node(
                "fw_script",
                "reused",
                f"якоря сцены: {len(units)} — граница текста, не сюжет",
            )
        )
        nodes.append(
            _node(
                "fw_check_script",
                "ok",
                "каждый якорь найден; текст сцены = куски этих якорей",
            )
        )
        return units

    if operator_prompt:
        text = _norm(vo)
        nodes.append(
            _node("fw_script", "ok", "сюжет из промта — биты из закадра не пишем")
        )
        nodes.append(_node("fw_check_script", "ok", "закадр целиком ляжет на кадры промта"))
        return [
            {
                "n": 1,
                "якорь": " ".join(text.split()[:8]),
                "изменение": "",
                "закадр": text,
                "бюджет": 1,
            }
        ]

    reason = "нет битов"
    tries: list[str] = []
    for attempt in range(2):
        prompt = build_script_prompt(parent, vo, operator_prompt, reason if attempt else "")
        try:
            reply = await ask(prompt)
        except Exception as exc:  # noqa: BLE001
            tries.append(f"GPT: {type(exc).__name__}: {exc}"[:160])
            break
        raw = _ops_fields(reply, str(parent.uuid or "")).get("биты")
        candidate = fill_bit_spans(vo, raw) if isinstance(raw, list) else []
        reason = check_bits(vo, candidate) or ""
        if not reason:
            try:
                units = anchor_units(vo, candidate, operator_prompt)
            except RuntimeError as exc:
                reason = str(exc)
            else:
                _save_bits(frames, parent, members, candidate)
                nodes.append(_node("fw_script", "ok", f"биты ячейки: {len(units)}"))
                nodes.append(
                    _node(
                        "fw_check_script",
                        "fixed" if attempt else "ok",
                        f"Не ок → переписано: {tries[-1]}" if attempt and tries else "Ок",
                    )
                )
                return units
        tries.append(reason)
    nodes.append(_node("fw_script", "warn", "биты не собраны — весь закадр одним куском"))
    nodes.append(_node("fw_check_script", "warn", "; ".join(tries)[:240]))
    text = _norm(vo)
    return [
        {
            "n": 1,
            "якорь": " ".join(text.split()[:8]),
            "изменение": "",
            "закадр": text,
            "бюджет": allot_budgets([text], operator_prompt)[0],
        }
    ]


# --------------------------------------------------------------------------- #
# fw_action — карточка N. на каждый якорь
# --------------------------------------------------------------------------- #


def _passport_input(passport: dict[str, Any], character_labels: str) -> dict[str, str]:
    def _get(*keys: str) -> str:
        for key in keys:
            val = str(passport.get(key) or "").strip()
            if val:
                return val
        return ""

    return {
        "смысл": _get("sense", "смысл"),
        "тип": _get("visual_type", "тип"),
        "место": _get("place", "место"),
        "набор": _get("set", "набор"),
        "персонажи": character_labels or _get("characters", "персонажи"),
        "свет": _get("light", "lighting", "освещение"),
        "предметы": _get("props", "предметы"),
        "фон": _get("bg", "фон"),
        "акцент": _get("accent", "акцент"),
        "особенность": _get("feature", "особенность"),
    }


def build_improve_action_prompt(
    *,
    parent: Frame,
    vo: str,
    units: list[dict[str, Any]],
    passport: dict[str, str],
    operator_prompt: str,
    registry_labels: str = "",
    matched_labels: str = "",
) -> str:
    """fw_action: сюжет только из заказа. Якоря, биты, закадр, паспорт не входим."""
    _ = vo, units, passport
    task = (
        "Поставь сцену по этому заказу — не копируй текст заказа в действие "
        "и не снимай другую историю:\n"
        f"{operator_prompt}\n\n"
        "Нода fw_action группы script_frames_qc. "
        "Биты, якоря и закадр во входе отсутствуют — не выдумывай их и не иллюстрируй.\n"
        "Напиши полное действие каждого кадра. "
        "Столько кадров, сколько событий в заказе. "
        "Не добавляй вход, мост, перебивку, реакцию и следствие, "
        "если заказ этого не просит. Исключение — физика: проход через дверь "
        "(снаружи открывает → изнутри входит), иначе герой телепортируется.\n"
        f"{SHOT_ACTION_BODY_RULES}\n"
        "В `fields` — только `главное_действие`."
    )
    payload = {
        "frame_uuid": str(parent.uuid or ""),
        "frame_number": int(parent.number),
        "заказ": operator_prompt,
        "персонажи_реестра": registry_labels or "",
        "уже_совпали": matched_labels or "",
        "mode": "improve",
    }
    return _compose(IMPROVE_ACTION_RULES, "", task, payload, operator_first=True)


def _card_steps(card: dict[str, Any]) -> list[str]:
    act = _norm(card.get("action"))
    parts = [p for p in _ARROW_SPLIT_RE.split(act) if p]
    return parts


_NUM_STEP_RE = re.compile(
    r"(?:^|\n)\s*(?:кадр\s*)?(\d+)\s*[.)]\s*(.+?)(?=(?:\n\s*(?:кадр\s*)?\d+\s*[.)])|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _action_from_item(item: Any) -> tuple[str, str]:
    if isinstance(item, str):
        return "", _norm(item)
    if isinstance(item, dict):
        act = _norm(item.get("действие") or item.get("action") or item.get("шаг"))
        place = _norm(item.get("место") or item.get("place"))
        return place, act
    return "", ""


def _sentence_steps(text: str) -> list[dict[str, Any]]:
    parts = [p.strip().rstrip(".!?") for p in re.split(r"(?<=[.!?])\s+", _norm(text)) if p.strip()]
    parts = [_norm(p) for p in parts if _norm(p)]
    if len(parts) < 2:
        return []
    return [{"n": i + 1, "place": "", "action": part} for i, part in enumerate(parts)]


def _inline_numbered_steps(text: str) -> list[dict[str, Any]]:
    marks = list(re.finditer(r"(?:^|\s)(\d+)\s*[.)]\s+", text))
    if len(marks) < 2:
        return []
    out: list[dict[str, Any]] = []
    for i, match in enumerate(marks):
        start = match.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        rest = _norm(text[start:end])
        rest = re.sub(r"\([^)]*\)\s*$", "", rest).strip()
        place, act = "", rest
        if " — " in rest:
            place, act = rest.split(" — ", 1)
        act = _norm(act)
        if act:
            out.append({"n": int(match.group(1)), "place": _norm(place), "action": act})
    return out


def loose_action_steps(
    text: str, *, allow_sentences: bool = False
) -> list[dict[str, Any]]:
    """Разбор цепи, если нет строгой формы ``N. место — шаг``."""
    raw = (text or "").replace("–", "—").replace(" - ", " — ").strip()
    if not raw:
        return []
    chain = parse_scene_chain(raw)
    if chain:
        return chain
    found: list[dict[str, Any]] = []
    for match in _NUM_STEP_RE.finditer(raw):
        rest = _norm(match.group(2))
        rest = re.sub(r"\([^)]*\)\s*$", "", rest).strip()
        place, act = "", rest
        if " — " in rest:
            place, act = rest.split(" — ", 1)
        act = _norm(act)
        if act:
            found.append({"n": int(match.group(1)), "place": _norm(place), "action": act})
    if len(found) >= 2:
        return found
    inline = _inline_numbered_steps(raw)
    if len(inline) >= 2:
        return inline
    if found:
        return found
    arrows = [p for p in _ARROW_SPLIT_RE.split(_norm(raw)) if p]
    if len(arrows) >= 2:
        return [{"n": i + 1, "place": "", "action": a} for i, a in enumerate(arrows)]
    if allow_sentences:
        return _sentence_steps(raw)
    return []


def cards_raw_from_reply(fields: dict[str, Any], reply: str) -> list[dict[str, Any]]:
    """главное_действие / кадры / нумерованный текст → шаги сцены."""
    raw = fields.get("главное_действие") or fields.get("main_action")
    if isinstance(raw, list):
        out: list[dict[str, Any]] = []
        for i, item in enumerate(raw, start=1):
            place, act = _action_from_item(item)
            if act:
                out.append({"n": i, "place": place, "action": act})
        if out:
            return out
    if isinstance(raw, str) and raw.strip():
        steps = loose_action_steps(raw, allow_sentences=True)
        if steps:
            return steps
    for key in ("кадры", "shots", "шаги", "steps"):
        val = fields.get(key)
        if not isinstance(val, list):
            continue
        out = []
        for i, item in enumerate(val, start=1):
            place, act = _action_from_item(item)
            if act:
                out.append({"n": i, "place": place, "action": act})
        if out:
            return out
    extra = _ops_fields(reply or "", "")
    if extra and extra is not fields:
        raw2 = extra.get("главное_действие") or extra.get("main_action")
        if raw2 and raw2 != raw:
            return cards_raw_from_reply(extra, "")
        for key in ("кадры", "shots", "шаги", "steps"):
            val = extra.get(key)
            if isinstance(val, list) and val:
                return cards_raw_from_reply(extra, "")
    return loose_action_steps(reply or "")


def fit_cards_to_units(
    cards: list[dict[str, Any]], units: list[dict[str, Any]], place: str
) -> list[dict[str, Any]] | None:
    """Карточки GPT → по одной на якорь с его куском закадра. None — не сходится."""
    if not cards:
        return None
    if len(cards) != len(units):
        if len(units) != 1:
            return None
        steps = [s for c in cards for s in _card_steps(c)]
        cards = [{"n": 1, "place": cards[0].get("place"), "action": " → ".join(steps)}]
    out: list[dict[str, Any]] = []
    for card, unit in zip(cards, units, strict=True):
        steps = _card_steps(card)
        if not steps:
            return None
        out.append(
            {
                "n": unit["n"],
                "place": place or _norm(card.get("place")),
                "action": " → ".join(steps),
                "vo": unit["закадр"],
            }
        )
    return out


def cap_cards_to_budget(
    cards: list[dict[str, Any]], units: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Лишние шаги сверх бюджета якоря отрезаем — GPT не раздувает сцену."""
    out: list[dict[str, Any]] = []
    for card, unit in zip(cards, units, strict=True):
        cap = max(1, int(unit.get("бюджет") or 1))
        steps = _card_steps(card)[:cap]
        row = dict(card)
        if steps:
            row["action"] = " → ".join(steps)
        out.append(row)
    return out


_PROSE_SPLIT_RE = re.compile(r"(?i)\s+(?:а\s+)?потом\s+")


def operator_action_beats(text: str) -> list[str]:
    """Шаги сцены только из промта. `→`, иначе «потом», иначе один шаг."""
    arrows = operator_arrow_beats(text)
    if arrows:
        return arrows[:MAX_IMPROVE_SHOTS]
    prose = [_norm(p) for p in _PROSE_SPLIT_RE.split(_norm(text)) if _norm(p)]
    if len(prose) >= 2:
        return prose[:MAX_IMPROVE_SHOTS]
    one = _norm(text)
    return [one] if one else []


def cards_from_operator_prompt(
    operator_prompt: str, place: str, vo: str
) -> list[dict[str, Any]]:
    """Запасной разбор, если GPT молчит: шаги `→` или один шаг."""
    beats = operator_action_beats(operator_prompt)
    if not beats:
        return []
    parts = split_vo_for_shots(_norm(vo), len(beats))
    return [
        {"n": i + 1, "place": place, "action": beat, "vo": parts[i]}
        for i, beat in enumerate(beats)
    ]


def scene_cards_from_gpt(
    cards_raw: list[dict[str, Any]], place: str, vo: str
) -> list[dict[str, Any]] | None:
    """Ответ GPT → кадры сцены. Закадр только нарезаем, сюжет из него не берём."""
    steps = [s for card in cards_raw for s in _card_steps(card)]
    steps = [s for s in steps if s][:MAX_IMPROVE_SHOTS]
    if not steps:
        return None
    loc = place or _norm(cards_raw[0].get("place"))
    parts = split_vo_for_shots(_norm(vo), len(steps))
    return [
        {"n": i + 1, "place": loc, "action": step, "vo": parts[i]}
        for i, step in enumerate(steps)
    ]


def is_raw_prompt_dump(cards: list[dict[str, Any]], operator_prompt: str) -> bool:
    """GPT не поставил сцену — в действие свалил весь заказ."""
    op = _norm(operator_prompt)
    if not cards or not op:
        return False
    if len(cards) == 1 and _norm(cards[0].get("action")) == op:
        return True
    glued = " → ".join(_norm(c.get("action")) for c in cards)
    return glued == op


def units_from_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "n": c["n"],
            "якорь": "",
            "изменение": _norm(c.get("action")),
            "закадр": _norm(c.get("vo")),
            "бюджет": max(1, len(_card_steps(c))),
        }
        for c in cards
    ]


def fallback_cards(
    parent: Frame, units: list[dict[str, Any]], operator_prompt: str, place: str
) -> list[dict[str, Any]]:
    """Без GPT: промт целиком → текущая цепь → изменение якоря."""
    vo = " ".join(_norm(u.get("закадр")) for u in units)
    if _norm(operator_prompt):
        return cards_from_operator_prompt(operator_prompt, place, vo)
    fitted = fit_cards_to_units(parse_scene_chain(main_action_text(parent)), units, place)
    if fitted:
        return cap_cards_to_budget(fitted, units)
    return [
        {
            "n": u["n"],
            "place": place,
            "action": u["изменение"] or "действие сцены",
            "vo": u["закадр"],
        }
        for u in units
    ]


async def stage_action(
    ask: AskFn,
    *,
    parent: Frame,
    vo: str,
    units: list[dict[str, Any]],
    passport: dict[str, str],
    operator_prompt: str,
    nodes: list[dict[str, Any]],
    registry_labels: str = "",
    matched_labels: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    place = passport.get("место") or ""
    if not _norm(operator_prompt):
        cards = fallback_cards(parent, units, operator_prompt, place)
        nodes.append(
            _node("fw_action", "fallback", "без заказа — запасной разбор, GPT не звали")
        )
        return cards, {}
    prompt = build_improve_action_prompt(
        parent=parent,
        vo=vo,
        units=units,
        passport=passport,
        operator_prompt=operator_prompt,
        registry_labels=registry_labels,
        matched_labels=matched_labels,
    )
    note = ""
    gpt_passport: dict[str, Any] = {}
    cards_raw: list[dict[str, Any]] = []
    for attempt in range(2 if operator_prompt else 1):
        ask_text = prompt
        if attempt:
            ask_text = (
                prompt
                + "\nНе копируй заказ в действие. Напиши полное действие каждого кадра."
            )
        try:
            reply = await ask(ask_text)
        except Exception as exc:  # noqa: BLE001
            reply = ""
            note = f"GPT: {type(exc).__name__}: {exc}"[:160]
            break
        fields = _ops_fields(reply, str(parent.uuid or ""))
        cards_raw = cards_raw_from_reply(fields, reply)
        if not cards_raw:
            logger.warning(
                "improve fw_action: не разобрали ответ GPT ({} симв): {}",
                len(reply or ""),
                _norm(reply)[:400],
            )
        raw_pass = fields.get("паспорт") or fields.get("passport") or {}
        if isinstance(raw_pass, dict):
            gpt_passport = dict(raw_pass)
        raw_plan = fields.get("площадка")
        if isinstance(raw_plan, dict):
            gpt_passport["площадка"] = raw_plan
        if operator_prompt:
            cards = scene_cards_from_gpt(cards_raw, place, vo)
            if cards and not is_raw_prompt_dump(cards, operator_prompt):
                nodes.append(
                    _node(
                        "fw_action",
                        "ok",
                        f"сцена по заказу: {len(cards)} кадр(ов), не закадр и не цитата промта",
                    )
                )
                return cards, gpt_passport
            note = "GPT списал заказ в действие" if cards else "GPT не поставил сцену"
            continue
        cards = fit_cards_to_units(cards_raw, units, place)
        if cards:
            cards = cap_cards_to_budget(cards, units)
            steps = sum(len(_card_steps(c)) for c in cards)
            nodes.append(
                _node(
                    "fw_action",
                    "ok",
                    f"монтажная фраза: {steps} шагов на {len(cards)} якор(ь/я/ей)",
                )
            )
            return cards, gpt_passport
        if cards_raw and not note:
            note = f"GPT вернул {len(cards_raw)} карточек на {len(units)} якорей"
        break
    cards = fallback_cards(parent, units, operator_prompt, place)
    nodes.append(
        _node(
            "fw_action",
            "fallback",
            (note or "GPT не вернул цепь") + " — запасной разбор",
        )
    )
    return cards, gpt_passport


# --------------------------------------------------------------------------- #
# fw_shots — покрытие кадра
# --------------------------------------------------------------------------- #


def canonical_plan(value: Any) -> str:
    raw = _norm(value).upper()
    if not raw:
        return ""
    if raw in COVERAGE_PLAN_CHOICES:
        return raw
    if raw in _PLAN_ALIASES:
        return _PLAN_ALIASES[raw]
    for choice in COVERAGE_PLAN_CHOICES:
        if raw.startswith(choice):
            return choice
    return ""


def canonical_angle(value: Any) -> str:
    raw = _norm(value).casefold()
    if not raw:
        return ""
    for choice in COVERAGE_ANGLE_CHOICES:
        if raw == choice.casefold():
            return choice
    if raw in _ANGLE_ALIASES:
        return _ANGLE_ALIASES[raw]
    for alias, choice in _ANGLE_ALIASES.items():
        if alias in raw:
            return choice
    for choice in COVERAGE_ANGLE_CHOICES:
        if choice.casefold() in raw:
            return choice
    return ""


def canonical_move(value: Any) -> str:
    raw = _norm(value).casefold()
    for choice in COVERAGE_MOVE_CHOICES:
        if raw == choice or raw.startswith(choice):
            return choice
    return ""


def canonical_stitch_key(value: Any) -> str:
    raw = _norm(value)
    if not raw:
        return ""
    key = canonical_stitch(raw)
    if key in STITCH_KEYS:
        return key
    low = raw.casefold()
    if "жест" in low or "действ" in low or "движен" in low:
        return "cut_on_action"
    if "взгляд" in low:
        return "eyeline"
    if "прям" in low or "склейк" in low:
        return "cut"
    return ""


def _pack_to_ui(pack: dict[str, Any]) -> dict[str, str]:
    plan = canonical_plan(pack.get("план")) or "СРЕДНИЙ"
    point = str(pack.get("точка") or "")
    height = str(pack.get("высота") or "")
    angle = canonical_angle(point) or "фронт"
    if height in {"сверху", "снизу"} and angle == "фронт":
        angle = height
    move = canonical_move(pack.get("движение")) or "статика"
    return {"план": plan, "ракурс": angle, "движение": move}


def _default_role(i: int, n: int, obj: str, place_new: bool) -> str:
    if i == 0 and place_new:
        return "вход"
    if obj == "лицо":
        return "реакция"
    if obj == "предмет":
        return "перебивка" if 0 < i < n - 1 else "действие"
    if i == n - 1 and n > 2:
        return "следствие"
    return "действие"


def _default_stitch(i: int, shot: dict[str, Any], prev: dict[str, Any] | None) -> str:
    if i == 0 or prev is None:
        return "cut"
    if str(prev.get("объект") or "") == "взгляд":
        return "eyeline"
    step = str(shot.get("действие") or "")
    if re.search(r"вход|вош|выход|выш|бег|ид[её]т|откр|закр|доста|полож|бер[её]т|взял", step, re.I):
        return "cut_on_action"
    return "cut"


def _split_place(raw: str, place: str) -> tuple[str, str]:
    """(место сцены, зона). Место паспорта не меняется; «дом: коридор» → зона."""
    text = _norm(raw)
    if not place:
        return text, ""
    if not text or text.casefold() == place.casefold():
        return place, ""
    head, _sep, tail = text.partition(":") if ":" in text else text.partition("·")
    if tail and head.strip().casefold() == place.casefold():
        return place, _norm(tail)
    if text.casefold().startswith(place.casefold()):
        return place, _norm(text[len(place) :].strip(" ,:·—-"))
    return place, text


def _anchor_n(row: dict[str, Any], k: int) -> int | None:
    for key in ("якорь_n", "якорь", "n_якоря"):
        raw = row.get(key)
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= k:
            return n
    return None


def normalize_shots(
    shots: list[Any],
    *,
    cell_number: int,
    place: str,
    anchors: int = 1,
) -> list[dict[str, Any]]:
    """Кадры GPT → enum доски; пустое покрытие дописывает таблица грамматики."""
    rows = [
        dict(s)
        for s in shots
        if isinstance(s, dict) and _norm(s.get("действие") or s.get("action"))
    ]
    out: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    n = len(rows)
    for i, row in enumerate(rows):
        step = _norm(row.get("действие") or row.get("action"))
        shot_place, zone = _split_place(_norm(row.get("место") or row.get("place")), place)
        obj = _norm(row.get("объект")).casefold()
        if obj not in OBJECTS:
            obj = classify_object(step)
        entrance = is_establishing_step(step)
        place_new = entrance
        pos = (
            "вход"
            if entrance
            else ("пик" if i == n - 1 and obj in {"лицо", "предмет", "взгляд"} else "развитие")
        )
        pack = _pack_to_ui(
            camera_pack(
                obj=obj,
                place_new=place_new,
                position=pos,
                hod=is_walk_step(step),
                prev=(
                    {
                        "план": prev.get("план"),
                        "точка": prev.get("ракурс"),
                        "линза_мм": 0,
                    }
                    if prev
                    else None
                ),
            )
        )
        role = _norm(row.get("роль")).casefold()
        shot = {
            "id": f"{int(cell_number)}-S1-K{i + 1}",
            "parent_id": None if i == 0 else f"{int(cell_number)}-S1-K1",
            "порядок": i + 1,
            "сцена": 1,
            "якорь_n": _anchor_n(row, anchors) if anchors > 1 else 1,
            "место": f"{shot_place}: {zone}" if shot_place and zone else shot_place,
            "зона": _norm(row.get("зона")) or zone,
            "действие": step,
            "объект": obj,
            "роль": role if role in SHOT_ROLES else _default_role(i, n, obj, place_new),
            "план": canonical_plan(row.get("план")) or pack["план"],
            "ракурс": canonical_angle(row.get("ракурс")) or pack["ракурс"],
            "движение": canonical_move(row.get("движение")) or pack["движение"],
            "закадр": _norm(row.get("закадр")),
        }
        shot["стык"] = (
            canonical_stitch_key(row.get("стык") or row.get("переход"))
            or _default_stitch(i, shot, prev)
        )
        if i == 0:
            shot["стык"] = "cut"
        out.append(shot)
        prev = shot
    return vary_adjacent_coverage(out)


def anchors_grouped(shots: list[dict[str, Any]], k: int) -> bool:
    """Кадры идут по якорям подряд 1..k, у каждого якоря есть кадр."""
    seq = [s.get("якорь_n") for s in shots]
    if any(not isinstance(v, int) for v in seq):
        return False
    return seq == sorted(seq) and set(seq) == set(range(1, k + 1))


def shots_list_from_reply(fields: dict[str, Any], reply: str) -> list[Any]:
    """кадры / shots из apply-ops, голого JSON или массива."""
    for key in ("кадры", "shots"):
        val = fields.get(key)
        if isinstance(val, list) and val:
            return val
    extra = _ops_fields(reply or "", "")
    if extra and extra is not fields:
        for key in ("кадры", "shots"):
            val = extra.get(key)
            if isinstance(val, list) and val:
                return val
    data = _loose_json_dict(reply or "")
    if isinstance(data, dict):
        for key in ("кадры", "shots"):
            val = data.get(key)
            if isinstance(val, list) and val:
                return val
        arr = data.get("data")
        if isinstance(arr, list) and arr:
            return arr
    return []


def merge_shot_coverage(
    canonical: list[dict[str, Any]],
    raw: list[Any],
    *,
    cell_number: int,
    place: str,
) -> list[dict[str, Any]] | None:
    """Покрытие GPT накладываем на шаги действия, даже если в ответе нет текста."""
    if not canonical or not isinstance(raw, list) or len(raw) != len(canonical):
        return None
    rows: list[dict[str, Any]] = []
    for ref, item in zip(canonical, raw, strict=True):
        row = {
            "действие": ref.get("действие"),
            "место": ref.get("место"),
            "якорь_n": ref.get("якорь_n"),
        }
        if isinstance(item, str) and _norm(item):
            row["действие"] = _norm(item)
        elif isinstance(item, dict):
            act = _norm(item.get("действие") or item.get("action"))
            if act:
                row["действие"] = act
            for src, dst in (
                ("план", "план"),
                ("plan", "план"),
                ("ракурс", "ракурс"),
                ("angle", "ракурс"),
                ("движение", "движение"),
                ("move", "движение"),
                ("стык", "стык"),
                ("переход", "стык"),
                ("роль", "роль"),
                ("объект", "объект"),
            ):
                val = item.get(src)
                if val:
                    row[dst] = val
        rows.append(row)
    out = normalize_shots(
        rows, cell_number=cell_number, place=place, anchors=len(canonical)
    )
    return out if len(out) == len(canonical) else None


def coverage_is_flat(shots: list[dict[str, Any]]) -> bool:
    """Одинаковая крупность на 2+ кадрах — чипы на доске не двигаются."""
    if len(shots) < 2:
        return False
    plans = {_norm(s.get("план")) for s in shots if _norm(s.get("план"))}
    return len(plans) < 2


_ANGLE_LADDER = ("3/4", "с плеча", "фронт", "сверху")


def diversify_shot_coverage(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Лестница покрытия: первый общий, последний крупнее, ракурсы разные."""
    n = len(shots)
    if n < 2:
        return shots
    for i, shot in enumerate(shots):
        act = _norm(shot.get("действие"))
        obj = _norm(shot.get("объект")).casefold() or classify_object(act)
        if i == 0:
            plan = "ОБЩИЙ"
            angle = "3/4"
            move = "следование" if is_walk_step(act) else "статика"
        elif i == n - 1:
            if obj == "предмет":
                plan, angle, move = "ДЕТАЛЬ", "сверху", "статика"
            elif obj == "лицо":
                plan, angle, move = "КРУПНЫЙ", "фронт", "наезд"
            else:
                plan, angle, move = "КРУПНЫЙ", "с плеча", "статика"
        else:
            plan = "СРЕДНИЙ"
            angle = _ANGLE_LADDER[i % len(_ANGLE_LADDER)]
            move = "статика"
        prev = shots[i - 1] if i else None
        if prev and _norm(prev.get("план")) == plan and _norm(prev.get("ракурс")) == angle:
            angle = _ANGLE_LADDER[(i + 1) % len(_ANGLE_LADDER)]
        shot["план"] = plan
        shot["ракурс"] = angle
        shot["движение"] = move
        if obj:
            shot["объект"] = obj
    return shots


def finish_shots(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if coverage_is_flat(shots):
        diversify_shot_coverage(shots)
    smooth_plan_sequence(shots)
    return shots


def shots_from_cards(
    cards: list[dict[str, Any]], *, cell_number: int, place: str
) -> list[dict[str, Any]]:
    """Код fw_shots без GPT: шаги карточек → кадры, камера из таблицы."""
    rows = [
        {"действие": step, "место": card.get("place") or place, "якорь_n": card["n"]}
        for card in cards
        for step in _card_steps(card)
    ]
    return normalize_shots(rows, cell_number=cell_number, place=place, anchors=len(cards))


def build_improve_shots_prompt(
    *,
    parent: Frame,
    vo: str,
    cards: list[dict[str, Any]],
    units: list[dict[str, Any]],
    passport: dict[str, str],
) -> str:
    task = (
        "Нода fw_shots группы script_frames_qc. "
        "Главное_действие этой ячейки → кадры. Один шаг `→` = один кадр, "
        "порядок тот же. Новых кадров не добавляй.\n"
        "Для каждого кадра заполни покрытие: `роль`, `план`, `ракурс`, "
        "`движение`, `стык` — только из списков "
        f"(роли: {', '.join(SHOT_ROLES)}; план: {', '.join(COVERAGE_PLAN_CHOICES)}; "
        f"ракурс: {', '.join(COVERAGE_ANGLE_CHOICES)}; "
        f"движение: {', '.join(COVERAGE_MOVE_CHOICES)}).\n"
        "`роль` — метка покрытия, не повод выдумать кадр.\n"
        "Якорей, паспорта и закадра как сюжета нет — не добавляй."
    )
    _ = passport, vo, units
    payload = {
        "frame_uuid": str(parent.uuid or ""),
        "frame_number": int(parent.number),
        "главное_действие": format_scene_chain(
            [
                {
                    "n": card.get("n"),
                    "place": card.get("place"),
                    "action": card.get("action"),
                }
                for card in cards
            ]
        ),
    }
    return _compose(IMPROVE_SHOTS_RULES, "", task, payload, operator_first=True)


async def stage_shots(
    ask: AskFn,
    *,
    parent: Frame,
    vo: str,
    cards: list[dict[str, Any]],
    units: list[dict[str, Any]],
    passport: dict[str, str],
    nodes: list[dict[str, Any]],
    from_prompt: bool = False,
) -> list[dict[str, Any]]:
    place = passport.get("место") or ""
    k = len(units)
    canonical = shots_from_cards(cards, cell_number=int(parent.number), place=place)
    if from_prompt:
        nodes.append(
            _node("fw_shots", "ok", f"кадров: {len(canonical)} — шаги промта, не закадр")
        )
        return finish_shots(canonical)
    prompt = build_improve_shots_prompt(
        parent=parent, vo=vo, cards=cards, units=units, passport=passport
    )
    note = ""
    try:
        reply = await ask(prompt)
    except Exception as exc:  # noqa: BLE001
        reply = ""
        note = f"GPT: {type(exc).__name__}: {exc}"[:160]
    fields = _ops_fields(reply, str(parent.uuid or ""))
    raw = shots_list_from_reply(fields, reply)
    shots = (
        normalize_shots(raw, cell_number=int(parent.number), place=place, anchors=k)
        if raw
        else []
    )
    if shots and len(shots) == len(canonical):
        for shot, ref in zip(shots, canonical, strict=True):
            if not shot.get("якорь_n"):
                shot["якорь_n"] = ref.get("якорь_n")
            if not _norm(shot.get("действие")):
                shot["действие"] = ref.get("действие")
        nodes.append(_node("fw_shots", "ok", f"кадров: {len(shots)}, покрытие от GPT"))
        return finish_shots(shots)
    merged = merge_shot_coverage(
        canonical, raw, cell_number=int(parent.number), place=place
    )
    if merged:
        nodes.append(
            _node("fw_shots", "ok", f"кадров: {len(merged)}, покрытие GPT на шаги действия")
        )
        return finish_shots(merged)
    if raw and not note:
        note = "число кадров GPT не совпало с действием"
    if reply and not raw:
        logger.warning(
            "improve fw_shots: нет списка кадров ({} симв): {}",
            len(reply or ""),
            _norm(reply)[:300],
        )
    nodes.append(
        _node(
            "fw_shots",
            "fallback",
            (note or "GPT не вернул кадры") + f" — {len(canonical)} кадров по таблице камеры",
        )
    )
    return finish_shots(canonical)


# --------------------------------------------------------------------------- #
# fw_qc — код чинит, GPT только если брак остался
# --------------------------------------------------------------------------- #


_COGNITIVE_ONLY_RE = re.compile(
    r"^(?:\S+\s+)?(?:понимает|узнаёт|узнает|думает|осознаёт|осознает|вспоминает|знает|решает)\b",
    re.IGNORECASE,
)


def split_vo_for_shots(vo: str, n: int) -> list[str]:
    """Дословные куски закадра на n кадров; при хватке слов — ни одного пустого."""
    from app.services.scene_design.camera_expand import split_text_into_parts

    text = _norm(vo)
    if n <= 1:
        return [text]
    parts = [_norm(p) for p in split_text_into_parts(text, n)]
    words = text.split()
    if all(parts) or len(words) < n:
        return parts
    total = sum(len(w) + 1 for w in words)
    out: list[str] = []
    start = 0
    for k in range(n):
        remaining_shots = n - k
        remaining_words = len(words) - start
        if remaining_shots == 1:
            take = remaining_words
        else:
            target = total * (k + 1) / n
            acc = sum(len(w) + 1 for w in words[:start])
            take = 1
            while (
                start + take < len(words)
                and remaining_words - take >= remaining_shots - 1
                and acc + sum(len(w) + 1 for w in words[start : start + take + 1]) <= target
            ):
                take += 1
        out.append(" ".join(words[start : start + take]))
        start += take
    return out


def _glue(shots: list[dict[str, Any]]) -> str:
    return " ".join(_norm(s.get("закадр")) for s in shots if _norm(s.get("закадр")))


def _group(shots: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    return [s for s in shots if s.get("якорь_n") == n]


def _trim_to_cap(shots: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """Сверх бюджета режем мосты и перебивки, исходные события и следствие держим."""
    out = list(shots)
    for role in ("мост", "перебивка"):
        i = len(out) - 1
        while len(out) > cap and i >= 1:
            if out[i].get("роль") == role:
                out.pop(i)
            i -= 1
    return out[:cap] if len(out) > cap else out


def repair_shots(shots: list[dict[str, Any]], units: list[dict[str, Any]]) -> list[str]:
    """Детерминированные правки fw_qc по якорям. Возвращает, что починено."""
    fixed: list[str] = []
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for shot in shots:
        stem = action_stem(str(shot.get("действие") or ""))
        alone = len(_group(shots, shot.get("якорь_n"))) <= 1
        if stem and stem in seen and not alone:
            fixed.append(f"убран повтор «{stem[:40]}»")
            continue
        if stem:
            seen.add(stem)
        kept.append(shot)
    rebuilt: list[dict[str, Any]] = []
    for unit in units:
        group = _group(kept, unit["n"])
        if len(group) > unit["бюджет"]:
            fixed.append(f"якорь {unit['n']}: кадров {len(group)} > {unit['бюджет']} — мосты сжаты")
            group = _trim_to_cap(group, unit["бюджет"])
        piece = _norm(unit["закадр"])
        empty = any(not _norm(s.get("закадр")) for s in group)
        if group and (_glue(group) != piece or (empty and len(piece.split()) >= len(group))):
            for shot, part in zip(group, split_vo_for_shots(piece, len(group)), strict=False):
                shot["закадр"] = part
            fixed.append(f"якорь {unit['n']}: закадр кадров = кусок якоря дословно")
        rebuilt.extend(group)
    shots[:] = rebuilt
    fixed.extend(smooth_plan_sequence(shots))
    prev: dict[str, Any] | None = None
    for shot in shots:
        if prev is not None and shot.get("объект") == prev.get("объект"):
            if shot.get("план") == prev.get("план") and shot.get("ракурс") == prev.get("ракурс"):
                alt = "3/4" if shot.get("ракурс") != "3/4" else "фронт"
                shot["ракурс"] = alt
                fixed.append(f"30°: кадр {shot.get('порядок')} ракурс → {alt}")
        if shot.get("объект") == "предмет" and shot.get("ракурс") == "с плеча":
            shot["ракурс"] = "сверху"
            fixed.append(f"кадр {shot.get('порядок')}: предмет не с плеча → сверху")
        prev = shot
    for i, shot in enumerate(shots, start=1):
        shot["порядок"] = i
        shot["id"] = re.sub(r"K\d+$", f"K{i}", str(shot.get("id") or f"1-S1-K{i}"))
        if i == 1:
            shot["parent_id"] = None
            shot["стык"] = "cut"
    if shots:
        master = str(shots[0].get("id") or "")
        for shot in shots[1:]:
            shot["parent_id"] = master
    return fixed


def qc_reasons(
    shots: list[dict[str, Any]], units: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """(брак, замечания) по разделу режиссуры и shots_qc_ru."""
    hard: list[str] = []
    soft: list[str] = []
    if not shots:
        return ["пустые кадры"], soft
    if not anchors_grouped(shots, len(units)):
        hard.append("кадры не разложены по якорям подряд")
    for unit in units:
        group = _group(shots, unit["n"])
        if group and _glue(group) != _norm(unit["закадр"]):
            hard.append(f"якорь {unit['n']}: склейка закадра кадров ≠ куску якоря")
        if len(_norm(unit["закадр"]).split()) >= len(group):
            for shot in group:
                if not _norm(shot.get("закадр")):
                    hard.append(f"кадр {shot.get('порядок')}: пустой закадр")
    stems: set[str] = set()
    prev: dict[str, Any] | None = None
    for shot in shots:
        n = shot.get("порядок")
        step = str(shot.get("действие") or "")
        if _COGNITIVE_ONLY_RE.search(step):
            hard.append(f"кадр {n}: вывод вместо видимого действия «{step[:40]}»")
        stem = action_stem(step)
        if stem in stems:
            soft.append(f"кадр {n}: повтор действия «{stem[:40]}»")
        stems.add(stem)
        for key, allowed in (
            ("план", COVERAGE_PLAN_CHOICES),
            ("ракурс", COVERAGE_ANGLE_CHOICES),
            ("движение", COVERAGE_MOVE_CHOICES),
            ("стык", STITCH_KEYS),
        ):
            if shot.get(key) not in allowed:
                hard.append(f"кадр {n}: {key} «{shot.get(key)}» не из списка")
        if prev is not None:
            a = _PLAN_STEP.get(str(prev.get("план") or ""), -1)
            b = _PLAN_STEP.get(str(shot.get("план") or ""), -1)
            if a >= 0 and b >= 0 and abs(a - b) >= 3 and prev.get("объект") == shot.get("объект"):
                soft.append(
                    f"кадр {n}: {prev.get('план')} → {shot.get('план')} одного объекта — не «через план»"
                )
            wide = wide_return_reason(shot, prev)
            if wide:
                hard.append(f"кадр {n}: {wide}")
            if (
                shot.get("объект") == prev.get("объект")
                and shot.get("план") == prev.get("план")
                and shot.get("ракурс") == prev.get("ракурс")
            ):
                hard.append(f"кадр {n}: план и ракурс как у предыдущего (30°)")
        prev = shot
    return hard, soft


def build_improve_qc_prompt(
    *,
    parent: Frame,
    vo: str,
    shots: list[dict[str, Any]],
    units: list[dict[str, Any]],
    reasons: list[str],
) -> str:
    task = (
        "Нода fw_qc группы script_frames_qc. Код нашёл брак:\n- "
        + "\n- ".join(reasons)
        + "\nВерни тот же список `кадры` с покрытием. "
        "Кадры не добавляй и не убирай. Сюжет и число кадров не меняй. "
        "Якорей и закадра как сюжета нет."
    )
    payload = {
        "frame_uuid": str(parent.uuid or ""),
        "frame_number": int(parent.number),
        "кадры": shots,
    }
    _ = vo, units
    return _compose(load_group_prompt("shots_qc_ru"), "", task, payload)


async def stage_qc(
    ask: AskFn,
    *,
    parent: Frame,
    vo: str,
    shots: list[dict[str, Any]],
    units: list[dict[str, Any]],
    place: str,
    nodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    fixed = repair_shots(shots, units)
    hard, soft = qc_reasons(shots, units)
    status = "fixed" if fixed else "ok"
    notes = list(fixed)
    if hard:
        try:
            reply = await ask(
                build_improve_qc_prompt(
                    parent=parent, vo=vo, shots=shots, units=units, reasons=hard
                )
            )
        except Exception as exc:  # noqa: BLE001
            reply = ""
            notes.append(f"GPT QC: {type(exc).__name__}"[:80])
        raw = _ops_fields(reply, str(parent.uuid or "")).get("кадры")
        if isinstance(raw, list) and raw:
            candidate = normalize_shots(
                raw, cell_number=int(parent.number), place=place, anchors=len(units)
            )
            if candidate and len(candidate) == len(shots):
                extra = repair_shots(candidate, units)
                c_hard, c_soft = qc_reasons(candidate, units)
                if len(c_hard) < len(hard):
                    shots, hard, soft = candidate, c_hard, c_soft
                    notes.extend(extra)
                    notes.append("GPT QC исправил кадры")
                    status = "fixed"
        if hard:
            status = "warn"
    shots = finish_shots(shots)
    nodes.append(_node("fw_qc", status, "; ".join(notes + hard)[:400] or "брака нет"))
    return shots, soft


def neighbour_scene_cells(frames: list[Any], parent: Any) -> tuple[list[dict], list[dict]]:
    """Соседние сцены (прошлая, следующая) — контекст площадки только для чтения."""
    from app.services.apply_ops_batches import _frame_shots, _is_child_frame

    rows = [
        {"uuid": str(fr.uuid), "attrs": dict(fr.attrs or {})}
        for fr in frames
        if getattr(fr, "uuid", None)
    ]
    rows = [r for r in rows if not _is_child_frame(r)]
    idx = next((i for i, r in enumerate(rows) if r["uuid"] == str(parent.uuid)), None)
    if idx is None:
        return [], []

    def cell(r: dict[str, Any]) -> list[dict[str, Any]]:
        shots = _frame_shots(r)
        if not shots:
            return []
        return [{
            "key": r["uuid"],
            "shots": shots,
            "plan": r["attrs"].get("площадка"),
            "owned": False,
        }]

    before = cell(rows[idx - 1]) if idx > 0 else []
    after = cell(rows[idx + 1]) if idx + 1 < len(rows) else []
    return before, after


def stage_scene_plan(
    shots: list[dict[str, Any]],
    raw_plan: Any,
    nodes: list[dict[str, Any]],
    *,
    before: list[dict[str, Any]] | None = None,
    after: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Площадка после fw_qc: двери, порог, путь, ось — и раскладка кадров."""
    if before or after:
        own = {"key": "_", "shots": shots, "plan": raw_plan, "owned": True}
        base, by_key = apply_scene_plan_cells([*(before or []), own, *(after or [])])
        issues = by_key.get("_", [])
        plan = with_plan_notes(base, raw_plan, issues, shots)
    else:
        plan, issues = apply_scene_plan(shots, raw_plan)
    fixed = plan_fix_notes(issues)
    hard = plan_hard_reasons(issues)
    note = "площадка: " + (
        "; ".join(fixed + hard)
        or f"зон {len(plan.get('зоны') or [])}, проходов {len(plan.get('проходы') or [])}"
    )
    qc = next((n for n in reversed(nodes) if n.get("node") == "fw_qc"), None)
    if qc is None:
        nodes.append(_node("fw_qc", "warn" if hard else "ok", note[:400]))
        return plan
    base = str(qc.get("note") or "")
    qc["note"] = (f"{base} · {note}" if base else note)[:600]
    if hard:
        qc["status"] = "warn"
    elif fixed and qc.get("status") == "ok":
        qc["status"] = "fixed"
    return plan


# --------------------------------------------------------------------------- #
# Паспорт → доска
# --------------------------------------------------------------------------- #

_LOCKED_KEYS = ("place", "characters", "set", "light", "visual_type")
_PASSPORT_RU = {
    "sense": "смысл",
    "visual_type": "тип",
    "place": "место",
    "set": "набор",
    "characters": "персонажи",
    "light": "свет",
    "props": "предметы",
    "bg": "фон",
    "accent": "акцент",
    "feature": "особенность",
}


def merge_passport(
    operator: dict[str, Any], gpt: dict[str, Any]
) -> tuple[dict[str, str], list[str]]:
    """Паспорт после улучшения: место/набор/персонажи/свет/тип оператора держим,
    описательные поля — от GPT (или оператора, если GPT пусто)."""
    out: dict[str, str] = {}
    changed: list[str] = []
    for key, ru in _PASSPORT_RU.items():
        mine = str(operator.get(key) or operator.get(ru) or "").strip()
        theirs = _norm(gpt.get(ru) or gpt.get(key))
        if key == "light" and theirs and theirs not in COVERAGE_LIGHT_CHOICES:
            theirs = ""
        if key == "visual_type" and theirs and theirs not in COVERAGE_VISUAL_TYPE_CHOICES:
            theirs = ""
        value = mine if key in _LOCKED_KEYS and mine else (theirs or mine)
        if value:
            out[key] = value
            if value != mine:
                changed.append(key)
    return out, changed


# --------------------------------------------------------------------------- #
# Пайплайн
# --------------------------------------------------------------------------- #


def _node(key: str, status: str, note: str) -> dict[str, Any]:
    label = dict(GROUP_NODES).get(key, key)
    return {"node": key, "label": label, "status": status, "note": note}


async def _load_frames(session: AsyncSession, project_id: int) -> list[Frame]:
    return list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project_id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )


def _final_chain(shots: list[dict[str, Any]], units: list[dict[str, Any]], place: str) -> str:
    """главное_действие после QC: карточка N. на якорь, шаги = кадры, закадр = кусок."""
    return format_scene_chain(
        [
            {
                "n": u["n"],
                "place": place or _norm(_group(shots, u["n"])[0].get("место") if _group(shots, u["n"]) else ""),
                "action": " → ".join(_norm(s.get("действие")) for s in _group(shots, u["n"])),
                "vo": u["закадр"],
            }
            for u in units
            if _group(shots, u["n"])
        ]
    )


async def improve_cell_scene(
    session: AsyncSession,
    project: Project,
    frame_id: int,
    *,
    operator_prompt: str = "",
    passport: dict[str, Any] | None = None,
    anchors: list[Any] | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """fw_action → fw_shots → fw_qc на одну ячейку. Якоря и паспорт не входят."""
    from app.services.gpt_client import gpt_ask_fresh
    from app.services.montage_action_gpt import apply_cell_passport
    from app.services.montage_coverage_ops import _visible_cell_members
    from app.services.montage_scene_direct import build_kadry, pick_scene_characters
    from app.services.montage_scene_editor import cell_full_text, scene_group
    from app.services.scene_character_match import (
        format_character_codes,
        format_character_labels,
        load_character_registry,
        match_registry_characters,
    )
    from app.services.vo_shot_expand import _flag_attrs, _set_cs

    _ = anchors, passport
    frames = await _load_frames(session, int(project.id))
    frame = next((fr for fr in frames if int(fr.id) == int(frame_id)), None)
    if frame is None:
        raise RuntimeError(f"кадр {frame_id} не найден")
    parent, members = scene_group(frames, frame)
    vo = _norm(cell_full_text(parent, members))
    if not vo:
        raise RuntimeError("у сцены нет закадрового текста")
    op_prompt = _norm(operator_prompt)
    entities = await load_character_registry(session, int(project.id))
    haystack = f"{op_prompt}\n{vo}"
    matched = match_registry_characters(haystack, entities)
    registry_labels = ", ".join(
        f"{str(e.code or '').strip()} · {str(e.name or '').strip()}".strip(" ·")
        for e in entities
        if str(getattr(e, "code", "") or "").strip()
        or str(getattr(e, "name", "") or "").strip()
    )
    matched_labels = format_character_labels(matched)

    async def ask(text: str) -> str:
        return await gpt_ask_fresh(
            text, timeout=timeout, project_id=int(project.id)
        )

    nodes: list[dict[str, Any]] = []
    action_extra: dict[str, Any] = {}
    if op_prompt:
        cards, action_extra = await stage_action(
            ask,
            parent=parent,
            vo=vo,
            units=[],
            passport={},
            operator_prompt=op_prompt,
            nodes=nodes,
            registry_labels=registry_labels,
            matched_labels=matched_labels,
        )
    else:
        kadry_fb = build_kadry(
            vo=vo,
            place="",
            parent_plan="ОБЩИЙ",
            parent_action="",
            shots=[],
            cell_number=int(parent.number),
            operator_prompt="",
        )
        cards = [
            {
                "n": i + 1,
                "place": "",
                "action": str(shot.get("действие") or ""),
                "vo": str(shot.get("закадр") or ""),
            }
            for i, shot in enumerate(kadry_fb)
        ]
        nodes.append(
            _node("fw_action", "fallback", "без заказа — нарезка по закадру, GPT не звали")
        )

    if not cards:
        raise RuntimeError("не удалось поставить сцену")
    if op_prompt:
        cards = scene_cards_from_gpt(cards, "", vo) or cards
    units = units_from_cards(cards)
    shots = await stage_shots(
        ask,
        parent=parent,
        vo=vo,
        cards=cards,
        units=units,
        passport={},
        nodes=nodes,
        from_prompt=not bool(op_prompt),
    )
    shots, warnings = await stage_qc(
        ask,
        parent=parent,
        vo=vo,
        shots=shots,
        units=units,
        place="",
        nodes=nodes,
    )
    if not shots:
        raise RuntimeError("не удалось нарезать сцену на кадры")
    before, after = neighbour_scene_cells(frames, parent)
    scene_plan = stage_scene_plan(
        shots,
        action_extra.get("площадка") or (parent.attrs or {}).get("площадка"),
        nodes,
        before=before,
        after=after,
    )

    act_blob = " ".join(
        _norm(s.get("действие")) or _norm(c.get("action"))
        for s, c in zip(shots, cards)
    ) or " ".join(_norm(c.get("action")) for c in cards)
    chars = pick_scene_characters(
        haystack=f"{haystack}\n{act_blob}",
        gpt_raw="",
        entities=entities,
    )
    codes = format_character_codes(chars)
    labels = format_character_labels(chars)
    nodes.append(
        _node("characters", "ok", labels or "реестр: никого в тексте")
    )

    chain_text = _final_chain(shots, units, "")
    frames = await _load_frames(session, int(project.id))
    parent, _members = scene_group(frames, parent)
    apply_report = await apply_coverage_scene_action(
        session,
        project,
        parent,
        frames,
        chain_text,
        grow=True,
        kadry=shots,
        lock_parent_plan=False,
    )
    await session.flush()
    frames = await _load_frames(session, int(project.id))
    parent, members = scene_group(frames, parent)
    visible = _visible_cell_members(parent, members)
    applied = apply_cell_passport(parent, frames, {})
    attrs = dict(parent.attrs or {})
    if codes:
        attrs["персонажи"] = codes
        attrs["персонажи_сцены"] = codes
        attrs["characters"] = codes
    attrs["scene_summary"] = op_prompt or vo[:180]
    parent_plan = str((shots[0] or {}).get("план") or "СРЕДНИЙ")
    report = {
        "nodes": nodes,
        "сцена": op_prompt or vo[:180],
        "characters": chars,
        "shots": shots,
        "passport": {},
        "warnings": list(warnings or []),
    }
    attrs["parent_still_plan"] = parent_plan
    attrs["площадка"] = scene_plan
    attrs[REPORT_ATTR] = report
    parent.attrs = attrs
    _flag_attrs(parent)
    _set_cs(parent, coverage_kind="parent", use_parent_still=False)
    for member in members:
        if int(member.id) == int(parent.id):
            continue
        extra = {"coverage_kind": "child", "use_parent_still": True}
        if codes:
            mattrs = dict(member.attrs or {})
            mattrs["персонажи"] = codes
            mattrs["characters"] = codes
            member.attrs = mattrs
            _flag_attrs(member)
        _set_cs(member, **extra)
    await session.flush()
    logger.info(
        "montage group-improve #{} cell={} chars={} shots={} inserted={}",
        project.id,
        parent.number,
        codes,
        len(shots),
        apply_report.get("inserted_frames"),
    )
    return {
        "ok": True,
        "mode": "improve",
        "frame_id": int(parent.id),
        "frame_number": int(parent.number),
        "replace_ns": [],
        "passport_applied": applied,
        "chain": apply_report.get("chain") or chain_text,
        "shots": apply_report.get("shots"),
        "skipped_shots": apply_report.get("skipped_shots"),
        "inserted_frames": apply_report.get("inserted_frames"),
        "frame_numbers": [int(m.number) for m in visible],
        "report": apply_report,
        "improve_report": report,
        "image_ops": [],
        "images": 0,
    }
