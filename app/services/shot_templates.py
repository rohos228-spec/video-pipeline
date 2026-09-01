"""Шаблоны сцена → кадры (T0–T10, X1/X2) для ноды script_frames_qc / shots.

SoT — ``templates/shot_templates/shot_templates.json`` (v2: вопросы выбора,
эталоны, именованные варианты сжатия, цепи, parent-сводка — по таблице
«Сцены кадры база.xlsx»).
"""

from __future__ import annotations

import json
import re
import unicodedata
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


def plain_scene_vo(raw: str) -> str:
    """Кусок закадра сцены без обёртки ``(...)``."""
    text = " ".join((raw or "").split())
    if len(text) >= 2 and text[0] == "(" and text[-1] == ")":
        return " ".join(text[1:-1].split())
    return text


# Паттерны дерева «Выбор». Префиксные (re.search, без якорей): «счита»
# ловит «считает/считал», «говор(и|ю)» — говорит/говорят/говорил.
# T1 = только РЕЧЬ (диалог/спор/допрос). Молчаливая передача предмета —
# T2; приказ/отказ/возврат — T7. T5 = только ДЛИТЕЛЬНАЯ работа руками.
_RE_T0_TITR = r"титр|имя появляется"
_RE_T1 = (
    r"говор(и|ю)|спор(и|ят)|спрашива|отвеча|диалог|бесед|допрашива|допрос|"
    r"расспрашива|объясня|обща|здорова|переговар|шепч|совеща|рассказыва|"
    r"рассказал|ссор|приветств|выслушива|выслушал|требу(ет|ют)|доказыва|"
    r"возража|оправдыва|защища(ет|ют|л)ся"
)
_RE_T2_VERB = (
    r"нашёл|нашел|нашла|находит|находят|взял|взяла|берёт|берут|прочитал|"
    r"прочла|читает|читают|читал|достал|доста(ёт|ет|ют)|открыл|открыла|"
    r"открывает|открывают|раскрыл|раскрыва(ет|ют)|поднял|вытащил|изуча|"
    r"изучил|переда(ёт|ет|ют|л|ла)|принос|протягива(ет|ют)|вруча|"
    r"принима(ет|ют|л|ла)"
)
_RE_T2_ITEM = (
    r"папк|документ|книг|схем|письм|дневник|фото|дело|архив|улик|карт|"
    r"журнал|протокол|запис|жалоб|прошен|заявлен|реестр|свод"
)
# Только прыжок жизни / новый мир. Запрещено: «долгие годы» в том же
# кабинете, «служба» как метафора, голое «годы» в парадоксе.
_RE_T8 = (
    r"родил(?:ся|ась|ись)?|вырос|"
    r"(?:служб[ауиеы]|служил)\s+в\s+арми|в\s+армии|арми[июяе]|"
    r"спустя\s+(?:годы|лет)|годами\s+спустя|а\s+потом|"
    r"друг(?:ая|ой)\s+жизн|эпох|"
    r"в\s+(?:18|19|20)\d\d|"
    r"детств|юност|молодост|"
    r"карьер[ауиеы]|"
    r"принадлеж|владел|владения"
)
_RE_T8_HOLDINGS = r"принадлеж|владел|владения|перечисл"
_RE_T4 = (
    r"смотр|наблюд|взгляд|видит|увидел|замеча|заметил|высматрив|следит|"
    r"разглядыв|указыва(ет|ют)|указал|показыва|показал|предъявля|тычет"
)
_RE_T5 = (
    r"пиш[еу]|писа|моет|мы(ет|л)|режет|резал|счита|листа|отпечат|схем|отмеча|"
    r"оформл|расклад|упаков|доказател|заполня|заполнил|подпис|печата|собира|"
    r"разбира|чистит|чистил|стира|рисует|рисовал|клеит|шь(ёт|ет)|готовит|"
    r"готовил|перебира|сортиру|фотографиру|мастерит|ремонтир|записыва|записал|"
    r"убира(ет|ют|л)"
)
_RE_T6_VERB = (
    r"идёт|идет|шёл|шел|шла|едет|поехал|шагает|добира|вышел из|вышла из|"
    r"у(шёл|шел|шла) из|приехал|подходит к|подош(ёл|ел) к|подошла к|направля|"
    r"возвраща|бежит|бежал|мчит|мчал|летит|летел|плыв|переезж|дорог|путь|коридор"
)
_RE_T6_DIR = r"\s(из|в|во|к|ко|от|до|между|через|вдоль|мимо)\s"
_RE_T7 = (
    r"услыша|понял|поняла|получи|узнал|узнала|звонок|звонит|приговор|удар|"
    r"шок|оглуш|осозна|дошло|поразил|приказыва|приказал|отворачива|отказ|"
    r"отклон|вернул|вернули|возврат"
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
    # 4. Смысл в том, на что смотрит?
    if _has(_RE_T4, blob):
        return "T4"
    # 5. Руки делают работу? Реестр владений / «ей принадлежали» = T8.
    if _has(_RE_T5, blob):
        if _has(_RE_T8_HOLDINGS, blob):
            return "T8"
        return "T5"
    # 6. Идёт / едет из A в B? «поехал служить в армию» = T8 (шаг 8).
    if _has(_RE_T6_VERB, blob) and _has(_RE_T6_DIR, blob):
        if _has(_RE_T8, blob):
            return "T8"
        return "T6"
    # 7. Короткий удар: услышал, понял, получил?
    if _has(_RE_T7, blob):
        return "T7"
    # 8. Прыжок жизни / новый мир (не «долгие годы» в том же кабинете).
    if _has(_RE_T8, blob):
        return "T8"
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


def template_required_shots(tid: str) -> int:
    """Сколько кадров required=1 у шаблона — минимум лестницы.

    T8/X2 — динамические (по числу мест/ячеек), минимума нет → 0.
    """
    base = normalize_template_id(tid)
    if not base or base in _DYNAMIC_LEN or base.startswith("X"):
        return 0
    return sum(
        1
        for row in (load_shot_templates().get("shots") or [])
        if normalize_template_id(str(row.get("template_id") or "")) == base
        and row.get("required") == 1
    )


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


_SAME_PLACE_DROP = {
    "T1": frozenset({"T1-K1"}),
    "T2": frozenset({"T2-K1"}),
    "T3": frozenset({"T3-K1"}),
    "T5": frozenset({"T5-K0"}),
    "T6": frozenset({"T6-K1", "T6-K2"}),
}


def catalog_shot_rows(tid: str, *, same_place: bool = False) -> list[dict[str, Any]]:
    """Строки SHOTS шаблона. То же место — без нового ОБЩЕГО."""
    base = normalize_template_id(tid)
    if not base or base.startswith("X"):
        return []
    rows = [
        dict(row)
        for row in (load_shot_templates().get("shots") or [])
        if normalize_template_id(str(row.get("template_id") or "")) == base
    ]
    rows.sort(key=lambda r: int(r.get("n") or 0))
    if same_place:
        drop = _SAME_PLACE_DROP.get(base) or frozenset()
        kept = [r for r in rows if str(r.get("shot_id") or "") not in drop]
        if kept:
            return kept
    return rows


_STUB_ACTION_RE = re.compile(
    r"\s/\s|"
    r"руки\s*\+|"
    r"\{|"
    r"лицо:\s*\(если|"
    r"крупно:\s*обложка|"
    r"понял\s*/\s*испугался|"
    r"тянет\s*/\s*открывает|"
    r"^реакция$|"
    r"^вошёл\s*/\s*стоит$|"
    r"^идёт\s*/\s*едет$|"
    r"^смотрит$|"
    r"^вышел$|"
    r"^пришёл$|"
    r"^лицо после$|"
    r"^сам удар:",
    re.IGNORECASE,
)
SHOT_ACTION_MIN_CHARS = 48


def _visible_len(text: str) -> int:
    return len(
        "".join(c for c in (text or "") if unicodedata.category(c) != "Mn")
    )


def is_stub_shot_action(text: str) -> bool:
    """Каталожный слоган / слишком короткое «действие», не описание кадра."""
    t = " ".join((text or "").split())
    if not t:
        return True
    if _STUB_ACTION_RE.search(t):
        return True
    return _visible_len(t) < SHOT_ACTION_MIN_CHARS


_ROLE_BEAT = {
    "master": "вход: видно всё помещение, кто в кадре, одежда, куда идёт",
    "action": "жест: руки, поза, к кому корпус, один видимый поступок",
    "insert": "деталь предмета: фактура, надпись, пальцы на нём, без лица целиком",
    "reaction": "лицо: какая эмоция после жеста, взгляд, рот, к кому смотрит",
    "start": "выход: шаг от порога, одежда, куда смотрит",
    "travel": "в пути: шаг, что по сторонам дороги, куда направлен корпус",
    "arrive": "прибытие: новое место целиком, кто встречает, куда ставит ногу",
}


def compose_shot_action(
    *,
    plan: str,
    place: str,
    scene_action: str,
    catalog_action: str = "",
    catalog_role: str = "",
) -> str:
    """Действие ЭТОГО кадра: роль из каталога SHOTS, не копия сцены + штамп плана."""
    place_s = " ".join((place or "").split()) or "место сцены"
    act = " ".join((scene_action or "").split()) or "действие сцены"
    filled = (
        _fill_slot_text(catalog_action, place=place_s, action=act)
        if catalog_action
        else ""
    )
    if filled and not is_stub_shot_action(filled):
        return f"{place_s}. {filled}"
    role_key = (catalog_role or "").strip().casefold()
    role_beat = _ROLE_BEAT.get(role_key, "")
    if role_beat:
        return f"{place_s}. {role_beat} во время «{act}»"
    plan_u = (plan or "").upper()
    if "ДЕТАЛЬ" in plan_u:
        beat = (
            f"деталь предмета или рук в {place_s} во время «{act}»: "
            "фактура, надпись, то что меняет смысл, без лица целиком"
        )
    elif "КРУПН" in plan_u:
        beat = (
            f"лицо после «{act}» в {place_s}: "
            "эмоция читается без слов, взгляд, рот, к кому обращён"
        )
    elif "СРЕДН" in plan_u:
        beat = (
            f"жест в {place_s}: «{act}» — руки, поза, "
            "к кому стоит корпус, один видимый поступок"
        )
    else:
        beat = (
            f"вход в {place_s} в начале «{act}»: "
            "всё помещение, кто в кадре, одежда, куда направлен взгляд"
        )
    return f"{place_s}. {beat}"


def _fill_slot_text(text: str, *, place: str, action: str) -> str:
    raw = str(text or "")
    raw = raw.replace("{место}", place).replace("{новое_место}", place)
    raw = raw.replace("{действие}", action).replace("{жест}", action)
    raw = raw.replace("{откуда}", place).replace("{куда}", place).replace("{путь}", place)
    raw = re.sub(r"\{[^}]+\}", "", raw)
    return " ".join(raw.split())


def _ensure_full_shot_action(
    shot: dict[str, Any],
    *,
    place: str,
    scene_action: str,
    catalog_action: str = "",
    catalog_role: str = "",
    force: bool = False,
) -> None:
    """Слоган каталога чиним. Уникальное описание GPT не затираем.

    ``force`` только для заглушек (попробовать ещё раз), не для полного текста.
    """
    raw = str(shot.get("действие") or shot.get("action") or "")
    if not is_stub_shot_action(raw):
        return
    shot["действие"] = compose_shot_action(
        plan=str(shot.get("план") or shot.get("plan") or ""),
        place=str(shot.get("место") or shot.get("place") or place),
        scene_action=scene_action or raw,
        catalog_action=catalog_action,
        catalog_role=catalog_role,
    )
    shot.pop("action", None)


def _norm_vo_words(text: str) -> list[str]:
    return " ".join((text or "").split()).split()


def _template_drop_ids(tid: str) -> list[str]:
    base = normalize_template_id(tid)
    for row in load_shot_templates().get("templates") or []:
        if normalize_template_id(str(row.get("id") or "")) == base:
            raw = str(row.get("drop_order") or "")
            return [p.strip() for p in raw.split(";") if p.strip()]
    return []


def _drop_extra_shots(
    shots: list[dict[str, Any]],
    keep_n: int,
    catalog_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Лишние кадры: сначала drop_order / required=0, потом с конца."""
    if keep_n >= len(shots) or not shots:
        return shots
    if keep_n < 1:
        keep_n = 1
    rows = list(catalog_rows or [])
    tid = normalize_template_id(
        str(shots[0].get("шаблон") or shots[0].get("template") or "")
    )
    order = _template_drop_ids(tid)
    drop_set: set[int] = set()
    need_drop = len(shots) - keep_n
    # С конца: сначала required=0 (не трогаем K1 / master), потом drop_order.
    for i in range(len(shots) - 1, 0, -1):
        if need_drop <= 0:
            break
        row = rows[i] if i < len(rows) else {}
        if row.get("required") == 1:
            continue
        drop_set.add(i)
        need_drop -= 1
    if need_drop > 0 and order:
        for sid in reversed(order):
            if need_drop <= 0:
                break
            for i, sh in enumerate(shots):
                if i in drop_set or i == 0:
                    continue
                row = rows[i] if i < len(rows) else {}
                if str(row.get("shot_id") or "") == sid:
                    drop_set.add(i)
                    need_drop -= 1
                    break
    if need_drop > 0:
        for i in range(len(shots) - 1, 0, -1):
            if need_drop <= 0:
                break
            if i not in drop_set:
                drop_set.add(i)
                need_drop -= 1
    return [sh for i, sh in enumerate(shots) if i not in drop_set]


def _assign_scene_vo(
    shots: list[dict[str, Any]],
    vo: str,
    *,
    catalog_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Каждый кадр — свой кусок закадра. Пустой кадр выкидываем, не оставляем."""
    vo = plain_scene_vo(vo)
    if not shots:
        return []
    if not vo:
        return []
    existing = [" ".join(str(s.get("закадр") or "").split()) for s in shots]
    if all(existing) and _norm_vo_words(" ".join(existing)) == _norm_vo_words(vo):
        return shots
    from app.services.scene_design.camera_expand import split_text_into_parts
    from app.services.vo_shot_expand import (
        kadry_vo_partition,
        kadry_vo_partition_aligned,
    )

    parts = kadry_vo_partition(vo, shots) or kadry_vo_partition_aligned(vo, shots)
    if not parts:
        parts = split_text_into_parts(vo, len(shots))
    nonempty = [" ".join((p or "").split()) for p in parts if str(p or "").strip()]
    if len(nonempty) < len(shots):
        shots = _drop_extra_shots(shots, len(nonempty) or 1, catalog_rows)
        parts = kadry_vo_partition(vo, shots) or kadry_vo_partition_aligned(vo, shots)
        if not parts:
            parts = split_text_into_parts(vo, len(shots))
    kept: list[dict[str, Any]] = []
    for sh, piece in zip(shots, parts):
        piece = " ".join((piece or "").split())
        if not piece:
            continue
        sh["закадр"] = piece
        kept.append(sh)
    if not kept:
        shots[0]["закадр"] = vo
        kept = [shots[0]]
    return kept


def fill_kadry_from_catalog(
    shots: list[Any],
    action: str,
    *,
    cell_number: int = 1,
) -> list[dict[str, Any]]:
    """GPT написал 1 кадр на сцену → полная лестница T/X из таблицы.

    Эталон: полиция T6→T3→T1→T5, дети = шаги одного действия.
    X1/X2 из GPT на сценах внутри ячейки сбрасываются на дерево.
    """
    chain = parse_scene_chain(action)
    plan = plan_templates_for_action(action)
    plan_by_n = {int(row["n"]): row for row in plan}
    incoming = [dict(s) for s in shots if isinstance(s, dict)]
    by_scene: dict[int, list[dict[str, Any]]] = {}
    for sh in incoming:
        try:
            n = int(sh.get("сцена"))
        except (TypeError, ValueError):
            continue
        by_scene.setdefault(n, []).append(sh)
    if not by_scene and incoming and chain:
        for i, sh in enumerate(incoming):
            if i < len(chain):
                by_scene.setdefault(int(chain[i]["n"]), []).append(sh)
    scenes = chain or [
        {"n": 1, "place": "", "action": "", "vo": "", "blob": action}
    ]
    out: list[dict[str, Any]] = []
    for scene in scenes:
        n = int(scene["n"])
        prow = plan_by_n.get(n) or {}
        place = str(scene.get("place") or prow.get("place") or "").strip()
        act = str(scene.get("action") or prow.get("action") or "").strip()
        vo = str(scene.get("vo") or "").strip()
        same = bool(prow.get("same_place"))
        existing = by_scene.get(n) or []
        tid = ""
        if existing:
            tid = normalize_template_id(
                str(existing[0].get("шаблон") or existing[0].get("template") or "")
            )
        if not tid or tid.startswith("X"):
            tid = str(prow.get("template") or select_template_when(scene, ""))
        rows = catalog_shot_rows(tid, same_place=same)
        if not rows:
            if existing:
                scene_acc = []
                for sh in existing:
                    _ensure_full_shot_action(sh, place=place, scene_action=act)
                    scene_acc.append(sh)
                out.extend(_assign_scene_vo(scene_acc, vo))
            continue
        required = sum(1 for r in rows if r.get("required") == 1)
        if existing and len(existing) >= max(len(rows), required, 1):
            scene_acc = []
            for i, sh in enumerate(existing):
                sh.setdefault("шаблон", tid)
                sh.setdefault("сцена", n)
                cat = str(rows[i].get("action") or "") if i < len(rows) else ""
                role = str(rows[i].get("role") or "") if i < len(rows) else ""
                _ensure_full_shot_action(
                    sh,
                    place=place,
                    scene_action=act,
                    catalog_action=cat,
                    catalog_role=role,
                )
                scene_acc.append(sh)
            out.extend(_assign_scene_vo(scene_acc, vo, catalog_rows=rows))
            continue
        seed = existing[0] if existing else {}
        incoming_vo = [str(s.get("закадр") or "").strip() for s in existing]
        master_id = f"{cell_number}-S{n}-K1"
        scene_acc = []
        for i, row in enumerate(rows):
            sid = f"{cell_number}-S{n}-K{i + 1}"
            plan_name = str(row.get("plan") or "").split("/")[0].strip()
            angle = str(row.get("angle") or "").split("/")[0].strip()
            cat = str(row.get("action") or "")
            shot = {
                "id": sid,
                "parent_id": None if i == 0 else master_id,
                "порядок": i + 1,
                "сцена": n,
                "шаблон": tid,
                "план": plan_name,
                "ракурс": angle if angle != "—" else "",
                "место": place,
                "действие": compose_shot_action(
                    plan=plan_name,
                    place=place,
                    scene_action=act,
                    catalog_action=cat,
                    catalog_role=str(row.get("role") or ""),
                ),
                "закадр": incoming_vo[i] if i < len(incoming_vo) else "",
            }
            src = existing[i] if i < len(existing) else None
            if src and src.get("закадр"):
                shot["закадр"] = str(src.get("закадр") or shot.get("закадр") or "")
            if src and not is_stub_shot_action(str(src.get("действие") or "")):
                shot["действие"] = str(src.get("действие") or shot["действие"])
            _ensure_full_shot_action(
                shot,
                place=place,
                scene_action=act,
                catalog_action=cat,
                catalog_role=str(row.get("role") or ""),
            )
            scene_acc.append(shot)
        out.extend(_assign_scene_vo(scene_acc, vo, catalog_rows=rows))
    if not out:
        leftover: list[dict[str, Any]] = []
        for sh in incoming:
            _ensure_full_shot_action(
                sh,
                place=str(sh.get("место") or sh.get("place") or ""),
                scene_action=action,
            )
            leftover.append(sh)
        glue = " ".join(
            str(s.get("закадр") or "").strip()
            for s in leftover
            if str(s.get("закадр") or "").strip()
        )
        out.extend(_assign_scene_vo(leftover, glue))
    return out


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
        required = template_required_shots(tid)
        if required and n < required:
            return (
                f"{prefix}сцена {key}: {tid} сжали до {n} кадров — "
                f"минимум лестницы {required} (required=1 не удаляют)"
            )
    # Кадры-вариации одной позы: одинаковое действие у кадров одной сцены.
    by_scene: dict[str, set[str]] = {}
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        sc = shot.get("сцена")
        key = str(sc) if sc not in (None, "") else "?"
        act = _action_stem(
            str(shot.get("действие") or shot.get("action") or "")
        )
        if not act:
            continue
        seen_acts = by_scene.setdefault(key, set())
        if act in seen_acts:
            return (
                f"{prefix}сцена {key}: два кадра с одинаковым действием "
                f"«{act[:50]}» — вариации позы, а не шаги одного действия"
            )
        seen_acts.add(act)
    return None


_GENERIC_PLAN_RE = re.compile(
    r"(деталь:\s*крупно руки|крупный план лица|средний план по пояс|"
    r"общий план:\s*видно всё помещение).*$",
    re.IGNORECASE,
)


def _action_stem(text: str) -> str:
    """Срезает штамп плана, чтобы «место. сцена. Средний план…» = копия."""
    t = " ".join((text or "").split()).casefold()
    t = _GENERIC_PLAN_RE.sub("", t).strip(" .")
    return t


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
        plan = plan_templates_for_action(action)
        for row in plan:
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
        # Один T* 3+ раза в цепи — почти всегда ошибка чтения дерева.
        counts: dict[str, int] = {}
        for row in plan:
            counts[row["template"]] = counts.get(row["template"], 0) + 1
        for tid, cnt in sorted(counts.items()):
            if cnt >= 3 and tid != "T8":
                lines.append(
                    f"! {tid} выбран {cnt} раз в одной цепи — прогони дерево "
                    "заново: T1 — только речь (диалог); «передал/принёс "
                    "предмет» = T2; «указывает/показывает» = T4; «получил/"
                    "услышал/приказал» = T7; T5 — только длительная работа "
                    "руками."
                )
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
    lines.append("## SHOTS (строки кадров: роль/план, структуру не выдумывай)")
    lines.append(
        "action в таблице — РОЛЬ кадра, не готовый текст. "
        "В JSON поле «действие» = полное описание: помещение, кто в кадре, "
        "одежда, эмоция если нужна, видимый поступок. "
        "Копировать «понял / испугался / решил», «тянет / открывает / берёт», "
        "«руки +», «лицо: (если есть в тексте)» — брак."
    )
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
            line = f"- {row.get('situation')} → {row.get('parent')}"
            if row.get("дочерний"):
                line += f" · дочерний: {row.get('дочерний')}"
            lines.append(line)
    child_rules = data.get("child_rules") or []
    if child_rules:
        lines.append("")
        lines.append("## ДОЧЕРНИЕ КАДРЫ (когда используются)")
        for rule in child_rules:
            lines.append(f"- {rule}")
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
