"""Детерминированные правила вокруг ответа модели-планировщика (без сети и без модели).

1. Значения по умолчанию: пропущенные обязательные параметры подставляются из фактов проекта
   (генератор и разрешение, шот, лимиты очереди, шаг и вариант мастер-промта); о каждом
   подставленном значении человек читает в explanation_ru до подтверждения.
2. Защита от лишнего: платный/разрушающий/фоновый шаг из таблицы GUARD_HINTS, о котором нет ни
   одного слова в запросе, убирается из плана (макросы идут другим путём и сюда не попадают).
3. Загрузки файлов: файл из текста не получить, поэтому вместо шага возвращается уточнение
   «прикрепите файл кнопкой загрузки» и действие, для которого он нужен.
4. Неоднозначный шот у разрушающего удаления превращается в вопрос с вариантами.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.action_layer import planner_context as pc

MAX_OPTIONS = 4
MAX_OPTION_LEN = 60

UPLOAD_ACTIONS = frozenset({
    "montage.upload_image", "montage.upload_video", "montage.upload_voice",
    "montage.upload_music", "montage.upload_ref", "node.upload_file", "node.upload_check_agent"})
FILE_PARAMS = ("source_path", "data_base64", "filename")

_GEN_KEYS = ("image_generator", "image_resolution", "video_generator", "video_resolution")
MAX_IMPROVE_OPS = 25


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zа-я0-9_]+", _norm(text))


def _has_stem(words: list[str], stems: tuple[str, ...]) -> bool:
    return any(w.startswith(s) for w in words for s in stems)


# --- значения по умолчанию ----------------------------------------------------------------

@dataclass
class Filled:
    params: dict
    notes: list[str] = field(default_factory=list)
    question: str = ""
    options: list[str] = field(default_factory=list)


def _row_by_number(facts: dict, number: Any) -> list | None:
    for r in facts.get("frames") or []:
        if r[0] == number:
            return r
    return None


def _row_by_id(facts: dict, fid: Any) -> list | None:
    for r in facts.get("frames") or []:
        if r[1] == fid:
            return r
    return None


def _single_shot(row: list | None, mask_index: int) -> int | None:
    """Шот, о котором идёт речь, если он один: у кадра один шот или файл есть только в одном.

    mask_index: 4 - картинки, 5 - видео. None - неоднозначно (файлы в обоих шотах)."""
    if row is None:
        return None
    mask = str(row[mask_index])
    if mask in ("1", "2"):
        return int(mask)
    if mask == "12":
        return None
    return 1  # файлов нет: шот 1 (у кадра с одним шотом он и есть, иначе действие бессмысленно)


def fill_defaults(action_id: str, params: dict, facts: dict | None, schema: dict) -> Filled:
    """Подставляет недостающие параметры. Заданное моделью не перезаписывается."""
    out = dict(params)
    notes: list[str] = []
    if not facts or facts.get("project_id") is None:
        return Filled(out)
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    defaults = facts.get("defaults") or {}
    if "project_id" in props and "project_id" not in out:
        out["project_id"] = facts["project_id"]

    if action_id in pc.GEN_ACTIONS:
        for key in _GEN_KEYS:
            if key in props and key not in out and defaults.get(key):
                out[key] = defaults[key]
                notes.append(f"{key}={defaults[key]}")
    if action_id == "montage.apply_queue":
        queue = facts.get("queue") or {}
        ops = queue.get("ops") or []
        wanted = set(out.get("frame_numbers") or [])
        sel = [o for o in ops if not wanted or o[0] in wanted]
        n_img = sum(1 for o in sel if str(o[1]).startswith("image_"))
        n_vid = sum(1 for o in sel if str(o[1]).startswith("video_"))
        if "max_image_ops" not in out:
            out["max_image_ops"] = n_img
            notes.append(f"max_image_ops={n_img} (по очереди)")
        if "max_video_ops" not in out:
            out["max_video_ops"] = n_vid
            notes.append(f"max_video_ops={n_vid} (по очереди)")
    if action_id == "montage.improve_scene" and "max_image_ops" not in out:
        row = _row_by_id(facts, out.get("frame_id"))
        scene = pc.scene_of(facts.get("frames") or [], row[0]) if row else []
        n = min(MAX_IMPROVE_OPS, 1 + 2 * len(scene)) if scene else MAX_IMPROVE_OPS
        out["max_image_ops"] = n
        notes.append(f"max_image_ops={n} (по размеру сцены)")

    _fill_shots(action_id, out, facts, notes)

    _normalize_values(action_id, out, notes)
    return Filled(out, notes)


def _fill_shots(action_id: str, out: dict, facts: dict, notes: list[str]) -> None:
    """Обязательный шот подставляется, только когда он однозначен."""
    pairs: list[tuple[str, str, int]] = []
    if action_id == "montage.swap_slots":
        idx = 5 if out.get("kind") == "video" else 4
        pairs = [("a_shot", "a_frame", idx), ("b_shot", "b_frame", idx)]
    elif action_id == "montage.move_image":
        pairs = [("from_shot", "from_frame", 4), ("to_shot", "to_frame", 4)]
    elif action_id == "montage.set_video_trim":
        pairs = [("shot", "frame_number", 5)]
    for shot_key, frame_key, idx in pairs:
        if shot_key in out or frame_key not in out:
            continue
        shot = _single_shot(_row_by_number(facts, out[frame_key]), idx)
        if shot is not None:
            out[shot_key] = shot
            notes.append(f"{shot_key}={shot}")


def shot_ambiguity(action_id: str, params: dict, facts: dict | None) -> tuple[str, list[str]] | None:
    """Шот не назван, а файлы есть в обоих шотах: вопрос, а не угадывание.

    Удаление картинки/видео и постановка перегенерации в очередь."""
    if not facts:
        return None
    if action_id in ("montage.delete_image", "montage.delete_video") and "shot" not in params:
        idx = 4 if action_id == "montage.delete_image" else 5
        row = _row_by_number(facts, params.get("frame_number"))
        if row is not None and str(row[idx]) == "12":
            what = "картинку" if idx == 4 else "видео"
            return (f"У кадра {row[0]} {what} есть в обоих шотах. Какой удалить?", ["шот 1", "шот 2"])
    if action_id == "montage.queue_ops":
        for op in params.get("ops") or []:
            if not isinstance(op, dict) or "shot" in op:
                continue
            idx = 4 if str(op.get("type", "")).startswith("image_") else 5
            row = _row_by_number(facts, op.get("frame_number"))
            if row is not None and str(row[idx]) == "12":
                what = "картинки" if idx == 4 else "видео"
                return (f"У кадра {row[0]} {what} есть в обоих шотах. Для какого шота поставить в очередь?",
                        ["шот 1", "шот 2", "оба шота"])
    return None


def defaults_used(action_id: str, params: dict, facts: dict | None, text: str) -> list[str]:
    """Значения, равные умолчаниям проекта и не названные в запросе (для строки в explanation_ru)."""
    if not facts:
        return []
    defaults = facts.get("defaults") or {}
    low = _norm(text)
    out: list[str] = []
    keys = _GEN_KEYS if action_id in pc.GEN_ACTIONS else ()
    for key in keys:
        val = params.get(key)
        if val is not None and val == defaults.get(key) and _norm(str(val)) not in low:
            out.append(f"{key}={val}")
    return out


_COVERAGE_CHOICES = {
    "coverage_plan": ("ОБЩИЙ", "ДАЛЬНИЙ", "СРЕДНИЙ", "КРУПНЫЙ", "ДЕТАЛЬ"),
    "coverage_angle": ("фронт", "3/4", "с плеча", "сверху", "снизу", "макро"),
    "coverage_move": ("статика", "наезд", "отъезд", "панорама", "следование", "ручная"),
}


def _normalize_values(action_id: str, out: dict, notes: list[str]) -> None:
    """Детерминированные поправки значений: шкала громкости и поля покрытия кадра."""
    if action_id == "montage.set_assemble_params":
        level = out.get("bgm_level")
        if isinstance(level, float) and 0 < level < 1:  # «0.2» = доля, шкала сервера 0..100
            out["bgm_level"] = round(level * 100)
            notes.append(f"bgm_level={out['bgm_level']} (доля {level} на шкале 0..100)")
    elif action_id == "montage.set_coverage":
        if out.get("type") != "coverage_kind":
            out.pop("parent_number", None)  # поле только для coverage_kind (дочерний кадр)
        choices = _COVERAGE_CHOICES.get(out.get("type"))
        value = out.get("value")
        if choices and isinstance(value, str):
            low = _norm(value)
            for c in choices:
                if low == _norm(c) or _norm(c) in low.split() or low.startswith(_norm(c)):
                    out["value"] = c
                    break


# --- мастер-промты: шаг и вариант -----------------------------------------------------------

PROMPT_ACTIONS = frozenset({"prompt.get_master", "prompt.save_master", "prompt.delete_master",
                            "prompt.create_master"})


def _variant_names(step: str) -> list[str]:
    from app.services import prompt_library as pl
    try:
        return list(pl.list_prompts(step))
    except Exception:  # noqa: BLE001
        return []


def _real_steps(pl) -> list[str]:
    """Шаги с собственными промтами: слоты enrich_N используют общий список excel_gpt."""
    return [s for s in pl.STEP_FOLDERS if s not in pl.ENRICH_STEP_CODES or s == pl.EXCEL_GPT_UNIFIED_STEP]


def resolve_prompt_target(action_id: str, params: dict, facts: dict | None,
                          text: str) -> tuple[dict, list[str], tuple[str, list[str]] | None]:
    """(params, заметки, вопрос). Шаг мастер-промта берётся из слов запроса, затем оттуда, где есть
    названный вариант; неясно - вопрос. Шаг по статусу проекта только при слове «текущий».
    Удалять вариант, выбранный в проекте, и default нельзя: вместо плана вопрос."""
    if not facts or action_id not in PROMPT_ACTIONS:
        return params, [], None
    from app.services import prompt_library as pl
    out, notes = dict(params), []
    overrides = facts.get("prompt_overrides") or {}
    words = set(_words(text))
    name = out.get("name")
    named = [s for s in pl.STEP_FOLDERS if s.lower() in words]
    step: str | None = None
    if len(named) == 1:
        step = named[0]
    elif len(named) > 1:
        return out, notes, ("В каком шаге? Названо несколько.", named[:4])
    elif "текущ" in _norm(text) and (facts.get("defaults") or {}).get("step_code"):
        step = facts["defaults"]["step_code"]
    elif isinstance(name, str) and name and action_id != "prompt.create_master":
        having = [s for s in _real_steps(pl) if name in _variant_names(s)]
        if len(having) == 1:
            step = having[0]
        elif len(having) > 1:
            return out, notes, (f"Вариант «{name}» есть в нескольких шагах. В каком шаге?", having[:4])
        elif out.get("step_code") in pl.STEP_FOLDERS:
            step = out["step_code"]
    if step is None:
        return out, notes, ("Для какого шага мастер-промт?", list(overrides)[:4])
    out["step_code"] = step
    if action_id in ("prompt.get_master", "prompt.save_master") and not out.get("name"):
        out["name"] = overrides.get(step) or (facts.get("defaults") or {}).get("prompt_variant") \
            or pl.DEFAULT_NAME
        notes.append(f"name={out['name']}")
    if action_id == "prompt.delete_master":
        name = out.get("name")
        if name == pl.DEFAULT_NAME or (name and name == overrides.get(step)):
            others = [n for n in _variant_names(step) if n not in (pl.DEFAULT_NAME, overrides.get(step))]
            return out, notes, (
                f"Вариант «{name}» нужен шагу {step} (выбран в проекте или default), удалять его нельзя. "
                "Выберите другой вариант или сначала смените выбранный.", others[:4])
    return out, notes, None


# --- подсказки по действиям (идут в промпт только для показанных действий) ------------------------

_UPLOAD_NOTE = "Шаг без source_path, data_base64, filename: файл прикрепит человек, сервер спросит сам."
ACTION_NOTES: dict[str, str] = {
    "montage.swap_slots": "Шот не назван: шот 1 у обоих; у обоих по 2 шота с такими файлами - два шага (1-1, 2-2).",
    "montage.swap_shots": "kind не пиши, если не сказано (по умолчанию картинки и видео).",
    "montage.queue_ops": "Только постановка в очередь, apply не добавляй. shot не пиши, если у кадра один шот; "
                         "файлы в обоих шотах и шот не назван - вопрос (шот 1/шот 2/оба шота).",
    "montage.cancel_queue_ops": "ops: операции кадра из queue.ops [кадр,тип,шот]; их нет - вопрос. "
                                "Вся очередь - clear_all.",
    "montage.apply_queue": "max_image_ops/max_video_ops опусти: сервер возьмёт по очереди названных кадров.",
    "montage.improve_scene": "max_image_ops опусти. frame_id головы сцены.",
    "montage.generate_scene": "Пишет текст сцены по заказу: prompt = заказ из запроса. frame_id головы сцены.",
    "montage.generate_scene_images": "Рисует картинки уже существующих кадров, сцену не переписывает; "
                                     "scene_prompt опусти.",
    "montage.delete_frame": "Любой кадр («удали кадр N»); delete_child_frame только если сказано "
                            "«дочерний»/«шот сцены».",
    "montage.delete_image": "Файлы в обоих шотах и шот не назван - вопрос.",
    "montage.delete_video": "Файлы в обоих шотах и шот не назван - вопрос.",
    "montage.merge_scenes": "left/right = frame_id голов соседних сцен.",
    "montage.explode_scene": "frame_id головы сцены.",
    "montage.split_scene": "frame_id отделяемого кадра (не головы сцены).",
    "montage.set_coverage": "plan ОБЩИЙ|ДАЛЬНИЙ|СРЕДНИЙ|КРУПНЫЙ|ДЕТАЛЬ; angle фронт|3/4|с плеча|сверху|снизу|макро; "
                            "move статика|наезд|отъезд|панорама|следование|ручная; light дневной|ночной|закат|"
                            "рассвет|контровой|холодный верхний|туман. parent_number только для coverage_kind.",
    "montage.set_assemble_params": "Передай только названные настройки. bgm_level: шкала 0..100, доля 0.2 = 20.",
    "montage.insert_frame": "Без уточнения kind=parent (новая сцена после кадра), voiceover не нужен.",
    "montage.set_scene_chain": "action = цепочка действий из запроса («А → Б»).",
    "montage.set_anchors": "anchors = [{anchor}] по названным якорям; change и main не нужны.",
    "montage.align_audio": "method не спрашивай (по умолчанию nemo_auto).",
    "group.insert": "after не нужен (группа в конец канваса).",
    "node.run_step": "Лимиты опусти: сервер подставит из расчёта по проекту.",
    "node.reset_from_step": "step_code: названный шаг; «текущий» = defaults.step_code.",
    "change.apply": "«предложение N» = proposals n=N: его id и digest.",
    "change.rollback": "target и v из versions (свежая цель нужного вида); нет данных - вопрос.",
    "change.get": "target из versions (свежая цель нужного вида) или proposal_id.",
    "change.propose": "Нужно содержимое (candidate); без него - вопрос.",
    "prompt.get_master": "step_code: из слов запроса («шага script»), иначе где есть названный вариант; "
                         "неясно - вопрос. name: выбранный в проекте (prompts.<шаг>.sel) или default.",
    "prompt.save_master": "step_code как у get_master; content = новый текст из запроса.",
    "prompt.create_master": "step_code из слов запроса; name и content из запроса, иначе вопрос.",
    "prompt.delete_master": "step_code из слов запроса или где есть вариант; выбранный в проекте и default "
                            "не удаляются.",
    "shot.edit_cell_voiceover": "parent_uuid из frame_uuids.",
    "shot.edit_field": "frame_uuid из frame_uuids.",
}
for _aid in UPLOAD_ACTIONS:
    ACTION_NOTES[_aid] = _UPLOAD_NOTE


def notes_for(offered) -> dict[str, str]:
    """Подсказки только для показанных действий; одинаковый текст загрузок не повторяется."""
    out: dict[str, str] = {}
    for aid in sorted(offered):
        note = ACTION_NOTES.get(aid)
        if not note:
            continue
        if aid in UPLOAD_ACTIONS:
            out["upload.*"] = note
        else:
            out[aid] = note
    return out


def with_defaults_note(explanation: str, notes: list[str]) -> str:
    notes = list(dict.fromkeys(notes))
    if not notes:
        return explanation
    tail = "По умолчанию подставлено: " + ", ".join(notes) + "."
    return (explanation.strip() + " " + tail).strip()[:1000]


# --- защита от лишнего --------------------------------------------------------------------

_DELETE = ("удал", "убер", "убра", "сотр", "снес", "снос", "выкин", "очист", "delete", "remove")

# Слова запроса (начала слов), без которых платный/разрушающий/фоновый шаг не нужен.
GUARD_HINTS: dict[str, tuple[str, ...]] = {
    "montage.run_montage": ("смонтир", "монтир", "монтаж", "собер", "собери", "собра", "сборк",
                            "рендер", "финальн", "собират"),
    "montage.align_audio": ("разбер", "разбор", "таймкод", "выровн", "синхрон", "asr", "nemo",
                            "align", "разметь"),
    "montage.apply_queue": ("примен", "apply", "запуст", "сгенер", "генерир", "генерац"),
    "montage.generate_scene_images": ("сгенер", "генерир", "нарису", "созда", "сдела"),
    "montage.generate_scene": ("сгенер", "генерир", "придум", "пересобер", "сочин", "созда",
                               "напиш", "пересоздай"),
    "montage.improve_scene": ("улучш", "лучше", "живее", "динамик", "динамич", "доработ", "усиль"),
    "montage.suggest_scene_variants": ("вариант", "подбер", "предлож", "иде", "подскаж"),
    "montage.recover_outsee": ("outsee", "аутси", "галере", "забер", "восстанов", "достань"),
    "montage.merge_scenes": ("объедин", "склей", "слей", "слить", "соедин", "склеи"),
    "montage.split_scene": ("раздел", "отдел", "раздро", "разбей", "разбить", "раздр", "ножниц",
                            "отреж", "разреж", "режь"),
    "montage.explode_scene": ("раздел", "разбей", "разбить", "кажд", "отдельн", "раздро"),
    "montage.delete_frame": _DELETE, "montage.delete_child_frame": _DELETE,
    "montage.delete_image": _DELETE, "montage.delete_video": _DELETE,
    "montage.delete_manual_ref": _DELETE, "group.delete": _DELETE,
    "prompt.delete_master": _DELETE, "node.clear_check_agent": _DELETE + ("сброс", "сбрас"),
    "node.run_step": ("запуст", "прогон", "прогна", "старт", "run", "выполн", "гони", "пуск"),
    "node.reset_from_step": ("сброс", "сбрас", "обнул", "заново", "перезапуст", "reset"),
    "project.continue_pipeline": ("продолж", "дальш", "далее", "конвейер", "пайплайн", "запуст"),
    "project.resume": ("возобнов", "продолж", "сними", "снять", "resume", "разблок"),
    "project.reload_xlsx": ("xlsx", "эксел", "excel", "таблиц", "перечит", "перезагруз", "обнов"),
    "prompt.save_master": ("сохран", "перепиш", "измен", "правк", "обнов", "замен", "запиш",
                           "поправ", "исправ", "отредакт"),
    "change.apply": ("примен", "apply", "внес", "внеси", "примени"),
    "change.rollback": ("откат", "откач", "верни", "вернуть", "назад", "отмен", "прежн"),
    "montage.set_anchors": ("якор", "anchor"),
    "montage.set_scene_chain": ("цепоч", "последовательн", "разбер", "действи"),
}
_UPLOAD_HINT = ("загруз", "upload", "залей", "залить", "прикреп", "подмен", "замен", "положи",
                "добав", "постав")
for _aid in UPLOAD_ACTIONS:
    GUARD_HINTS.setdefault(_aid, _UPLOAD_HINT)


def is_guarded(spec: Any) -> bool:
    """Только действия из явной таблицы GUARD_HINTS: незнакомое действие никогда не убирается."""
    return spec.id in GUARD_HINTS


def unguarded_risky(registry: Any) -> list[str]:
    """Платные, разрушающие и фоновые действия реестра без записи в GUARD_HINTS (для теста)."""
    return [s.id for s in registry.list()
            if (s.risk in ("paid", "destructive") or s.is_job) and s.id not in GUARD_HINTS]


def request_hints_action(text: str, spec: Any) -> bool:
    """Есть ли в запросе хоть одно слово, указывающее на это действие."""
    return _has_stem(_words(text), tuple(GUARD_HINTS.get(spec.id) or ()))


def drop_unrequested(steps: list[dict], text: str, registry: Any) -> tuple[list[dict], list[str]]:
    """(оставшиеся шаги с пересчитанными depends_on, названия убранных действий)."""
    dropped: set[int] = set()
    for i, st in enumerate(steps):
        spec = registry.find(st["action_id"])
        if spec is None or not is_guarded(spec):
            continue
        if not request_hints_action(text, spec):
            dropped.add(i)
    if not dropped:
        return steps, []
    changed = True
    while changed:  # зависимые от убранного тоже убираются
        changed = False
        for i, st in enumerate(steps):
            if i not in dropped and any(d in dropped for d in st["depends_on"]):
                dropped.add(i)
                changed = True
    remap: dict[int, int] = {}
    kept: list[dict] = []
    for i, st in enumerate(steps):
        if i in dropped:
            continue
        remap[i] = len(kept)
        kept.append({**st, "depends_on": sorted({remap[d] for d in st["depends_on"]})})
    names = [registry.find(steps[i]["action_id"]).title_ru for i in sorted(dropped)]
    return kept, names


# --- загрузки файлов ----------------------------------------------------------------------

_FORMATS = {
    "montage.upload_image": "PNG", "montage.upload_video": "MP4", "montage.upload_voice": "аудио",
    "montage.upload_music": "аудио", "montage.upload_ref": "картинка",
    "node.upload_file": "xlsx, txt или md", "node.upload_check_agent": "txt или md"}


def _target_text(action_id: str, p: dict) -> str:
    if action_id in ("montage.upload_image", "montage.upload_video"):
        slot = "slot parent" if p.get("slot") == "parent" else f"шот {p.get('shot', 1)}"
        return f"кадр {p.get('frame_number', '?')}, {slot}"
    if action_id == "montage.upload_ref":
        return f"реф кадра {p.get('frame_number', '?')}" + (f" «{p['name']}»" if p.get("name") else "")
    if action_id == "montage.upload_voice":
        return "озвучка проекта"
    if action_id == "montage.upload_music":
        return "фоновая музыка проекта"
    if action_id == "node.upload_file":
        return f"нода {p.get('node_key', '?')}"
    return f"агент проверки ноды {p.get('node_key', '?')}"


def upload_clarification(action_id: str, params: dict, title: str) -> dict:
    """Структурное уточнение вместо шага загрузки: файл прикрепляется кнопкой, а не текстом."""
    clean = {k: v for k, v in params.items() if k not in FILE_PARAMS and k != "project_id"}
    question = (f"Прикрепите файл кнопкой загрузки. Он нужен для действия «{title}» "
                f"({_target_text(action_id, clean)}; формат: {_FORMATS.get(action_id, 'файл')}).")
    return {"question": question[:500], "options": ["Прикрепить файл"],
            "attachment": {"action_id": action_id, "params": clean,
                           "formats": _FORMATS.get(action_id, "файл")}}


_UPLOAD_INTENT = ("загруз", "загруж", "залей", "залить", "залив", "upload", "прикреп", "подгруз", "положи",
                  "подмен")
_UPLOAD_NOUNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("montage.upload_ref", ("реф", "референс")),
    ("node.upload_check_agent", ("агент",)),
    ("montage.upload_voice", ("озвуч", "голос", "аудио", "voice")),
    ("montage.upload_music", ("музык", "bgm", "трек")),
    ("montage.upload_video", ("видео", "клип", "mp4")),
    ("montage.upload_image", ("картин", "изображ", "фото", "png", "image")),
    ("node.upload_file", ("excel", "эксел", "xlsx", "файл", "таблиц")),
)


def upload_intent_action(text: str, offered: frozenset[str] | set[str]) -> str | None:
    """Какое действие загрузки имел в виду запрос (по словам), если оно показано модели."""
    words = _words(text)
    if not _has_stem(words, _UPLOAD_INTENT):
        return None
    for aid, nouns in _UPLOAD_NOUNS:
        if aid in offered and _has_stem(words, nouns):
            return aid
    return None


def upload_params_from_text(action_id: str, text: str) -> dict:
    p: dict = {}
    m = re.search(r"(?:кадр\w*|сцен\w*|frame)\s*(?:№|#|номер\s*)?(\d+)", _norm(text))
    if m and action_id in ("montage.upload_image", "montage.upload_video", "montage.upload_ref"):
        p["frame_number"] = int(m.group(1))
    node = re.search(r"\b(n_[\w.\-]+)", text or "")
    if node and action_id.startswith("node."):
        p["node_key"] = node.group(1)
    return p


def clean_options(raw: Any) -> list[str]:
    """Короткие варианты ответа для кнопок: только непустые строки, не больше MAX_OPTIONS."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            val = item.strip()[:MAX_OPTION_LEN]
            if val not in out:
                out.append(val)
        if len(out) >= MAX_OPTIONS:
            break
    return out
