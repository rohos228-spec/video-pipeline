"""Шаблоны сцена → кадры (T0–T10, X1/X2) для ноды script_frames_qc / shots.

SoT — ``templates/shot_templates/shot_templates.json`` (v2: вопросы выбора,
эталоны, именованные варианты сжатия, цепи, parent-сводка — по таблице
«Сцены кадры база.xlsx»).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.project_root import find_project_root

_SCENE_SPLIT = re.compile(r"\n(?=\d+\.\s)")
_SCENE_HEAD = re.compile(r"^(\d+)\.\s*(.+)$")


def _has(pat: str, text: str) -> bool:
    return bool(re.search(pat, text, flags=re.IGNORECASE))


_REL = Path("templates") / "shot_templates" / "shot_templates.json"


def shot_templates_path() -> Path:
    return find_project_root() / _REL


@lru_cache(maxsize=1)
def load_shot_templates() -> dict[str, Any]:
    path = shot_templates_path()
    if not path.is_file():
        return {
            "version": 0,
            "select": [],
            "templates": [],
            "shots": [],
            "rules_ai": [],
            "chains": [],
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def clear_shot_templates_cache() -> None:
    load_shot_templates.cache_clear()


def parse_scene_chain(action: str) -> list[dict[str, Any]]:
    """Сцены из главное_действие: N. место — действие + (закадр)."""
    text = (action or "").strip()
    if not text:
        return []
    scenes: list[dict[str, Any]] = []
    for raw in _SCENE_SPLIT.split(text):
        raw = raw.strip()
        if not raw:
            continue
        lines = raw.split("\n", 1)
        head = lines[0].strip()
        vo = lines[1].strip() if len(lines) > 1 else ""
        m = _SCENE_HEAD.match(head)
        if not m:
            continue
        rest = m.group(2).strip()
        if " — " in rest:
            place, act = rest.split(" — ", 1)
        elif " - " in rest:
            place, act = rest.split(" - ", 1)
        else:
            place, act = "", rest
        scenes.append(
            {
                "n": int(m.group(1)),
                "place": place.strip(),
                "action": act.strip(),
                "vo": vo,
                "blob": f"{place} {act} {vo}",
            }
        )
    return scenes


# Паттерны дерева «Выбор». Префиксные (re.search, без якорей): «счита»
# ловит «считает/считал», «говор(и|ю)» — говорит/говорят/говорил.
_RE_T0_TITR = r"титр|имя появляется"
_RE_T1 = (
    r"говор(и|ю)|спор(и|ят)|спрашива|отвеча|диалог|бесед|допрашива|допрос|"
    r"расспрашива|объясня|обща|здорова|переговар|шепч|совеща|рассказыва|рассказал"
)
_RE_T2_VERB = (
    r"нашёл|нашел|нашла|взял|взяла|прочитал|прочла|достал|доста(ёт|ет|ют)|"
    r"открыл|открыла|раскрыл|поднял|вытащил|изуча|изучил"
)
_RE_T2_ITEM = (
    r"папк|документ|книг|схем|письм|дневник|фото|дело|архив|улик|карт|"
    r"журнал|протокол|запис"
)
_RE_T8 = (
    r"родил|вырос|служб|арми|годы|годами|спустя|а потом|друг(ая|ой) жизн|"
    r"эпох|в\s(18|19|20)\d\d|детств|юност|молодост|карьер"
)
_RE_T4 = (
    r"смотр|наблюд|взгляд|видит|увидел|замеча|заметил|высматрив|следит|"
    r"разглядыв"
)
_RE_T5 = (
    r"пиш[еу]|писа|моет|мы(ет|л)|режет|резал|счита|листа|отпечат|схем|отмеча|"
    r"оформл|расклад|упаков|доказател|заполня|заполнил|подпис|печата|собира|"
    r"разбира|чистит|чистил|стира|рисует|рисовал|клеит|шь(ёт|ет)|готовит|"
    r"готовил|перебира|сортиру|фотографиру|мастерит|ремонтир"
)
_RE_T6_VERB = (
    r"идёт|идет|шёл|шел|шла|едет|поехал|шагает|добира|вышел из|вышла из|"
    r"у(шёл|шел|шла) из|приехал|подходит к|подош(ёл|ел) к|подошла к|направля|"
    r"возвраща|бежит|бежал|мчит|мчал|летит|летел|плыв|переезж|дорог|путь|коридор"
)
_RE_T6_DIR = r"\s(из|в|во|к|ко|от|до|между|через|вдоль|мимо)\s"
_RE_T7 = (
    r"услыша|понял|поняла|получи|узнал|узнала|звонок|звонит|приговор|удар|"
    r"шок|оглуш|осозна|дошло|поразил"
)
_RE_T9 = (
    r"сидит|сидел|ждёт|ждет|ждал|ожида|одиночеств|быт|пь(ёт|ет) чай|ужина|"
    r"завтрака|ложится|просыпа|молчит|у окна"
)
_RE_T10 = (
    r"уход|покид|уехал|отъехал|уезжа|пустое место|пустой стол|пустая комнат|"
    r"закрывает|закрыл|конча|финал|ушёл|ушел|ушла"
)


def select_template_when(scene: dict[str, Any], prev_place: str = "") -> str:
    """Первое «да» по листу «Выбор» (Excel SoT).

    Порядок проверок = дереву вопросов, но T3 («ТОЛЬКО смена места»)
    срабатывает последним из содержательных: его вопрос содержит «только»,
    т.е. все остальные ответы — «нет». Раньше ``if place → T3`` шёл до
    проверок T4/T5/T7/T9/T10, и любая сцена с местом («пишет заявление»,
    «смотрит на экран») становилась T3 — главный баг выбора.

    Совпадение места с предыдущей сценой НЕ меняет шаблон — оно меняет
    сжатие (см. :func:`compression_hint_for`). X1/X2 здесь не выбираются:
    это стыки ячеек, а не сцен внутри ячейки.
    """
    blob = str(scene.get("blob") or "")
    place = str(scene.get("place") or "").strip().casefold()
    prev = (prev_place or "").strip().casefold()
    same = bool(place and prev and place == prev)
    if _has(_RE_T0_TITR, blob) and not same:
        return "T0"
    # 1. Двое говорят / слушают друг друга?
    if _has(_RE_T1, blob):
        return "T1"
    # 2. Пришёл в новое место и нашёл / взял / прочитал?
    if _has(_RE_T2_VERB, blob) and ((not same) or _has(_RE_T2_ITEM, blob)):
        return "T2"
    # 8. Годы / «а потом» / другая жизнь? Раньше T6: «поехал служить
    # в армию» — это мир/жизнь (T8), не разовый путь.
    if _has(_RE_T8, blob):
        return "T8"
    # 4. Смысл в том, на что смотрит?
    if _has(_RE_T4, blob):
        return "T4"
    # 5. Руки делают работу?
    if _has(_RE_T5, blob):
        return "T5"
    # 6. Идёт / едет из A в B?
    if _has(_RE_T6_VERB, blob) and _has(_RE_T6_DIR, blob):
        return "T6"
    # 7. Короткий удар: услышал, понял, получил?
    if _has(_RE_T7, blob):
        return "T7"
    # 9. Один в комнате, быт или ожидание?
    if _has(_RE_T9, blob):
        return "T9"
    # 10. Уходит / сцена кончилась?
    if _has(_RE_T10, blob):
        return "T10"
    # 3. ТОЛЬКО смена места, без находки и без работы рук.
    if place:
        return "T3"
    return "T0"


_SAME_PLACE_HINT = {
    "T1": "T1-c2: место уже было → только K2+K3, без нового ОБЩЕГО",
    "T2": "T2-c3: место знакомо → без K1, старт с K2",
    "T3": "T3-c0: место уже мелькало → T0 средний, без нового ОБЩЕГО",
    "T4": "место то же → K1 parent = master места, T3-K1 не вставлять",
    "T5": "место знакомо → без K0",
    "T6": "место то же → только K3 (прибытие), без нового ОБЩЕГО",
    "T7": "место то же → parent = master, без нового ОБЩЕГО",
    "T9": "T9-c1: то же место, одно действие → T0, без нового ОБЩЕГО",
    "T10": "место то же → K1 parent = master",
}


def compression_hint_for(tid: str, *, same_place: bool) -> str:
    """То же место, что у прошлой сцены → вариант сжатия «место уже было»."""
    if not same_place:
        return ""
    base = normalize_template_id(tid)
    return _SAME_PLACE_HINT.get(
        base, "то же место → без нового ОБЩЕГО, parent = master локации"
    )


def normalize_template_id(tid: str) -> str:
    raw = (tid or "").strip().upper()
    match = re.match(r"^([TX]\d+)", raw)
    return match.group(1) if match else raw


_DYNAMIC_LEN = {"T8"}  # лестница = числу мест в закадре, фикс. лимита нет


def template_max_shots(tid: str) -> int:
    """Сколько строк shots у шаблона. 0 если id нет в каталоге или лимит
    динамический (T8: сколько мест назвал закадр — столько кадров)."""
    base = normalize_template_id(tid)
    if not base or base in _DYNAMIC_LEN:
        return 0
    rows = [
        row
        for row in (load_shot_templates().get("shots") or [])
        if normalize_template_id(str(row.get("template_id") or "")) == base
    ]
    return len(rows)


def _select_keys() -> dict[str, str]:
    """template_id → короткий ключ вопроса (select.if)."""
    return {
        str(r.get("then_template") or ""): str(r.get("if") or "")
        for r in (load_shot_templates().get("select") or [])
    }


def plan_templates_for_action(action: str) -> list[dict[str, Any]]:
    """Предвыбор T/X по дереву «Выбор» + подсказка сжатия на том же месте.

    Повтор одного T* на соседних сценах разрешён (Excel «Цепи»:
    T8>T8>T8 для биографии) — шаблон отвечает на вопрос дерева, а не
    чередуется ради разнообразия.
    """
    plan: list[dict[str, Any]] = []
    prev_place = ""
    keys = _select_keys()
    for scene in parse_scene_chain(action):
        place = str(scene.get("place") or "").strip()
        same = bool(
            place and prev_place and place.casefold() == prev_place.casefold()
        )
        tid = select_template_when(scene, prev_place)
        plan.append(
            {
                "n": int(scene["n"]),
                "place": place,
                "action": str(scene.get("action") or ""),
                "template": tid,
                "reason": keys.get(tid, ""),
                "same_place": same,
                "compression": compression_hint_for(tid, same_place=same),
            }
        )
        if place:
            prev_place = place
    return plan


def assign_templates_for_action(action: str) -> dict[int, str]:
    """Сцена → template_id (краткий вид plan_templates_for_action)."""
    return {row["n"]: row["template"] for row in plan_templates_for_action(action)}


def coverage_template_reason(shots: list[dict[str, Any]], uid: str = "") -> str | None:
    """Один T на сцену, без удлинения лестницы, T8 — один кадр на место.

    Повтор одного T* на соседних сценах НЕ брак (Excel «Цепи»: T8>T8>T8).
    """
    prefix = f"uuid {uid[:8]}: " if uid else ""
    blocks: list[tuple[str, str, int, list[str]]] = []
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        sc = shot.get("сцена")
        place = str(shot.get("место") or shot.get("place") or "").strip().casefold()
        key = str(sc) if sc not in (None, "") else f"p:{place}"
        tid = normalize_template_id(str(shot.get("шаблон") or shot.get("template") or ""))
        if not blocks or blocks[-1][0] != key:
            blocks.append((key, tid, 1, [place]))
            continue
        prev_key, prev_tid, n, places = blocks[-1]
        if tid and prev_tid and tid != prev_tid:
            return f"{prefix}сцена {key}: два шаблона {prev_tid} и {tid}"
        places.append(place)
        blocks[-1] = (prev_key, prev_tid or tid, n + 1, places)
    for key, tid, n, places in blocks:
        if not tid:
            continue
        if tid == "T8":
            seen = [p for p in places if p]
            if len(seen) != len(set(seen)):
                return (
                    f"{prefix}сцена {key}: T8 покрывает одно место дважды — "
                    "один ОБЩИЙ на мир, не плоди покрытие"
                )
            continue
        limit = template_max_shots(tid)
        if limit and n > limit:
            return (
                f"{prefix}сцена {key}: {tid} удлинили до {n} кадров "
                f"(в таблице {limit})"
            )
    return None


def format_when_assignments(frames: list[Any]) -> str:
    """Предвыбор T/X по дереву + сжатие. GPT может поправить с select_fix."""
    lines = [
        "# ПРЕДВЫБОР T/X ПО ДЕРЕВУ (по каждой сцене главное_действие)",
        "Строка: сцена | место | действие → T* (ключ вопроса) · сжатие.",
        "Это предвыбор по листу «Выбор». Если он явно противоречит сцене "
        "(стоит T3, а в сцене двое говорят) — возьми правильный шаблон по "
        "дереву и пометь первый кадр сцены полем «select_fix»: "
        "«T3→T1: двое говорят».",
        "То же место, что у прошлой сцены → сжатие «место уже было»; "
        "шаблон НЕ менять ради чередования (T8>T8>T8 — норма).",
    ]
    any_row = False
    for fr in frames:
        attrs = getattr(fr, "attrs", None)
        if attrs is None and isinstance(fr, dict):
            attrs = fr.get("attrs") or fr
        if not isinstance(attrs, dict):
            continue
        action = str(
            attrs.get("главное_действие") or attrs.get("main_action") or ""
        ).strip()
        if not action:
            continue
        for row in plan_templates_for_action(action):
            line = (
                f"сцена {row['n']} | {row['place'] or '—'} | "
                f"{row['action'][:80]} → {row['template']}"
            )
            if row["reason"]:
                line += f" ({row['reason']})"
            if row["compression"]:
                line += f" · сжатие: {row['compression']}"
            lines.append(line)
            any_row = True
    if not any_row:
        return ""
    return "\n".join(lines).strip() + "\n"


def format_shot_templates_catalog(*, max_chars: int = 24000) -> str:
    """Компактный каталог v2 для accompanying ноды shots."""
    data = load_shot_templates()
    lines: list[str] = [
        "# ШАБЛОНЫ СЦЕНА→КАДРЫ (SoT v2)",
        "T = тип сцены · X = стык двух ячеек закадра · K = кадр внутри типа.",
        "select — на КАЖДУЮ сцену главное_действие, не на ячейку целиком.",
        "",
        "## Общие правила",
    ]
    for rule in data.get("rules_general") or []:
        lines.append(f"- {rule}")
    lines.append("")
    lines.append("## ВЫБОР шаблона (первое «да» → шаблон)")
    for row in data.get("select") or []:
        step = row.get("step")
        prefix = "—" if str(step) == "99" else f"{step}."
        lines.append(f"{prefix} {row.get('question') or row.get('if')} → {row.get('then_template')}")
    for note in data.get("select_notes") or []:
        lines.append(f"! {note}")
    lines.append("")
    lines.append("## КАТАЛОГ T/X (когда · лестница · сжатие · эталон)")
    for row in data.get("templates") or []:
        head = (
            f"{row.get('id')} {row.get('name')} | axis={row.get('axis')} | "
            f"parent={row.get('parent_default')}"
        )
        lines.append(head)
        lines.append(f"  когда: {row.get('when')}")
        ladder = str(row.get("shots_full") or "")
        if row.get("drop_order"):
            ladder += f" · drop={row.get('drop_order')}"
        lines.append(f"  лестница: {ladder}")
        comp = [
            f"{c.get('variant')} ({c.get('when')}) → {c.get('keep')}"
            for c in (row.get("compression") or [])
            if str(c.get("variant") or "") != "полная"
        ]
        if comp:
            lines.append("  сжатие: " + " · ".join(comp))
        if row.get("note"):
            lines.append(f"  parent/ось: {row.get('note')}")
        if row.get("example"):
            lines.append(f"  эталон: {row.get('example')}")
    lines.append("")
    lines.append("## SHOTS (строки кадров: подставь слоты, структуру не выдумывай)")
    lines.append(
        "template_id|shot_id|n|plan|angle|place|who|action|parent|role|required"
    )
    for row in data.get("shots") or []:
        lines.append(
            f"{row.get('template_id')}|{row.get('shot_id')}|{row.get('n')}|"
            f"{row.get('plan')}|{row.get('angle')}|{row.get('place')}|"
            f"{row.get('who')}|{row.get('action')}|{row.get('parent')}|"
            f"{row.get('role')}|{row.get('required')}"
        )
    lines.append("")
    lines.append("## rules_ai")
    for row in data.get("rules_ai") or []:
        lines.append(
            f"[{row.get('тема')}={row.get('значение')}] {row.get('команда_для_ии')}"
        )
    chains = data.get("chains") or []
    if chains:
        lines.append("")
        lines.append("## ЦЕПИ (как склеивать шаблоны в ролике)")
        for row in chains:
            what = f" — {row.get('what')}" if row.get("what") else ""
            lines.append(f"{row.get('chain')}{what}")
    parent_rules = data.get("parent_rules") or []
    if parent_rules:
        lines.append("")
        lines.append("## PARENT (сводка)")
        for row in parent_rules:
            lines.append(f"- {row.get('situation')} → {row.get('parent')}")
    examples = data.get("example_chains") or []
    if examples:
        lines.append("")
        lines.append("## ЭТАЛОННАЯ ЦЕПЬ (пример разбора)")
        for ex in examples:
            lines.append(f"закадр: «{ex.get('vo')}»")
            for sc in ex.get("scenes") or []:
                lines.append(f"  {sc}")
            if ex.get("note"):
                lines.append(f"  ! {ex.get('note')}")
    text = "\n".join(lines).strip() + "\n"
    if len(text) > max_chars:
        return text[: max_chars - 20].rstrip() + "\n…(обрезано)\n"
    return text


def neighbor_place_hints(frames: list[Any]) -> str:
    """Для X1/X2: место предыдущей ячейки в батче (по number)."""
    rows: list[tuple[int, str, str, str]] = []
    for fr in frames:
        uid = str(getattr(fr, "uuid", None) or "").strip()
        if not uid and isinstance(fr, dict):
            uid = str(fr.get("uuid") or "").strip()
        if not uid:
            continue
        num = getattr(fr, "number", None)
        if num is None and isinstance(fr, dict):
            num = fr.get("number")
        try:
            n = int(num)
        except (TypeError, ValueError):
            continue
        attrs = getattr(fr, "attrs", None)
        if attrs is None and isinstance(fr, dict):
            attrs = fr.get("attrs") or fr
        attrs = attrs if isinstance(attrs, dict) else {}
        action = str(
            attrs.get("главное_действие")
            or attrs.get("main_action")
            or ""
        ).strip()
        place = ""
        for line in action.splitlines():
            line = line.strip()
            if not line or line.startswith("("):
                continue
            # «1. двор — действие» / «двор — действие»
            body = line.split(".", 1)[-1].strip() if line[:1].isdigit() else line
            if "—" in body:
                place = body.split("—", 1)[0].strip()
            elif "-" in body:
                place = body.split("-", 1)[0].strip()
            if place:
                break
        rows.append((n, uid, place, action[:120]))
    rows.sort(key=lambda r: r[0])
    if not rows:
        return ""
    lines = [
        "# СОСЕДИ ДЛЯ X1/X2",
        "X1 = следующая ячейка, то же место → parent = master прошлой локации, без нового ОБЩЕГО.",
        "X2 = другая география → parent_id null.",
    ]
    for i, (n, uid, place, _action) in enumerate(rows):
        prev_place = rows[i - 1][2] if i else ""
        prev_uid = rows[i - 1][1][:8] if i else ""
        lines.append(
            f"number={n} uuid={uid[:8]} place≈{place or '—'} "
            f"prev_place≈{prev_place or '—'} prev_uuid={prev_uid or '—'}"
        )
    return "\n".join(lines).strip() + "\n"
