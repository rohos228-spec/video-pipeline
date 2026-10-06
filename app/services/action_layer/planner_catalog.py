"""Сжатие каталога для промпта планировщика: офлайн-предфильтр и компактные записи.

Модель видит не весь реестр, а не более N действий, выбранных детерминированно по тексту
запроса, контексту проекта и макросам (совпадение по основам слов, без сети и без модели).
Проверка ответа остаётся строгой и идёт по ПОЛНОМУ реестру; дополнительно id, которого не
было в промпте, отклоняется (модель может использовать только показанное).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.action_layer.spec import ActionRegistry

# Всегда в каталоге (чтение состояния и остановка), если они есть в реестре.
ESSENTIAL_IDS = ("montage.get_board", "project.stop")
# Небольшой безопасный набор, когда запрос ничему не соответствует.
FALLBACK_IDS = ("montage.get_board", "montage.apply_status", "montage.montage_status",
                "shot.get_menu", "montage.get_cost_basis", "project.stop")
_RELATIVE_CUTOFF = 0.22   # доля от лучшего балла, ниже которой действие не берётся
_MIN_KEEP = 8             # но не меньше стольких действий при наличии совпадений
_SHORT_TOKENS = 2         # запрос из такого числа слов считается расплывчатым: берём шире
_SHORT_KEEP = 12
_CUT_MIN_SCORE = 8.0      # после вырезанной фразы вне каталога остаток должен совпасть уверенно
_MACRO_MIN_SCORE = 3.0
_MACRO_TOP = 2
_BONUS = 0.55             # вес действий, подтянутых макросом, от балла макроса
_PARAM_LIMIT = 420        # символов на описание одного параметра

_W_SYN, _W_TITLE, _W_TAG, _W_DESC = 2.0, 1.5, 1.2, 0.6

# Синонимы и разговорные формы: начала слов (сравнение по началу слова запроса).
SYNONYMS: dict[str, str] = {
    "change.apply": "предложен изменен промпт",
    "change.get": "предложен изменен верс истор промпт покаж парамет нод",
    "change.list": "список предложен изменен",
    "change.propose": "измен промпт предлож предложен парамет нод верс",
    "change.rollback": "откат откач верн назад изменен верс промпт",
    "group.delete": "удал убер сотр групп пользовательск",
    "group.get": "состав описан групп внутр нод откр",
    "group.insert": "встав вставь добав групп канвас",
    "group.list": "групп каталог библиотек списк",
    "group.save_from_selection": "сохран выделен групп",
    "group.shots_report": "отчет кадров шот qc проверк замечан",
    "group.update": "переимен названи групп",
    "media.get_recovery_assessment": "провер задач kie киэ состоян провайдер зависл завис восстанов",
    "media.list_unknown_outcomes": "неизвестн исход блокиров заблокир заблок застр потерян ответ outsee аутси kie киэ отправк",
    "media.release_unknown": "сними снять разблок блокиров заблокир неизвестн исход outsee аутси kie киэ разреш повтор отправк списан",
    "media.resume_poll": "докач возобнов дожд подтян доведи задач kie киэ провайдер",
    "montage.align_audio": "разбор аудио таймкод выровн озвучк голос синхрон asr nemo пауз",
    "montage.apply_queue": "примен правк очеред примен сгенер перегенер запуст генерац применить",
    "montage.apply_status": "статус примен правк прогресс идет генерац готов",
    "montage.cancel_queue_ops": "убер отмен очеред правк сними сброс",
    "montage.delete_child_frame": "удал дочерн кадр убер",
    "montage.delete_frame": "удал кадр убер снес",
    "montage.delete_image": "удал картинк изображен убер",
    "montage.delete_manual_ref": "удал реф референс загруж",
    "montage.delete_video": "удал видео клип убер",
    "montage.explode_scene": "раздел сцен кажд кадр отдельн разбей",
    "montage.generate_scene": "сгенер сцен пересобер куск придум заказ промт",
    "montage.generate_scene_images": "сгенер картинк изображен сцен нарису",
    "montage.get_board": "доск монтаж покаж прочит состоян посмотр",
    "montage.get_cost_basis": "стоимост цен сколько стоит бюджет оплат дорог очеред примен",
    "montage.improve_scene": "улучш сцен лучше",
    "montage.insert_frame": "добав встав нов кадр сцен",
    "montage.link_ref": "приложи реф референс привяж прикреп",
    "montage.list_ref_assets": "реф референс готов",
    "montage.merge_scenes": "объедин склей слей сцен",
    "montage.montage_status": "статус монтаж сборк собрал смонтир монтир",
    "montage.move_image": "перенес перетащ картинк слот",
    "montage.queue_ops": "постав очеред правк перегенер регенер картинк кадр изменен ии",
    "montage.recover_outsee": "забер галере outsee восстанов готов карточ",
    "montage.recover_status": "статус галере забор outsee",
    "montage.run_montage": "смонтир монтаж собер сбор ролик видео финальн",
    "montage.set_anchors": "якор",
    "montage.set_assemble_params": "настройк сборк хвост громкост музык субтитр пропуст интро",
    "montage.set_coverage": "крупност ракурс движен стык свет план роль родител дочерн покрыт",
    "montage.set_scene_chain": "последовательн цепочк разбер разбир действи шот кадров",
    "montage.set_video_trim": "обрез трим видео начал конец",
    "montage.set_voiceover": "закадр озвучк текст голос поправ исправ измен реплик",
    "montage.set_voiceover_span": "закрепи кусок закадр сцен фрагмент",
    "montage.split_scene": "раздел отдел сцен нов ножниц",
    "montage.suggest_scene_variants": "подбер вариант предлож иде",
    "montage.swap_shots": "помен мест шот перв втор",
    "montage.swap_slots": "помен мест слот картинк видео обмен кадр",
    "montage.unlink_ref": "убер реф персонаж предмет отвяж",
    "node.get_config": "настройк нод пульт операт покаж",
    "node.get_llm_route": "модел через какую нод маршрут",
    "node.reset_from_step": "сброс сброс шаг",
    "node.run_step": "запуст шаг нод",
    "node.set_config": "измен пульт нод настройк роль режим",
    "node.set_disabled": "отключ включ нод выключ выруб верни работ обратн",
    "node.set_prompt_variant": "вариант промт слот мастер нод",
    "node.clear_check_agent": "сброс агент проверк убер загруж нод",
    "node.get_run_cost_basis": "стоимост цен сколько стоит прогон оценк нод запуск шаг",
    "node.upload_check_agent": "загруз агент проверк нод файл txt",
    "node.upload_file": "загруз залей залит файл нод excel эксел xlsx подмен",
    "project.continue_pipeline": "продолж конвейер пайплайн дальше запуст проект",
    "project.pause": "пауз приостанов поставь замороз",
    "project.reload_xlsx": "перечит перезагруз обнов xlsx эксел excel таблиц",
    "project.resume": "сними пауз возобнов продолж пауз",
    "prompt.create_master": "созда нов мастер промт вариант добав",
    "prompt.delete_master": "удал мастер промт вариант убер",
    "prompt.get_master": "покаж прочит мастер промт текст",
    "prompt.save_master": "сохран перепиш измен правк обнов мастер промт",
    "montage.set_send_to_main_pc": "отправ основн компьютер сборк пк main",
    "montage.upload_image": "загруз залей залит положи картинк изображен файл слот компьютер",
    "montage.upload_music": "загруз залей залит музык фонов bgm трек компьютер",
    "montage.upload_ref": "загруз залей залит реф референс картинк кадр компьютер",
    "montage.upload_video": "загруз залей залит видео клип mp4 файл слот компьютер",
    "montage.upload_voice": "загруз залей залит озвучк голос аудио компьютер",
    "project.stop": "останов стоп прекрат хватит отмен",
    "shot.add_cell": "добав ячейк меню съемк",
    "shot.edit_cell_voiceover": "закадр ячейк меню съемк озвучк",
    "shot.edit_field": "пол шот меню съемк действи персонаж движен крупност стык",
    "shot.get_menu": "меню съемк покаж",
}

_STOP = frozenset("""для что как это или при над под про без чем так тоже нужно надо хочу пожалуйста
мне нам его ее них там тут быть было будет есть все всё еще уже тоже можно можешь сделай
сделать нужен нужна нужны этот эта эти эту того туда также чтобы если когда потом затем
после перед между себя свой своя свои мой моя мои наш наша наши там изменения изменение покажи покажите выведи
выполни выполните команду расскажи скажи""".split())

# Фразы вне каталога: вырезаются из запроса до подбора действий.
_OUT_OF_SCOPE = (
    re.compile(r"\b(?:удал\w*|сотр\w*|снес\w*|убер\w*|стер\w*)\s+(?:весь\s+|все\s+|всех\s+|этот\s+|мой\s+|"
               r"мои\s+|целиком\s+|полностью\s+)?проект\w*"),
    re.compile(r"\b(?:удал\w*|сотр\w*|снес\w*|убер\w*|стер\w*|очист\w*)\s+(?:\w+\s+)?"
               r"(?:баз\w*\s+данн\w*|репозитор\w*|сервер\w*|систем\w*)"),
    re.compile(r"\b(?:выключ\w*|перезагруз\w*)\s+(?:\w+\s+)?(?:компьютер\w*|пк\b|сервер\w*|windows)"),
    re.compile(r"\b(?:отправ\w*|пошл\w*|скин\w*)\s+(?:\w+\s+){0,2}(?:почт\w*|email|e-mail|мейл\w*|"
               r"телеграм\w*|telegram)"),
    re.compile(r"\b(?:git\w*|гит\w*|github|пуш\w*|запуш\w*|закоммит\w*|коммит\w*|пулл?-?реквест\w*|"
               r"pull\s+request)\b"),
    re.compile(r"\b(?:измени\w*|поменяй\w*|поправ\w*|исправ\w*|перепиши\w*|отредактируй\w*|напиши\w*|добав\w*|"
               r"отрефактор\w*)\s+(?:\w+\s+){0,2}(?:код\w*|исходник\w*|скрипт\w*|программ\w*|функци\w*)\b"),
    re.compile(r"\b(?:сотр\w*|удал\w*|стер\w*|очист\w*)\s+(?:все\s+|всё\s+|вс[её]х\s+)"
               r"(?:файл\w*|папк\w*|данны\w*|диск\w*)"),
    re.compile(r"\b(?:sql\w*|drop|терминал\w*|bash|powershell|shell|консол\w*|rm\s+-rf|sudo|"
               r"пароль|пароли|api[- ]?ключ\w*|токен\w*)\b"),
    re.compile(r"\b(?:установи\w*|скачай\w*)\s+(?:\w+\s+){0,2}(?:пакет\w*|библиотек\w*|программ\w*)\b"),
)


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zа-я0-9]+", _norm(text)) if len(w) >= 3 and w not in _STOP]


def _stem(word: str) -> str:
    n = len(word)
    return word if n <= 4 else word[:4] if n == 5 else word[:5]


def _id_words(action_id: str) -> list[str]:
    return [w for w in re.split(r"[._]", action_id) if len(w) >= 3]


@dataclass
class CatalogSelection:
    ids: list[str]                      # что показано модели (отсортировано по id)
    total: int                          # размер всего реестра
    no_match: bool = False              # ничего не подошло: безопасный набор + уточнение
    out_of_scope: str = ""              # вырезанная фраза вне каталога (если была)
    filtered: bool = True               # False: показан весь реестр
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def offered(self) -> frozenset[str]:
        return frozenset(self.ids)


def strip_out_of_scope(text: str) -> tuple[str, str]:
    """(остаток запроса, вырезанные фразы). Остаток и идёт в подбор действий."""
    norm = _norm(text)
    cut: list[str] = []
    for pat in _OUT_OF_SCOPE:
        for m in pat.finditer(norm):
            cut.append(m.group(0))
        norm = pat.sub(" ", norm)
    return norm, "; ".join(cut)


def _action_docs(registry: ActionRegistry) -> dict[str, list[tuple[float, set[str]]]]:
    docs: dict[str, list[tuple[float, set[str]]]] = {}
    for s in registry.list():
        syn = {w for w in SYNONYMS.get(s.id, "").split()}
        title = {_stem(w) for w in _words(s.title_ru)}
        tags = {_stem(w) for w in [*s.tags, *_id_words(s.id)] if len(w) >= 3}
        desc = {_stem(w) for w in _words(s.description_ru)}
        docs[s.id] = [(_W_SYN, syn), (_W_TITLE, title), (_W_TAG, tags), (_W_DESC, desc)]
    return docs


def _matches(token: str, keys: set[str]) -> bool:
    return any(token.startswith(k) for k in keys)


_BASE_ACTIONS = 56  # опорный размер каталога для idf: рост реестра не сдвигает веса прежних действий


def _score_docs(tokens: list[str], docs: dict[str, list[tuple[float, set[str]]]],
                base_n: int | None = None) -> dict[str, float]:
    """Сумма вес_поля * idf(слово) по уникальным словам запроса; редкие слова весят больше.

    idf считается от фиксированного опорного числа документов ``base_n``, а не от размера реестра."""
    n = max(1, base_n if base_n is not None else len(docs))
    per_token: list[dict[str, float]] = []
    for tok in tokens:
        hit = {}
        for aid, fields in docs.items():
            best = max((w for w, keys in fields if _matches(tok, keys)), default=0.0)
            if best:
                hit[aid] = best
        if hit:
            idf = math.log(1.0 + n / len(hit))
            per_token.append({aid: w * idf for aid, w in hit.items()})
    out: dict[str, float] = {}
    for hit in per_token:
        for aid, v in hit.items():
            out[aid] = out.get(aid, 0.0) + v
    return out


def _macro_bonus(tokens: list[str], registry: ActionRegistry, macros: Any) -> dict[str, float]:
    if macros is None:
        from app.services.action_layer import macros as macros_mod
        macros = macros_mod.BUILTIN_MACROS
    docs: dict[str, list[tuple[float, set[str]]]] = {}
    steps: dict[str, list[str]] = {}
    for m in macros:
        docs[m.id] = [(_W_TITLE, {_stem(w) for w in _words(m.title_ru)}),
                      (_W_DESC, {_stem(w) for w in _words(m.description_ru)})]
        steps[m.id] = [st.get("action_id") for st in m.steps if isinstance(st, dict)]
    ranked = sorted(_score_docs(tokens, docs).items(), key=lambda kv: (-kv[1], kv[0]))
    bonus: dict[str, float] = {}
    for mid, sc in ranked[:_MACRO_TOP]:
        if sc < _MACRO_MIN_SCORE:
            break
        for aid in steps[mid]:
            if isinstance(aid, str) and registry.find(aid) is not None:
                bonus[aid] = max(bonus.get(aid, 0.0), sc * _BONUS)
    return bonus


def _context_tokens(facts: dict | None, norm_text: str) -> list[str]:
    """Дополнительные слова из контекста проекта (выбранные сцена/кадр, статус, пустая доска)."""
    f = facts or {}
    extra: list[str] = []
    deictic = re.search(r"\b(?:этот|эту|этого|этой|текущ\w*|выбран\w*|здесь|тут|его|ее)\b", norm_text)
    if deictic:
        if f.get("selected_frame") is not None:
            extra.append("кадр")
        if f.get("selected_scene") is not None:
            extra.append("сцен")
    status = _norm(str(f.get("status") or ""))
    if re.search(r"run|process|generat|work|progress", status):
        extra += ["стоп", "статус"]
    if f.get("project_id") is not None and f.get("frame_count") == 0:
        extra += ["добав", "кадр"]
    return extra


def select_catalog(text: str, facts: dict | None, registry: ActionRegistry, max_catalog: int,
                   macros: Any = None) -> CatalogSelection:
    """Детерминированный отбор не более ``max_catalog`` действий для промпта."""
    all_ids = [s.id for s in registry.list()]
    total = len(all_ids)
    if max_catalog >= total:
        return CatalogSelection(all_ids, total, filtered=False)
    rest, cut = strip_out_of_scope(text)
    tokens = list(dict.fromkeys(_words(rest) + _context_tokens(facts, rest)))
    docs = _action_docs(registry)
    scores = _score_docs(tokens, docs, _BASE_ACTIONS)
    for aid, b in _macro_bonus(tokens, registry, macros).items():
        scores[aid] = scores.get(aid, 0.0) + b
    essentials = [a for a in ESSENTIAL_IDS if a in docs]
    ranked = sorted(((a, v) for a, v in scores.items() if v > 0 and a not in essentials),
                    key=lambda kv: (-kv[1], kv[0]))
    if not ranked or (cut and ranked[0][1] < _CUT_MIN_SCORE):
        fb = [a for a in FALLBACK_IDS if a in docs][:max_catalog]
        return CatalogSelection(sorted(fb), total, no_match=True, out_of_scope=cut)
    room = max(0, max_catalog - len(essentials))
    top = ranked[0][1]
    vague = len(tokens) <= _SHORT_TOKENS
    keep = [(a, v) for a, v in ranked if v >= top * (0.05 if vague else _RELATIVE_CUTOFF)]
    floor = _SHORT_KEEP if vague else _MIN_KEEP
    if len(keep) < min(floor, len(ranked), room):
        keep = ranked[:min(floor, room)]
    keep = keep[:room]
    ids = sorted({*essentials, *(a for a, _ in keep)})
    return CatalogSelection(ids, total, out_of_scope=cut, scores=dict(keep))


# --- компактные записи ------------------------------------------------------

_T = {"integer": "int", "string": "str", "boolean": "bool", "number": "num", "array": "list",
      "object": "obj"}


def _type_str(sch: Any, depth: int = 0) -> str:
    if not isinstance(sch, dict):
        return "any"
    if "enum" in sch and isinstance(sch["enum"], list) and len(sch["enum"]) <= 12:
        return "{" + "|".join(str(v) for v in sch["enum"]) + "}"
    typ = sch.get("type")
    if typ == "array":
        inner = _type_str(sch.get("items"), depth + 1) if depth < 2 else "any"
        base = f"[{inner}]"
    elif typ == "object":
        props = sch.get("properties") or {}
        if depth >= 2 or not props:
            base = "obj"
        else:
            req = set(sch.get("required") or [])
            base = "{" + ",".join(f"{k}{'*' if k in req else ''}:{_type_str(v, depth + 1)}"
                                  for k, v in props.items()) + "}"
    else:
        base = _T.get(str(typ), "any")
        lo, hi = sch.get("minimum"), sch.get("maximum")
        if base in ("int", "num") and hi is not None:
            base += f"[{lo if lo is not None else ''}..{hi}]"
        elif base == "str" and sch.get("maxLength") and sch["maxLength"] <= 500:
            base += f"<={sch['maxLength']}"
    return base


def compact_entry(spec: Any) -> dict:
    props = (spec.params_schema or {}).get("properties") or {}
    req = set((spec.params_schema or {}).get("required") or [])
    params = []
    for name in sorted(props, key=lambda k: (k != "project_id", k not in req, k)):
        item = f"{name}{'*' if name in req else ''}:{_type_str(props[name])}"
        params.append(item[:_PARAM_LIMIT])
    return {"id": spec.id, "t": spec.title_ru, "risk": spec.risk, "p": ",".join(params)}


def catalog_entries(registry: ActionRegistry, ids: list[str] | None = None) -> list[dict]:
    wanted = None if ids is None else set(ids)
    return [compact_entry(s) for s in registry.list() if wanted is None or s.id in wanted]


def full_entries(registry: ActionRegistry) -> list[dict]:
    """Прежний полный формат (для сравнения размера в тестах и отчётах)."""
    from app.services.action_layer.planner import _compact_schema
    return [{"id": s.id, "title": s.title_ru, "what": s.description_ru[:160], "risk": s.risk,
             "params_schema": _compact_schema(s.params_schema)} for s in registry.list()]


def payload_chars(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
