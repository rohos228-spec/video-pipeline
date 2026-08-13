"""Реестр агентов scene_design: промпты, парсеры и валидаторы JSON-срезов."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from app.project_root import find_project_root

PROMPTS_SUBDIR = Path("prompts") / "scene_design"

ASSEMBLER = "assemble"
SKELETON = "skeleton"
CATEGORY_AGENTS: tuple[str, ...] = ("characters", "world", "style", "camera", "action")
# Все GPT-агенты веера, включая волну 0 (скелет).
ALL_AGENTS: tuple[str, ...] = (SKELETON, *CATEGORY_AGENTS)
# Legacy: chars/world/style/action параллельно → camera.
WAVE1_AGENTS: tuple[str, ...] = ("characters", "world", "style", "action")
WAVE2_AGENTS: tuple[str, ...] = ("camera",)
# chrono_dyn: верхние 3 → action → camera (как на канвасе).
CHRONO_WAVE1_AGENTS: tuple[str, ...] = ("characters", "world", "style")
CHRONO_WAVE2_AGENTS: tuple[str, ...] = ("action",)
CHRONO_WAVE3_AGENTS: tuple[str, ...] = ("camera",)
SKELETON_WAVE: tuple[str, ...] = (SKELETON,)


def project_scene_design_variant(project: Any | None) -> str:
    """``meta.scene_design_variant`` или вывод из prompt_slot_variants (*_chrono_dyn)."""
    if project is None:
        return ""
    meta = getattr(project, "meta", None)
    if not isinstance(meta, dict):
        return ""
    raw = str(meta.get("scene_design_variant") or "").strip()
    if raw:
        return raw
    slots = meta.get("prompt_slot_variants")
    if isinstance(slots, dict):
        for per in slots.values():
            if not isinstance(per, dict):
                continue
            for v in per.values():
                name = str(v or "")
                if name.endswith("_chrono_dyn") or "chrono_dyn" in name:
                    return "chrono_dyn"
    return ""


def uses_chrono_dyn(project: Any | None) -> bool:
    return project_scene_design_variant(project) == "chrono_dyn"


def uses_skeleton(project: Any | None) -> bool:
    """Волна 0 скелета: meta.scene_design_skeleton или нода marker=skeleton."""
    if project is None:
        return False
    meta = getattr(project, "meta", None)
    if not isinstance(meta, dict):
        return False
    if meta.get("scene_design_skeleton") is True:
        return True
    raw = meta.get("scene_design_skeleton")
    if isinstance(raw, str) and raw.strip().lower() in ("1", "true", "yes", "on"):
        return True
    cg = meta.get("canvas_graph")
    if isinstance(cg, dict):
        from app.services.excel_gpt_node import sd_agent_marker

        for n in cg.get("nodes") or []:
            if sd_agent_marker(n) == SKELETON:
                return True
    return False


def agent_waves(project: Any | None) -> tuple[tuple[str, ...], ...]:
    """Порядок волн GPT: опционально skeleton → категорийные."""
    if uses_chrono_dyn(project):
        waves: tuple[tuple[str, ...], ...] = (
            CHRONO_WAVE1_AGENTS,
            CHRONO_WAVE2_AGENTS,
            CHRONO_WAVE3_AGENTS,
        )
    else:
        waves = (WAVE1_AGENTS, WAVE2_AGENTS)
    if uses_skeleton(project):
        return (SKELETON_WAVE, *waves)
    return waves

# Обязательный ключ-список в JSON-срезе агента (непустой при успехе).
LIST_KEY: dict[str, str] = {
    SKELETON: "scenes",
    "characters": "characters",
    "world": "locations",
    "style": "style_arc",
    "camera": "shot_plan",
    "action": "scenes",
    ASSEMBLER: "scenes",
}


class SceneDesignAgentError(RuntimeError):
    """Агент вернул невалидный/пустой JSON-срез."""


def prompts_dir() -> Path:
    return find_project_root() / PROMPTS_SUBDIR


# Доп. файлы, доклеиваемые к базовому промпту агента (каталоги, словари).
PROMPT_INCLUDES: dict[str, tuple[str, ...]] = {
    "camera": ("camera_sets.md",),
}


def _excel_gpt_prompts_dir() -> Path:
    from app.services.prompt_library import step_dir

    return step_dir("excel_gpt")


def _node_prompt_variant(project: Any, agent: str) -> str | None:
    """Вариант промпта, назначенный на ноду агента на канвасе (SSoT —
    meta.prompt_slot_variants[node_key], как у всех нод «Работа с GPT»)."""
    from app.services.excel_gpt_node import sd_agent_marker

    meta = getattr(project, "meta", None)
    if not isinstance(meta, dict):
        return None
    cg = meta.get("canvas_graph")
    if not isinstance(cg, dict):
        return None
    node_key: str | None = None
    for n in cg.get("nodes") or []:
        if sd_agent_marker(n) == agent:
            node_key = str(n.get("id") or "") or None
            break
    if not node_key:
        return None
    slots = meta.get("prompt_slot_variants")
    if not isinstance(slots, dict):
        return None
    per_node = slots.get(node_key)
    if not isinstance(per_node, dict):
        return None
    for slot_id in ("main", "gpt", "prompt"):
        v = str(per_node.get(slot_id) or "").strip()
        if v:
            return v
    return None


def load_prompt(agent: str, project: Any | None = None) -> str:
    """Промпт агента: сначала вариант ноды «Работа с GPT» (05_excel_gpt),
    затем конвенция sd_<агент>.md там же, затем legacy scene_design/<агент>.md.
    """
    text = ""
    excel_dir = _excel_gpt_prompts_dir()
    if project is not None:
        variant = _node_prompt_variant(project, agent)
        if variant:
            from app.services.prompt_library import is_valid_prompt_name

            if is_valid_prompt_name(variant):
                p = excel_dir / f"{variant}.md"
                if p.is_file():
                    text = p.read_text(encoding="utf-8").strip()
    if not text:
        conv = excel_dir / f"sd_{agent}.md"
        if conv.is_file():
            text = conv.read_text(encoding="utf-8").strip()
    if not text:
        path = prompts_dir() / f"{agent}.md"
        if not path.is_file():
            raise SceneDesignAgentError(f"scene_design: нет файла промпта {path}")
        text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SceneDesignAgentError(f"scene_design: пустой промпт агента {agent}")
    for extra_name in PROMPT_INCLUDES.get(agent, ()):
        extra_path = prompts_dir() / extra_name
        if not extra_path.is_file():
            raise SceneDesignAgentError(
                f"scene_design: нет файла включаемого промпта {extra_path}"
            )
        extra = extra_path.read_text(encoding="utf-8").strip()
        if extra:
            text = f"{text}\n\n---\n\n{extra}"
    return text


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_JSON_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def loads_json_loose(raw: str) -> Any:
    """json.loads + снятие /* */ и висячих запятых (модели часто копируют stub)."""
    if not isinstance(raw, str) or not raw.strip():
        raise json.JSONDecodeError("empty", raw or "", 0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        cleaned = _JSON_BLOCK_COMMENT_RE.sub("", raw)
        cleaned = _JSON_TRAILING_COMMA_RE.sub(r"\1", cleaned)
        return json.loads(cleaned)


def extract_json_object(
    text: str, *, marker_keys: tuple[str, ...]
) -> dict[str, Any] | None:
    """Достать JSON-объект из ответа модели (fence или голый JSON с маркером).

    Если в ответе несколько JSON (пример в fence + рабочий объект), берём
    объект с наибольшим числом *непустых* списков по ``marker_keys`` —
    иначе первый fence-stub обнуляет склейку assemble.
    """
    if not text:
        return None
    candidates: list[str] = [m.group(1) for m in _JSON_FENCE_RE.finditer(text)]
    # Все вхождения маркеров (не только первое) — иначе stub-fence раньше
    # рабочего JSON блокирует склейку assemble.
    seen_spans: set[tuple[int, int]] = set()
    for k in marker_keys:
        needle = f'"{k}"'
        start_search = 0
        while True:
            idx = text.find(needle, start_search)
            if idx == -1:
                break
            # НЕ rfind("{") — иначе ключ "scenes" цепляет вложенный объект
            # из map.кадры[...] прямо перед корневым "scenes".
            # Идём влево с балансом скобок → охватывающий объект.
            depth = 0
            start = -1
            for i in range(idx - 1, -1, -1):
                ch = text[i]
                if ch == "}":
                    depth += 1
                elif ch == "{":
                    if depth == 0:
                        start = i
                        break
                    depth -= 1
            if start != -1:
                depth = 0
                for i in range(start, len(text)):
                    ch = text[i]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            span = (start, i + 1)
                            if span not in seen_spans:
                                seen_spans.add(span)
                                candidates.append(text[start : i + 1])
                            break
            start_search = idx + 1
    parsed: list[dict[str, Any]] = []
    for cand in candidates:
        try:
            data = loads_json_loose(cand)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            parsed.append(data)
    if not parsed:
        return None
    if not marker_keys:
        return parsed[0]

    def _score(d: dict[str, Any]) -> int:
        score = 0
        for k in marker_keys:
            v = d.get(k)
            if isinstance(v, list) and v:
                score += 10  # непустой список — главный критерий
            elif isinstance(v, list):
                score += 1
            elif k in d:
                score += 1
        return score

    parsed.sort(key=_score, reverse=True)
    return parsed[0]


def _chain_phases(scene: dict[str, Any]) -> list[Any]:
    raw = scene.get("цепь_действия") or scene.get("chain") or []
    return raw if isinstance(raw, list) else []


_BEAT_OK = frozenset({"setup", "develop", "turn", "payoff"})


_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_PLACE_BUCKETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hospital", re.compile(r"больниц|стационар|палат|психиатр|отделени", re.I)),
    ("police", re.compile(r"милиц|участков|следовател|кабинет милиц", re.I)),
    ("court", re.compile(r"суд\b|заседани|приговор", re.I)),
    ("home", re.compile(r"квартир|подъезд|кухн|лифт|дверь\s*357", re.I)),
    ("street", re.compile(r"пустыр|улиц|окраин|новокузнецк", re.I)),
)


def _phase_action_text(ph: dict[str, Any]) -> str:
    return " ".join(
        str(ph.get(k) or "") for k in ("action", "действие", "продолжает")
    )


def _place_buckets_in_text(text: str) -> set[str]:
    found: set[str] = set()
    for name, pat in _PLACE_BUCKETS:
        if pat.search(text or ""):
            found.add(name)
    return found


def _is_compound_action(action: str) -> bool:
    """Несколько жестов в одной фазе: «X, Y и Z» / два « и »."""
    t = re.sub(r"\s+", " ", (action or "").strip())
    if not t:
        return False
    if t.count(" и ") >= 2:
        return True
    # «встречаются у лифта, забирают письма и проходят»
    if "," in t and " и " in t:
        return True
    return False


# «Стоит у двери» / «замирает» / «дышит» — дыра в blocking (V7/V9).
_PASSIVE_ACTION_RE = re.compile(
    r"^(?:[^\s,]+\s+){0,3}"
    r"(?:стоит|сидит|смотрит|глядит|наблюдает|ждёт|ждет|замирает|дышит|"
    r"удерживает|молчит)\b",
    re.IGNORECASE,
)

# Дверной конвейер (V11): засов/звонок/глазок как единственный сюжет.
_DOOR_PROP_RE = re.compile(
    r"(?:двер(?:ь|и|ью|ной|ного|ную|ные|ным)|звонк|засов|глазок|цепочк|"
    r"щель\s+под\s+двер|дверн\w+\s+проём|дверн\w+\s+проем|порог\s+\d)",
    re.IGNORECASE,
)
_DOOR_SLAM_RE = re.compile(
    r"(?:захлопывает\s+двер|задвигает\s+(?:засов|щеколд)|"
    r"набрасывает\s+дверную\s+цепоч|прищемляет\s+лист|"
    r"проталкивает\w*\s+(?:жалоб|заявл|лист).*двер|"
    r"суёт\w*\s+.*в\s+щель|вдавливает\w*\s+.*в\s+щель)",
    re.IGNORECASE,
)

# Соевое передвижение без удара (V10): «проходит мимо / лестница / лифт».
_SOY_LOCOMOTION_RE = re.compile(
    r"(?:"
    r"проходит\s+(?:мимо|через|вдоль)|"
    r"\bбродит\b|\bходит\s+по\b|"
    r"(?:идёт|идет)\s+(?:к|по|мимо|вдоль)\b|"
    r"подходит\s+к\b|"
    r"поднимается\s+по\s+лестниц|"
    r"спускается\s+(?:по\s+лестниц|со\s+ступен)|"
    r"выходит\s+из\s+(?:лифта|автобуса)\b|"
    r"входит\s+в\s+(?:прихожую|зал)\b|"
    r"отводит\s+взгляд|"
    r"\bкивает\b|"
    r"указывает\s+рукой|"
    r"поднимает\s+глаза|"
    r"задаёт\s+вопрос|задает\s+вопрос|"
    r"отступает\s+от\b|"
    r"поднимается\s+(?:со\s+скамь|из-?за\s+стола|после\b)|"
    r"\bудаляется\b|\bпоявляется\s+в\b"
    r")",
    re.IGNORECASE,
)

# Метафорический нейрослоп: папки/карточки «как символ» ведомств (V9).
_METAPHOR_SLOP_RE = re.compile(
    r"(папк|карточк|конверт|мед\.?\s*карт|медицинск\w+\s+карт)",
    re.IGNORECASE,
)
_METAPHOR_VERB_RE = re.compile(
    r"(прижима\w+|роняет\w+\s+рук|вытягивает\w+\s+рук|разводит\w+\s+карто|"
    r"закрывает\w+\s+лицо|подстраивает\w+\s+дыхан)",
    re.IGNORECASE,
)


def _is_passive_action(action: str) -> bool:
    """Пассив + соевое передвижение без столкновения (V10)."""
    t = re.sub(r"\s+", " ", (action or "").strip())
    if not t:
        return False
    if _PASSIVE_ACTION_RE.match(t):
        return True
    return bool(_SOY_LOCOMOTION_RE.search(t))


def _is_door_prop_action(action: str) -> bool:
    return bool(_DOOR_PROP_RE.search(action or ""))


def _is_door_slam_action(action: str) -> bool:
    return bool(_DOOR_SLAM_RE.search(action or ""))


def _is_metaphor_slop(action: str) -> bool:
    """Папка/карточка + жест «символа» — не живое событие."""
    t = re.sub(r"\s+", " ", (action or "").strip())
    if not t:
        return False
    return bool(_METAPHOR_SLOP_RE.search(t) and _METAPHOR_VERB_RE.search(t))


def _near_duplicate_actions(a: str, b: str) -> bool:
    """Соседние фазы почти одинаковые (одни корни глагола/предмета)."""
    def toks(s: str) -> set[str]:
        words = re.findall(r"[а-яёa-z0-9]{4,}", (s or "").lower())
        return set(words)

    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return overlap >= 0.55


def _phase_year(ph: dict[str, Any]) -> str | None:
    years = _YEAR_RE.findall(_phase_action_text(ph))
    return years[0] if years else None


def repair_chrono_dyn_year_jumps(scenes: list[Any]) -> list[Any]:
    """Разрезать сцены, где в одной цепи ≥2 календарных года.

    GPT иногда пишет «1987…» и «1991…» в фазах одной сцены; валидатор это
    бракует. Режем цепь на непрерывные группы с одним годом (фазы без года
    наследуют текущий), пропорционально делим ``время_сек``.
    """
    out: list[Any] = []
    for sc in scenes:
        if not isinstance(sc, dict):
            out.append(sc)
            continue
        chain = [ph for ph in _chain_phases(sc) if isinstance(ph, dict)]
        if not chain:
            out.append(sc)
            continue
        years_seq: list[str | None] = []
        cur: str | None = None
        for ph in chain:
            y = _phase_year(ph)
            if y is not None:
                cur = y
            years_seq.append(cur)
        distinct = {y for y in years_seq if y}
        if len(distinct) < 2:
            out.append(sc)
            continue
        try:
            total_sec = float(sc.get("время_сек") or 0.0)
        except (TypeError, ValueError):
            total_sec = 0.0
        groups: list[list[dict[str, Any]]] = []
        buf: list[dict[str, Any]] = []
        buf_year: str | None = years_seq[0]
        for ph, y in zip(chain, years_seq):
            if buf and y != buf_year and y is not None and buf_year is not None:
                groups.append(buf)
                buf = [ph]
                buf_year = y
            else:
                buf.append(ph)
                if y is not None:
                    buf_year = y
        if buf:
            groups.append(buf)
        if len(groups) < 2:
            out.append(sc)
            continue
        n_ph = len(chain)
        for gi, gchain in enumerate(groups):
            part = copy.deepcopy(sc)
            for j, ph in enumerate(gchain, start=1):
                ph["phase_index"] = j
            part["цепь_действия"] = gchain
            if total_sec > 0 and n_ph > 0:
                part["время_сек"] = max(
                    0.1, round(total_sec * len(gchain) / n_ph, 2)
                )
            else:
                # Не оставляем 0.0 — camera-агент бракует нулевой бюджет.
                part["время_сек"] = round(2.0 * max(1, len(gchain)), 2)
            if gi > 0:
                part["связь_с_прошлой"] = part.get("связь_с_прошлой") or (
                    "продолжение после смены года"
                )
            if gi < len(groups) - 1:
                part["крючок_в_следующую"] = part.get("крючок_в_следующую") or (
                    "время сдвигается — новая сцена"
                )
            out.append(part)
    return out


def normalize_chrono_dyn_phase_budget(
    scenes: list[Any], *, min_sec_per_phase: float = 2.0
) -> list[Any]:
    """Слить фазы, если ``время_сек / n < min`` — иначе camera GPT шлёт error.

    VO-бюджет (14 сим = 1 сек) священен: время не накручиваем, а уменьшаем
    число фаз (1 фаза = 1 shot, минимум ~2 сек на шот).
    """
    out: list[Any] = []
    for sc in scenes:
        if not isinstance(sc, dict):
            out.append(sc)
            continue
        sc = copy.deepcopy(sc)
        chain = [ph for ph in _chain_phases(sc) if isinstance(ph, dict)]
        try:
            sec = float(sc.get("время_сек") or 0.0)
        except (TypeError, ValueError):
            sec = 0.0
        if not chain:
            out.append(sc)
            continue
        if sec <= 0.05:
            vo = " ".join(
                str(sc.get(k) or "") for k in ("start_words", "end_words", "цитата")
            )
            vo_len = len(" ".join(vo.split()))
            sec = max(min_sec_per_phase, vo_len / 14.0) if vo_len else min_sec_per_phase
            sc["время_сек"] = round(sec, 2)
        while len(chain) >= 2 and (sec / len(chain)) + 1e-9 < min_sec_per_phase:
            prev, last = chain[-2], chain[-1]
            if str(last.get("beat") or "").strip().lower() == "payoff":
                prev["beat"] = "payoff"
            tr = str(last.get("переход_к_следующей") or "").strip()
            if tr:
                prev["переход_к_следующей"] = tr
            chain = chain[:-1]
        for j, ph in enumerate(chain, start=1):
            ph["phase_index"] = j
        sc["цепь_действия"] = chain
        out.append(sc)
    return out


def validate_chrono_dyn_action_scenes(scenes: list[Any]) -> None:
    """Брак: склейка действий, коллаж мест/лет, нет арки/связей, мало cNN."""
    if not scenes:
        return
    phase_counts: list[int] = []
    object_phases = 0
    cnn_phases = 0
    with_beats = 0
    with_transitions = 0
    missing_links = 0
    missing_hooks = 0
    missing_payoff = 0
    teleport_scenes = 0
    year_jump_scenes = 0
    year_jump_ids: list[str] = []
    compound_phases = 0
    passive_phases = 0
    metaphor_phases = 0
    near_dupe_pairs = 0
    thin_long_vo = 0
    door_phases = 0
    door_slam_phases = 0
    loc_counts: dict[str, int] = {}
    dict_scenes = 0
    for i, sc in enumerate(scenes):
        if not isinstance(sc, dict):
            continue
        dict_scenes += 1
        loc = str(sc.get("location") or sc.get("локация") or "").strip().lower()
        if loc:
            loc_counts[loc] = loc_counts.get(loc, 0) + 1
        chain = _chain_phases(sc)
        phase_counts.append(len(chain))
        if i > 0 and not str(sc.get("связь_с_прошлой") or "").strip():
            missing_links += 1
        if not str(sc.get("крючок_в_следующую") or "").strip():
            missing_hooks += 1
        beats: list[str] = []
        scene_years: set[str] = set()
        scene_places: set[str] = set()
        prev_act = ""
        scene_object_before = object_phases
        for ph in chain:
            if not isinstance(ph, dict):
                continue
            object_phases += 1
            sub = str(ph.get("subject") or "").strip().lower()
            if re.match(r"^c\d+$", sub):
                cnn_phases += 1
            beat = str(ph.get("beat") or "").strip().lower()
            if beat in _BEAT_OK:
                with_beats += 1
                beats.append(beat)
            if str(ph.get("переход_к_следующей") or "").strip():
                with_transitions += 1
            act = str(ph.get("action") or ph.get("действие") or "")
            if _is_compound_action(act):
                compound_phases += 1
            if _is_passive_action(act):
                passive_phases += 1
            if _is_metaphor_slop(act):
                metaphor_phases += 1
            if _is_door_prop_action(act):
                door_phases += 1
            if _is_door_slam_action(act):
                door_slam_phases += 1
            if prev_act and _near_duplicate_actions(prev_act, act):
                near_dupe_pairs += 1
            prev_act = act
            txt = _phase_action_text(ph)
            scene_years.update(_YEAR_RE.findall(txt))
            scene_places |= _place_buckets_in_text(txt)
        scene_had_phases = object_phases > scene_object_before
        if scene_had_phases and beats and "payoff" not in beats:
            missing_payoff += 1
        # ≥2 разных календарных года в одной цепи = телепорт по времени
        if len(scene_years) >= 2:
            year_jump_scenes += 1
            year_jump_ids.append(str(sc.get("id_scene") or f"#{i+1}"))
        # ≥3 разных типа мест в одной сцене = коллаж локаций
        if len(scene_places) >= 3:
            teleport_scenes += 1
        try:
            sec = float(sc.get("время_сек") or 0.0)
        except (TypeError, ValueError):
            sec = 0.0
        # Длинный VO (≥6с) с ≤2 фазами — пресный setup/payoff на большой текст.
        if sec >= 6.0 and len(chain) <= 2:
            thin_long_vo += 1
    if not phase_counts or object_phases == 0:
        # legacy строки в цепи — не chrono_dyn контракт
        return
    n = len(phase_counts)
    avg = sum(phase_counts) / n
    # V8: бюджет кадра 2–4 сек (14 сим VO = 1 сек). Если есть время_сек —
    # проверяем сек/фазу; иначе старый гейт плотности.
    timed_sec_per_phase: list[float] = []
    for sc in scenes:
        if not isinstance(sc, dict):
            continue
        chain = _chain_phases(sc)
        if not chain:
            continue
        try:
            sec = float(sc.get("время_сек") or 0.0)
        except (TypeError, ValueError):
            sec = 0.0
        if sec > 0:
            timed_sec_per_phase.append(sec / len(chain))
    if len(timed_sec_per_phase) >= max(3, n // 2):
        too_fast = sum(1 for sp in timed_sec_per_phase if sp < 1.4)
        too_slow = sum(1 for sp in timed_sec_per_phase if sp > 5.5)
        bad = too_fast + too_slow
        if bad / len(timed_sec_per_phase) > 0.4:
            raise SceneDesignAgentError(
                f"scene_design/action: брак тайминга анимации — "
                f"{too_fast} сцен слишком мелко (<1.4с/кадр), "
                f"{too_slow} слишком тянут (>5.5с/кадр). "
                f"Оптимум 2–4 сек/кадр при 14 сим VO = 1 сек; "
                f"фаз ≈ время_сек/3."
            )
    else:
        two_or_less = sum(1 for c in phase_counts if c <= 2)
        if two_or_less / n > 0.35 and n >= 5:
            raise SceneDesignAgentError(
                f"scene_design/action: брак плотности — {two_or_less}/{n} сцен с <=2 "
                f"фазами (avg={avg:.1f}). Нужно больше фаз на длинный VO "
                f"(1 кадр ≈ 2–4 сек, 14 сим = 1 сек)."
            )
        if avg < 3.2 and n >= 5:
            raise SceneDesignAgentError(
                f"scene_design/action: avg фаз {avg:.1f} < 3.2 — действия слиты; "
                f"разбей под тайминг VO (2–4 сек на кадр)."
            )
    # 0.25: после split-чанков модель часто даёт ~26–30% cNN; жёсткие 0.35
    # крутили soft-retry часами. Промпт V6 требует ≥50% — это цель, не брак.
    if object_phases >= 10 and cnn_phases / object_phases < 0.25:
        raise SceneDesignAgentError(
            f"scene_design/action: персонажи cNN только в {cnn_phases}/"
            f"{object_phases} фазах — задействуй героев текста, не crowd/prop."
        )
    # Арочные поля — обязательны, когда модель уже пишет объекты-фазы.
    if object_phases >= 8 and with_beats / object_phases < 0.7:
        raise SceneDesignAgentError(
            "scene_design/action: у фаз нет beat setup|develop|turn|payoff — "
            "сцена должна иметь начало/середину/конец."
        )
    if object_phases >= 8 and with_transitions < max(1, object_phases - dict_scenes):
        raise SceneDesignAgentError(
            "scene_design/action: нет переход_к_следующей между фазами — "
            "нужна монтажная последовательность, не набор кадров."
        )
    if dict_scenes >= 3 and missing_links / max(1, dict_scenes - 1) > 0.4:
        raise SceneDesignAgentError(
            "scene_design/action: сцены не связаны (нет связь_с_прошлой) — "
            "нужна сквозная последовательность сюжета."
        )
    if dict_scenes >= 3 and missing_hooks / dict_scenes > 0.4:
        raise SceneDesignAgentError(
            "scene_design/action: нет крючок_в_следующую — сцены не ведут сюжет дальше."
        )
    if missing_payoff > max(1, dict_scenes // 3):
        raise SceneDesignAgentError(
            f"scene_design/action: {missing_payoff} сцен без payoff — "
            f"у каждой сцены должен быть видимый итог бита."
        )
    if year_jump_scenes > 0:
        ids = ", ".join(year_jump_ids[:8])
        raise SceneDesignAgentError(
            f"scene_design/action: {year_jump_scenes} сцен прыгают по годам "
            f"внутри одной цепи ({ids}) — смена года = новая сцена, не фаза. "
            f"Нужна непрерывная цепочка в одном месте (вход→жест→стол→итог)."
        )
    if teleport_scenes > 0:
        raise SceneDesignAgentError(
            f"scene_design/action: {teleport_scenes} сцен — коллаж локаций "
            f"(больница/милиция/дом в одной цепи). Одна сцена = одно пространство "
            f"и непрерывный blocking, не набор кадров из разных мест."
        )
    if object_phases >= 8 and compound_phases / object_phases >= 0.2:
        raise SceneDesignAgentError(
            f"scene_design/action: {compound_phases}/{object_phases} фаз склеивают "
            f"несколько действий («…, … и …»). 1 кадр = 1 действие — разбей фазы."
        )
    # V10: соя (ходит/проходит мимо/лестница) = пассив.
    # 0.08 резал валидные chrono_dyn срезы (3/32≈9% при лифте/взгляде в VO).
    # 0.15 — ещё режет «проходит мимо»-простыни, но допускает 2–4 B-roll фазы.
    if object_phases >= 10 and passive_phases / object_phases >= 0.15:
        raise SceneDesignAgentError(
            f"scene_design/action: {passive_phases}/{object_phases} фаз — соя/"
            f"пассив («проходит мимо/лестница/лифт/отводит взгляд/смотрит»). "
            f"Каждый кадр — столкновение людей: хват, отказ, улика, эмоция. "
            f"Не начинай сцену с дороги к месту."
        )
    # V11: дверной конвейер / одна локация — только на полном (или крупном)
    # срезе. На чанках из 8–12 сцен один loc03 на 4 сцены — нормальный кластер.
    if object_phases >= 40 and door_phases / object_phases > 0.18:
        raise SceneDesignAgentError(
            f"scene_design/action: {door_phases}/{object_phases} фаз про "
            f"дверь/звонок/засов — дверной конвейер. Чередуй больницу, суд, "
            f"милицию, улицу, пустырь, комнату; ≤15% фаз про дверь."
        )
    if object_phases >= 40 and door_slam_phases / object_phases > 0.12:
        raise SceneDesignAgentError(
            f"scene_design/action: {door_slam_phases}/{object_phases} фаз — "
            f"повтор «захлопывает дверь/засов». Придумай разные конфликты и "
            f"эмоции людей, не один и тот же хлопок двери."
        )
    if dict_scenes >= 28 and loc_counts:
        top_loc, top_n = max(loc_counts.items(), key=lambda kv: kv[1])
        if top_n / dict_scenes > 0.32 and len(loc_counts) >= 3:
            raise SceneDesignAgentError(
                f"scene_design/action: локация {top_loc} в {top_n}/{dict_scenes} "
                f"сцен — слишком однообразно. Разнеси события по разным locNN "
                f"из world; абстрактный VO → выдумай яркую сцену в другом месте."
            )
    if object_phases >= 8 and metaphor_phases / object_phases >= 0.12:
        raise SceneDesignAgentError(
            f"scene_design/action: {metaphor_phases}/{object_phases} фаз — "
            f"метафорический нейрослоп (папки/карточки «как символ»). "
            f"Покажи живые столкновения: осмотр, ссора, допрос — не аллегорию."
        )
    if object_phases >= 8 and near_dupe_pairs >= max(2, object_phases // 8):
        raise SceneDesignAgentError(
            f"scene_design/action: {near_dupe_pairs} пар соседних фаз почти "
            f"одинаковы. Каждый кадр — новый шаг, не пересказ предыдущего."
        )
    if thin_long_vo >= max(2, dict_scenes // 5):
        raise SceneDesignAgentError(
            f"scene_design/action: {thin_long_vo} сцен с VO≥6с и ≤2 фазами — "
            f"мало кадров на длинный текст. Нужно ≥3 разных действия "
            f"(~2–4 сек на кадр при 14 сим/сек)."
        )


_CAMERA_MOVE_OK = frozenset(
    {
        "static",
        "push-in",
        "pull-out",
        "follow",
        "pan",
        "tilt",
        "handheld",
        "whip-pan",
    }
)


def validate_chrono_dyn_camera_shots(shots: list[Any]) -> None:
    """V11: static не должен забивать почти весь shot_plan."""
    if not shots:
        return
    moves: list[str] = []
    for sh in shots:
        if not isinstance(sh, dict):
            continue
        m = str(sh.get("движение") or sh.get("move") or "static").strip().lower()
        # «push-in / handheld» → берём первый токен
        m = re.split(r"[\s,/|]+", m)[0] if m else "static"
        if m not in _CAMERA_MOVE_OK:
            m = "static"
        moves.append(m)
    n = len(moves)
    if n < 16:
        return
    static_n = sum(1 for m in moves if m == "static")
    if static_n / n > 0.55:
        raise SceneDesignAgentError(
            f"scene_design/camera: {static_n}/{n} шотов static — камера мёртвая. "
            f"Нужно static≤45%: push-in/pull-out/follow/pan/tilt/handheld на "
            f"turn/payoff и эмоциях лиц."
        )


def validate_skeleton_one_vo_per_scene(
    scenes: list[Any], expected_frame_numbers: list[int]
) -> None:
    """1 VO-ячейка = 1 сцена + полное покрытие без дыр. См. ``skeleton.validate_skeleton_coverage``."""
    from app.services.scene_design.skeleton import validate_skeleton_coverage

    validate_skeleton_coverage(scenes, expected_frame_numbers)


def parse_agent_slice(
    agent: str,
    text: str,
    *,
    validate: bool = True,
    expected_frame_numbers: list[int] | None = None,
) -> dict[str, Any]:
    """Распарсить и провалидировать JSON-срез категорийного агента.

    ``validate=False`` — для частичных чанков после 524 (полная проверка на merge).
    """
    list_key = LIST_KEY[agent]
    markers: tuple[str, ...] = (list_key, "error")
    if agent == SKELETON:
        # V3: cells; legacy: scenes / «сцены».
        markers = ("cells", list_key, "сцены", "error")
    data = extract_json_object(text, marker_keys=markers)
    if data is None:
        raise SceneDesignAgentError(
            f"scene_design/{agent}: в ответе нет JSON (len={len(text or '')})"
        )
    if agent == SKELETON:
        if not isinstance(data.get(list_key), list):
            alt = data.get("сцены")
            if isinstance(alt, list):
                data[list_key] = alt
        # Нормализация cells→scenes без циклического import на уровне модуля.
        from app.services.scene_design.skeleton import normalize_skeleton_draft

        normalize_skeleton_draft(data)
    err = str(data.get("error") or "").strip()
    if err:
        raise SceneDesignAgentError(f"scene_design/{agent}: агент вернул error: {err}")
    items = data.get(list_key)
    if agent == SKELETON and (not isinstance(items, list) or not items):
        # После normalize scenes обязателен; cells тоже годится как сигнал успеха.
        cells = data.get("cells")
        if isinstance(cells, list) and cells:
            items = data.get(list_key) or []
    if not isinstance(items, list) or not items:
        keys = sorted(str(k) for k in data.keys())
        raise SceneDesignAgentError(
            f"scene_design/{agent}: пустой «{list_key}» — срез не принят "
            f"(keys={keys})"
        )
    if agent == "action":
        items = repair_chrono_dyn_year_jumps(items)
        items = normalize_chrono_dyn_phase_budget(items)
        data[list_key] = items
        for i, sc in enumerate(items, start=1):
            if isinstance(sc, dict):
                sc["id_scene"] = f"scene_{i:02d}"
    if validate and agent == "action":
        validate_chrono_dyn_action_scenes(items)
    if validate and agent == "camera":
        validate_chrono_dyn_camera_shots(items)
    if validate and agent == SKELETON and expected_frame_numbers is not None:
        validate_skeleton_one_vo_per_scene(items, expected_frame_numbers)
    return data


def parse_assembler_payload(text: str) -> dict[str, Any]:
    """Финальный JSON сборщика: {characters, scenes, ops, report}."""
    data = extract_json_object(text, marker_keys=("scenes", "ops", "characters", "error"))
    if data is None:
        raise SceneDesignAgentError(
            f"scene_design/{ASSEMBLER}: в ответе нет JSON (len={len(text or '')})"
        )
    err = str(data.get("error") or "").strip()
    if err:
        raise SceneDesignAgentError(f"scene_design/{ASSEMBLER}: error: {err}")
    for key in ("characters", "scenes", "ops"):
        if not isinstance(data.get(key), list):
            raise SceneDesignAgentError(
                f"scene_design/{ASSEMBLER}: ключ «{key}» — не список"
            )
    if not data["scenes"]:
        raise SceneDesignAgentError(f"scene_design/{ASSEMBLER}: пустой scenes[]")
    if not data["ops"]:
        raise SceneDesignAgentError(f"scene_design/{ASSEMBLER}: пустой ops[]")
    return data
