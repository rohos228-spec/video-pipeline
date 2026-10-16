from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.action_layer import planner_context as pc

MAX_OPTIONS = 4
MAX_OPTION_LEN = 60

UPLOAD_ACTIONS = frozenset({
    "montage.upload_image", "montage.upload_video", "montage.upload_voice",
    "montage.upload_music", "montage.upload_ref", "node.upload_file", "node.upload_check_agent",
    "project.upload_xlsx", "hero.replace_image", "storage.upload_file"})
FILE_PARAMS = ("source_path", "data_base64", "filename")

_GEN_KEYS = ("image_generator", "image_resolution", "video_generator", "video_resolution")
MAX_IMPROVE_OPS = 25


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zа-я0-9_]+", _norm(text))


def _has_stem(words: list[str], stems: tuple[str, ...]) -> bool:
    return any(w.startswith(s) for w in words for s in stems)



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
    if row is None:
        return None
    mask = str(row[mask_index])
    if mask in ("1", "2"):
        return int(mask)
    if mask == "12":
        return None
    return 1


def fill_defaults(action_id: str, params: dict, facts: dict | None, schema: dict) -> Filled:
    out = dict(params)
    notes: list[str] = []
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    for key in required:
        prop = props.get(key)
        if key not in out and isinstance(prop, dict) and "const" in prop:
            out[key] = prop["const"]
    if not facts or facts.get("project_id") is None:
        return Filled(out)
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

    if action_id == "publish.send":
        _fill_publish(out, facts, notes)

    _frame_ids_to_numbers(out, props, facts, notes)
    _fill_shots(action_id, out, facts, notes)

    _normalize_values(action_id, out, notes)
    return Filled(out, notes)


def _fill_publish(out: dict, facts: dict, notes: list[str]) -> None:
    server = facts.get("publish_server") or {}
    out.pop("caption", None)
    if server.get("caption"):
        out["caption"] = server["caption"]
    if "platforms" not in out and server.get("platforms"):
        out["platforms"] = list(server["platforms"])
    if server.get("caption"):
        notes.append("подпись публикации подставлена сервером из темы проекта")


_NUMBER_KEYS = ("frame_number", "a_frame", "b_frame", "from_frame", "to_frame", "parent_number")


def _frame_ids_to_numbers(out: dict, props: dict, facts: dict, notes: list[str]) -> None:
    rows = facts.get("frames") or []
    if not rows:
        return
    max_no = int(facts.get("max_frame_number") or max(r[0] for r in rows))
    by_id = {r[1]: r[0] for r in rows}

    def fix(v: Any) -> Any:
        if isinstance(v, int) and not isinstance(v, bool) and v > max_no and v in by_id:
            notes.append(f"номер кадра {by_id[v]} (модель назвала идентификатор {v})")
            return by_id[v]
        return v

    for key in _NUMBER_KEYS:
        if key in props and key in out:
            out[key] = fix(out[key])
    if "frame_numbers" in props and isinstance(out.get("frame_numbers"), list):
        out["frame_numbers"] = [fix(v) for v in out["frame_numbers"]]


def _fill_shots(action_id: str, out: dict, facts: dict, notes: list[str]) -> None:
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
    if action_id == "montage.set_assemble_params":
        level = out.get("bgm_level")
        if isinstance(level, float) and 0 < level < 1:
            out["bgm_level"] = round(level * 100)
            notes.append(f"bgm_level={out['bgm_level']} (доля {level} на шкале 0..100)")
    elif action_id == "montage.set_coverage":
        if out.get("type") != "coverage_kind":
            out.pop("parent_number", None)
        choices = _COVERAGE_CHOICES.get(out.get("type"))
        value = out.get("value")
        if choices and isinstance(value, str):
            low = _norm(value)
            for c in choices:
                if low == _norm(c) or _norm(c) in low.split() or low.startswith(_norm(c)):
                    out["value"] = c
                    break



_PLATFORM_WORDS: dict[str, tuple[str, ...]] = {
    "tiktok": ("тикток", "тик-ток", "tiktok", "тиктоке", "тик"),
    "yt_shorts": ("ютуб", "ютьюб", "youtube", "шортс", "шортсы", "shorts", "ютубе"),
    "ig_reels": ("инстаграм", "инстаграме", "инста", "instagram", "рилс", "рилсы", "reels", "инсту"),
    "vk_clips": ("вк", "вконтакте", "vk", "клипы", "клипах"),
    "likee": ("likee", "лайки"),
}
_PLATFORM_EXCLUDE = ("кроме", "исключ", "без", "минус")


def named_platforms(text: str) -> list[str]:
    words = _words(text)
    if _has_stem(words, _PLATFORM_EXCLUDE):
        return []
    return [pid for pid, names in _PLATFORM_WORDS.items()
            if any(w == n or (len(n) > 3 and w.startswith(n)) for w in words for n in names)]


_NUM_WORDS = {"один": 1, "одну": 1, "одной": 1, "одного": 1, "два": 2, "две": 2, "двух": 2, "три": 3, "трех": 3,
              "четыре": 4, "четырех": 4, "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7,
              "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9, "десять": 10, "десяти": 10}
_OPS_LIMIT = re.compile(
    r"(?:не\s+(?:больше|более|свыше|выше)|максимум|макс\.?|лимит\w*|до|только)\s+(\d{1,3}|[а-я]+)\s+"
    r"(?:[а-я]+\s+)?(картин\w*|изображен\w*|фото\w*|видео\w*|клип\w*|ролик\w*)")


_LIMIT_CUE = r"(?:не\s+(?:больше|более|свыше|выше)|максимум|макс\.?|лимит\w*|до|только)"
_OPS_NOUN = r"(?:операц\w*|действ\w*|правок|штук\w*|генерац\w*|обработ\w*)"
_MEDIA = r"(картин\w*|изображен\w*|фото\w*|видео\w*|клип\w*|ролик\w*)"
_OPS_LIMIT_AFTER = re.compile(_MEDIA + r"\s+(?:[а-я]+\s+){0,2}?" + _LIMIT_CUE + r"\s+(\d{1,3}|[а-я]+)\s+" + _OPS_NOUN)
_OPS_LIMIT_BEFORE = re.compile(_LIMIT_CUE + r"\s+(\d{1,3}|[а-я]+)\s+" + _OPS_NOUN + r"\s+(?:на|для|по|с)\s+" + _MEDIA)


def named_ops_limits(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    low = _norm(text)
    found = [(m.group(1), m.group(2)) for m in _OPS_LIMIT.finditer(low)]
    found += [(m.group(2), m.group(1)) for m in _OPS_LIMIT_AFTER.finditer(low)]
    found += [(m.group(1), m.group(2)) for m in _OPS_LIMIT_BEFORE.finditer(low)]
    for raw, kind in found:
        num = int(raw) if raw.isdigit() else _NUM_WORDS.get(raw)
        if num is None:
            continue
        key = "max_video_ops" if kind.startswith(("видео", "клип", "ролик")) else "max_image_ops"
        out.setdefault(key, num)
    return out


def _small_numbers() -> dict[str, int]:
    out: dict[str, int] = {}
    forms = {
        0: "ноль", 1: "один одна одну одного одной одному", 2: "два две двух", 3: "три трех трем",
        4: "четыре четырех", 5: "пять пяти", 6: "шесть шести", 7: "семь семи", 8: "восемь восьми",
        9: "девять девяти", 10: "десять десяти", 11: "одиннадцать одиннадцати", 12: "двенадцать двенадцати",
        13: "тринадцать тринадцати", 14: "четырнадцать четырнадцати", 15: "пятнадцать пятнадцати",
        16: "шестнадцать шестнадцати", 17: "семнадцать семнадцати", 18: "восемнадцать восемнадцати",
        19: "девятнадцать девятнадцати", 20: "двадцать двадцати", 30: "тридцать тридцати",
        40: "сорок сорока", 50: "пятьдесят пятидесяти", 60: "шестьдесят шестидесяти",
        70: "семьдесят семидесяти", 80: "восемьдесят восьмидесяти", 90: "девяносто девяноста",
        100: "сто ста", 200: "двести двухсот", 300: "триста трехсот", 400: "четыреста четырехсот",
        500: "пятьсот пятисот", 600: "шестьсот шестисот", 700: "семьсот семисот",
        800: "восемьсот восьмисот", 900: "девятьсот девятисот"}
    for value, names in forms.items():
        for name in names.split():
            out[name] = value
    return out


_SMALL_NUMBERS = _small_numbers()


def ru_number(words: list[str]) -> int | None:
    total = cur = 0
    seen = False
    for w in words:
        short = re.fullmatch(r"(\d+)[кk]", w)
        if short:
            total += int(short.group(1)) * 1000
            seen = True
        elif w.isdigit():
            cur += int(w)
            seen = True
        elif w in _SMALL_NUMBERS:
            cur += _SMALL_NUMBERS[w]
            seen = True
        elif w.startswith("тысяч") or w == "тыс":
            total += (cur or 1) * 1000
            cur = 0
            seen = True
        elif w.startswith("миллион") or w == "млн":
            total += (cur or 1) * 1_000_000
            cur = 0
            seen = True
        else:
            return None
    return total + cur if seen else None


_LIMIT_CUES = ("больше", "более", "максим", "макс", "лимит", "потол", "выше", "до", "ограни", "предел")
_INPUT_WORDS = ("вход", "входн", "входящ", "ввод")
_OUTPUT_WORDS = ("выход", "выходн", "ответ", "вывод")


def named_token_limits(text: str) -> dict[str, int]:
    words = _words(text)
    out: dict[str, int] = {}
    for i, w in enumerate(words):
        if not w.startswith("токен"):
            continue
        start = i
        while start > 0 and (words[start - 1].isdigit() or words[start - 1] in _SMALL_NUMBERS
                             or words[start - 1].startswith(("тысяч", "миллион"))
                             or words[start - 1] in ("тыс", "млн")
                             or re.fullmatch(r"\d+[кk]", words[start - 1])):
            start -= 1
        num = ru_number(words[start:i]) if start < i else None
        if not num:
            continue
        window = words[max(0, start - 6):start] + words[i + 1:i + 3]
        if not _has_stem(window, _LIMIT_CUES):
            continue
        inp, outp = _has_stem(window, _INPUT_WORDS), _has_stem(window, _OUTPUT_WORDS)
        if inp == outp:
            continue
        out.setdefault("max_input_tokens" if inp else "max_output_tokens", num)
    return out


_CALL_WORDS = ("запрос", "вызов", "обращен")
_MODEL_WORDS = ("модел", "llm", "ллм", "gpt", "нейрос", "джипити")
_CAP_CUES = ("больше", "более", "максим", "макс", "лимит", "до", "ограни", "предел")


def named_llm_limit(text: str) -> dict[str, int]:
    words = _words(text)
    out: dict[str, int] = {}
    for i, w in enumerate(words):
        if not w.startswith(_CALL_WORDS) or not _has_stem(words[max(0, i - 4):i + 6], _MODEL_WORDS):
            continue
        for j in range(max(0, i - 4), min(len(words), i + 9)):
            if not words[j].startswith(_CAP_CUES):
                continue
            k = j + 1
            while k < len(words) and (words[k].isdigit() or words[k] in _SMALL_NUMBERS):
                k += 1
            num = ru_number(words[j + 1:k]) if k > j + 1 else None
            if num:
                out.setdefault("max_llm_requests", num)
                break
    return out


_HERO_RE = ((re.compile(r"\bбез\s+(?:главн\w+\s+)?геро\w*|\bбез\s+персонаж\w*"), "no_hero"),
            (re.compile(r"\bс\s+(?:главн\w+\s+)?геро\w*|\bгеро\w*\s+(?:нужен|включ|есть)"), "hero"))


def named_hero_mode(text: str) -> str:
    low = _norm(text)
    for pat, mode in _HERO_RE:
        if pat.search(low):
            return mode
    return ""


_CEILING_ACTIONS = frozenset({"step.run_step", "project.resume", "project.continue_pipeline", "hitl.regenerate",
                              "massfactory.start", "node.run_step"})
_CREDITS_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(?:кредит|кр(?![а-я]))")
_AFTER_NODE_RE = re.compile(r"(?:после|сразу\s+за|вслед\s+за|следом\s+за)\s+(n_[a-z0-9_]+)")


def apply_request_values(action_id: str, params: dict, text: str) -> dict:
    out = dict(params)
    if not text:
        return out
    if action_id == "publish.send":
        named = named_platforms(text)
        if named and set(out.get("platforms") or ()) != set(named):
            out["platforms"] = named
    elif action_id == "montage.apply_queue":
        for key, num in named_ops_limits(text).items():
            out[key] = num
    elif action_id == "montage.improve_scene":
        num = named_ops_limits(text).get("max_image_ops")
        if num:
            out["max_image_ops"] = max(1, min(num, MAX_IMPROVE_OPS))
    elif action_id == "node.run_step":
        out.update(named_token_limits(text))
        out.update(named_llm_limit(text))
    elif action_id in ("project.create", "project.patch_settings") and "hero_mode" not in out:
        mode = named_hero_mode(text)
        if mode:
            out["hero_mode"] = mode
    if action_id in _CEILING_ACTIONS and "ceiling_credits" not in out:
        credits = _CREDITS_RE.findall(_norm(text))
        if len(set(credits)) == 1:
            out["ceiling_credits"] = float(credits[0].replace(",", "."))
            if out["ceiling_credits"].is_integer():
                out["ceiling_credits"] = int(out["ceiling_credits"])
    if action_id == "group.insert" and not out.get("after"):
        after = _AFTER_NODE_RE.findall(_norm(text))
        if len(set(after)) == 1:
            out["after"] = after[0]
    if "image_resolution" in out and action_id.startswith("montage."):
        named = _IMG_RES_TIGHT.findall(_norm(text))
        if len(set(named)) == 1:
            out["image_resolution"] = f"{named[0]}k"
    return out



_SETTING_KEYS = ("image_generator", "video_generator", "image_resolution", "video_resolution")
_LATIN = {"a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и", "j": "дж",
          "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "кв", "r": "р", "s": "с", "t": "т",
          "u": "у", "v": "в", "w": "в", "x": "кс", "y": "и", "z": "з"}


_GENERIC_PARTS = frozenset({"image", "video", "lite", "fast", "pro", "vip", "ultra", "max", "plus"})


def _translit(word: str) -> str:
    return "".join(_LATIN.get(ch, ch) for ch in word)


def _resolution_named(value: str, low: str) -> bool:
    m = re.fullmatch(r"(\d+)([kp])", str(value))
    if not m:
        return True
    num, unit = m.groups()
    if unit == "k":
        return bool(re.search(rf"(?<![\d.]){num}\s*[kк](?![a-zа-я])", low))
    if num == "1080" and re.search(r"full\s*hd|fhd|фулл|фхд", low):
        return True
    if num == "720" and re.search(r"(?<![a-zа-я])(?:hd|хд)(?![a-zа-я])", low):
        return True
    return bool(re.search(rf"(?<!\d){num}\s*[pр]?(?![\d])", low))


def _generator_named(value: str, text: str) -> bool:
    parts = [t for t in re.split(r"[_\-]", str(value).lower())
             if t and not t.isdigit() and len(t) >= 3 and t not in _GENERIC_PARTS]
    if not parts:
        return True
    words = _words(text)
    for part in parts:
        if any(w.startswith(part) or part.startswith(w) and len(w) >= 3 for w in words):
            return True
        stem = _translit(part)[:3]
        if len(part) >= 4 and any(w.startswith(stem) for w in words if re.search(r"[а-я]", w)):
            return True
    return False


def drop_unnamed_settings(params: dict, text: str) -> tuple[dict, list[str]]:
    out = dict(params)
    low = _norm(text)
    dropped: list[str] = []
    for key in _SETTING_KEYS:
        if key not in out:
            continue
        named = (_resolution_named(out[key], low) if key.endswith("resolution")
                 else _generator_named(out[key], text))
        if not named:
            out.pop(key)
            dropped.append(key)
    return out, dropped


def settings_question(dropped: list[str]) -> tuple[str, list[str]]:
    gen = any(k.endswith("generator") for k in dropped)
    res = any(k.endswith("resolution") for k in dropped)
    if gen and res:
        return ("Что изменить в настройках проекта: генератор или разрешение, для картинок или видео, "
                "и на какое значение?",
                ["Генератор картинок", "Генератор видео", "Разрешение картинок", "Разрешение видео"])
    if gen:
        return ("Какой генератор поставить: для картинок или для видео, и какой именно?",
                ["Генератор картинок", "Генератор видео"])
    return ("На какое разрешение поменять: картинок (1k..4k) или видео (720p, 1080p)?",
            ["Разрешение картинок", "Разрешение видео"])


_CLEAR_ALL_WORDS = ("все", "всю", "всё", "всей", "вся", "целиком", "полност", "очист", "сброс", "обнул",
                    "подчистую", "насовсем")


def queue_clear_question(action_id: str, params: dict, text: str) -> tuple[str, list[str]] | None:
    if action_id != "montage.cancel_queue_ops" or not params.get("clear_all") or params.get("ops"):
        return None
    if _has_stem(_words(text), _CLEAR_ALL_WORDS):
        return None
    return ("Что убрать из очереди правок: всю очередь или правки конкретного кадра?",
            ["Всю очередь", "Правки кадра", "Не убирать"])



_CAPTION_RE = re.compile(r"(?:подпис\w*|caption|описани\w*)\s*[:=\-–—]?\s*[«\"“'](.{1,300}?)[»\"”']")


def named_caption(text: str) -> str:
    m = _CAPTION_RE.search(text or "")
    return m.group(1).strip() if m else ""


def caption_override_note(action_id: str, params: dict, text: str) -> str:
    if action_id != "publish.send":
        return ""
    named = named_caption(text)
    actual = str(params.get("caption") or "")
    if not named or _norm(named) == _norm(actual):
        return ""
    return (f"Подпись «{named}» из запроса не применена: подпись публикации задаёт сервер "
            f"(тема проекта: «{actual}»), её видно в превью дословно.")


_PUBLISH_VERBS = ("опублик", "выложи", "выкла", "запост", "постни", "залей", "залить", "разошли", "размест",
                  "выгруз", "выпуст", "публик", "отправ")
_FILLER_WORDS = frozenset({"пожалуйста", "плиз", "уже", "давай", "ну", "сейчас", "скорее", "же", "теперь",
                           "быстро", "тогда", "ладно", "все", "всё"})
_PLATFORM_ASK = ("Что и куда опубликовать? Назовите площадку (TikTok, YouTube Shorts, Instagram Reels, "
                 "VK Клипы, Likee) или «на все площадки».")


def bare_publish_question(steps: list[dict], text: str) -> tuple[str, list[str]] | None:
    if not any(st.get("action_id") == "publish.send" for st in steps):
        return None
    words = [w for w in _words(text) if w not in _FILLER_WORDS]
    if len(words) != 1 or not words[0].startswith(_PUBLISH_VERBS):
        return None
    return _PLATFORM_ASK, ["TikTok", "YouTube Shorts", "Instagram Reels", "Все площадки"]


_MASS_SCOPE = re.compile(
    r"(?<![а-я])(?:вс(?:е|ех|ей|ю)|кажд\w*)\s+(?:\w+\s+)?(?:нод|хранилищ|проект|групп|ветк)\w*"
    r"|(?<![а-я])(?:в|во|на)\s+(?:вс(?:ех|ей)|кажд\w*)\s+(?:нод|хранилищ|проект|групп)\w*")
_MASS_MIN_STEPS = 2


def mass_destructive_question(steps: list[dict], text: str, registry: Any) -> tuple[str, list[str]] | None:
    if not _MASS_SCOPE.search(_norm(text)):
        return None
    destructive = [st for st in steps
                   if (spec := registry.find(st["action_id"])) is not None and spec.destructive]
    if len(destructive) < _MASS_MIN_STEPS or len({st["action_id"] for st in destructive}) != 1:
        return None
    targets = [str(v) for st in destructive for k, v in st["params"].items()
               if k in ("node_key", "frame_number", "group_id") and v is not None]
    where = f" ({', '.join(dict.fromkeys(targets))})" if targets else ""
    return (f"Это {len(destructive)} необратимых действий сразу{where}. Назовите конкретные ноды или "
            "объекты, которые нужно затронуть, и они будут в плане по одному на подтверждение.",
            ["Назову конкретные", "Отмена"])



_UNDO_RE = re.compile(
    r"(?<![а-я])(?:отмен|откат|откач|отмот|верн|убер|сними)\w*\s+(?:(?:мо[еёюй]\w*|послед\w+|предыдущ\w+|прошл\w+|"
    r"только\s+что\w*)\s+){1,2}(?:действи|изменени|операци|команд|шаг|правк)\w*[\s.!?]*$"
    r"|(?<![а-я])(?:отмен|откат|откач|отмот)\w*(?:\s+(?:это|все|всё|его|назад))?[\s.!?]*$"
    r"|\bundo\b|ctrl\s*\+\s*z")
_UNDO_TARGET = ("очеред", "кадр", "шот", "снимок", "снимк", "версии", "версия", "предложен", "журнал", "hitl",
                "проект", "публикац")
_UNDO_ACTIONS = frozenset({"montage.cancel_queue_ops", "history.rollback", "change.rollback",
                           "montage.swap_slots", "montage.swap_shots", "node.reset_from_step"})


def undo_question(steps: list[dict], text: str) -> tuple[str, list[str]] | None:
    if not any(st.get("action_id") in _UNDO_ACTIONS for st in steps):
        return None
    low = _norm(text)
    if not _UNDO_RE.search(low) or re.search(r"\d", low) or _has_stem(_words(low), _UNDO_TARGET):
        return None
    return ("Что именно отменить? Назовите цель: правку в очереди доски, откат по журналу правок или "
            "предложение изменения.",
            ["Правки в очереди", "Откат по журналу", "Предложение изменения", "Ничего не отменять"])


_VAGUE_DEST = re.compile(
    r"(?<![а-я])(?:в|во|на|по)\s+(?:соцсет\w*|социальн\w*\s+сет\w*|сет[иь]\w*|интернет\w*|паблик\w*|"
    r"мессенджер\w*|медиа)|(?<![а-я])(?:куда[\s-]?(?:нибудь|то)|где[\s-]?(?:нибудь|то))")
_ALL_PLATFORMS = re.compile(r"(?<![а-я])(?:все|всех|всем|всюду|везде|каждую|каждой)\s*(?:\w+\s+)?"
                            r"(?:площадк\w*|платформ\w*|соцсет\w*|сет\w*)|(?<![а-я])(?:всюду|везде)(?![а-я])")


def vague_publish_question(steps: list[dict], text: str) -> tuple[str, list[str]] | None:
    if not any(st.get("action_id") == "publish.send" for st in steps):
        return None
    low = _norm(text)
    if named_platforms(text) or _ALL_PLATFORMS.search(low) or not _VAGUE_DEST.search(low):
        return None
    return _PLATFORM_ASK, ["TikTok", "YouTube Shorts", "Instagram Reels", "Все площадки"]


_CREATE_WORDS = ("созда", "нов", "завед", "сдела", "начн", "начат", "запуст", "заряд")


def create_instead_of_settings(action_id: str, params: dict, text: str, facts: dict | None,
                               offered: Any, registry: Any) -> tuple[str, dict, str] | None:
    if action_id != "project.create" or not facts or facts.get("project_id") is None:
        return None
    if _has_stem(_words(text), _CREATE_WORDS):
        return None
    target = "project.patch_settings"
    if registry.find(target) is None or (offered is not None and target not in offered):
        return None
    out = {k: v for k, v in params.items() if k in ("title", "topic", "hero_mode")}
    if not out:
        return None
    out["project_id"] = facts["project_id"]
    if registry.validate_params(target, out):
        return None
    return target, out, "настройки открытого проекта вместо нового проекта (слов о создании в запросе нет)"



CONFIRM_YES = "Да, подготовить"
CONFIRM_NO = "Нет, другое"
_CONFIRM_RE = re.compile(r"уточнени[ея]\s*:\s*да\s*,?\s*подготов\w*[\s.!]*$")
_DECLINE_RE = re.compile(r"уточнени[ея]\s*:\s*нет\s*,?\s*друг\w*[\s.!]*$")


def guard_confirmed(text: str) -> bool:
    return bool(_CONFIRM_RE.search(_norm(text)))


def guard_declined(text: str) -> bool:
    return bool(_DECLINE_RE.search(_norm(text)))


_PARAM_LABELS = {
    "frame_number": "кадр", "frame_id": "кадр", "frame_numbers": "кадры", "from_frame": "из кадра",
    "to_frame": "в кадр", "a_frame": "кадр", "b_frame": "кадр", "parent_number": "родитель",
    "shot": "шот", "from_shot": "шот", "to_shot": "шот", "node_key": "нода", "step_code": "шаг",
    "name": "вариант", "hitl_id": "запрос", "group_id": "группа", "ceiling_credits": "лимит, кредитов",
    "title": "название", "topic": "тема", "content": "текст", "prompt": "заказ", "platforms": "площадки",
    "image_resolution": "разрешение картинок", "video_resolution": "разрешение видео", "enabled": "включить",
    "disabled": "отключить", "clear_all": "вся очередь", "method": "метод", "kind": "вид", "type": "тип",
    "value": "значение", "field": "поле", "key": "ключ", "provider": "провайдер", "reason": "причина",
    "left_frame_id": "кадр", "right_frame_id": "кадр", "after_frame_id": "после кадра"}
_SKIP_PARAMS = frozenset({"project_id", "source_path", "data_base64", "filename", "image_generator",
                          "video_generator", "max_image_ops", "max_video_ops", "min_llm_requests",
                          "max_llm_requests", "max_input_tokens", "max_output_tokens", "caption",
                          "expected_digest", "rationale"})
_MAX_PARAMS_SHOWN = 4
_VALUE_LIMIT = 40
_QUOTED_PARAMS = ("title", "topic", "content", "prompt", "name")


def _param_text(key: str, value: Any, facts: dict | None) -> str:
    if key.endswith("frame_id") and isinstance(value, int) and not isinstance(value, bool) and facts:
        row = _row_by_id(facts, value)
        if row is not None:
            value = row[0]
    label = _PARAM_LABELS.get(key, key)
    if isinstance(value, bool):
        return label if value else ""
    if isinstance(value, dict):
        return ""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value[:6] if isinstance(v, (str, int, float)))
    text = " ".join(str(value).split())
    if len(text) > _VALUE_LIMIT:
        text = text[:_VALUE_LIMIT - 3] + "..."
    if not text:
        return ""
    return f"{label} «{text}»" if key in _QUOTED_PARAMS else f"{label} {text}"


def describe_step_ru(step: dict, registry: Any, facts: dict | None = None) -> str:
    from app.services.action_layer.planner_guard import sanitize_text
    spec = registry.find(step.get("action_id"))
    if spec is None:
        return "неизвестное действие"
    title = spec.title_ru.strip()
    title = title[:1].lower() + title[1:]
    parts: list[str] = []
    for key, value in (step.get("params") or {}).items():
        if key in _SKIP_PARAMS or value is None:
            continue
        txt = _param_text(key, value, facts)
        if txt:
            parts.append(sanitize_text(txt, 80))
    parts = list(dict.fromkeys(parts))[:_MAX_PARAMS_SHOWN]
    if not parts:
        return title
    if len(parts) == 1 and parts[0].startswith("кадр ") and title.endswith("кадр"):
        return f"{title} {parts[0][5:]}"
    return f"{title} ({'; '.join(parts)})"


def guard_confirm_question(steps: list[dict], registry: Any, facts: dict | None) -> tuple[str, list[str]]:
    said = "; ".join(describe_step_ru(st, registry, facts) for st in steps[:3])
    if len(steps) > 3:
        said += f" и ещё {len(steps) - 3}"
    what = "шаги" if len(steps) > 1 else "шаг"
    question = (f"Вы хотите: {said}? В запросе нет слов об этом действии, поэтому я не готовлю его сам. "
                f"Подтвердите, я подготовлю {what}; выполнение будет отдельным подтверждением.")
    return question[:500], [CONFIRM_YES, CONFIRM_NO]



PROMPT_ACTIONS = frozenset({"prompt.get_master", "prompt.save_master", "prompt.delete_master",
                            "prompt.create_master"})


def _variant_names(step: str) -> list[str]:
    from app.services import prompt_library as pl
    try:
        return list(pl.list_prompts(step))
    except Exception:
        return []


def _real_steps(pl) -> list[str]:
    return [s for s in pl.STEP_FOLDERS if s not in pl.ENRICH_STEP_CODES or s == pl.EXCEL_GPT_UNIFIED_STEP]


def resolve_prompt_target(action_id: str, params: dict, facts: dict | None,
                          text: str) -> tuple[dict, list[str], tuple[str, list[str]] | None]:
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



_UPLOAD_NOTE = "Шаг без source_path, data_base64, filename: файл прикрепит человек, сервер спросит сам."
ACTION_NOTES: dict[str, str] = {
    "montage.move_image": "Перенос картинки между кадрами («отправь/переложи/перекинь/переедет из A в B»; обмен двух слотов местами - swap_slots). from_frame/to_frame - НОМЕРА кадров (кадр 5 = 5), не frame_id из карты кадров. Шот не назван: берётся единственный шот кадра; файлы в обоих шотах исходного кадра и шот не назван - вопрос (шот 1/шот 2), не угадывай.",
    "montage.swap_slots": "Только обмен двух слотов местами («поменяй местами»); перенос одной картинки в другой слот - montage.move_image. Шот не назван: шот 1 у обоих; у обоих по 2 шота с такими файлами - два шага (1-1, 2-2).",
    "montage.swap_shots": "kind не пиши, если не сказано (по умолчанию картинки и видео).",
    "montage.queue_ops": "Постановка в очередь; apply_queue добавь вторым шагом ТОЛЬКО если в запросе сказано "
                         "выполнить/применить/запустить очередь. shot не пиши, если у кадра один шот; "
                         "файлы в обоих шотах и шот не назван - вопрос (шот 1/шот 2/оба шота).",
    "montage.cancel_queue_ops": "ops: операции кадра из queue.ops [кадр,тип,шот]; их нет - вопрос. Вся очередь - clear_all, только при словах «всю/все/целиком/очисти/сбрось/обнули», иначе вопрос. Только убрать: «отработай/выполни/примени очередь» - apply_queue. Отмена без названной цели (ни очереди, ни кадра) - вопрос.",
    "montage.apply_queue": "«Выполни/примени/запусти/отработай очередь» = это действие; «только для кадра N» = frame_numbers. max_*_ops опусти (сервер возьмёт по очереди), кроме названного предела «не больше N».",
    "montage.improve_scene": "max_image_ops опусти. frame_id головы сцены. Только «улучши/прокачай/оживи»; «пересобери/перепиши сцену под запрос X» - generate_scene.",
    "montage.generate_scene": "Пишет текст сцены по заказу: prompt = заказ из запроса. frame_id головы сцены. «Пересобери/перепиши сцену под запрос X» - это действие (картинок нет, improve_scene не нужен).",
    "montage.generate_scene_images": "Рисует картинки уже существующих кадров, сцену не переписывает; "
                                     "scene_prompt опусти.",
    "montage.delete_frame": "Любой кадр («удали кадр N»); delete_child_frame только если сказано "
                            "«дочерний»/«шот сцены».",
    "montage.delete_image": "Файлы в обоих шотах и шот не назван - вопрос.",
    "montage.delete_video": "Файлы в обоих шотах и шот не назван - вопрос.",
    "montage.merge_scenes": "left/right = frame_id голов соседних сцен.",
    "montage.explode_scene": "frame_id головы сцены.",
    "montage.split_scene": "frame_id отделяемого кадра (не головы сцены).",
    "montage.set_coverage": "Крупность/ракурс/движение/свет/стык кадра БЕЗ слов «меню съёмки» - это действие "
                            "(крупность = type coverage_plan); с «меню съёмки» - shot.edit_field. plan ОБЩИЙ|ДАЛЬНИЙ|СРЕДНИЙ|КРУПНЫЙ|ДЕТАЛЬ; angle фронт|3/4|с плеча|сверху|снизу|макро; "
                            "move статика|наезд|отъезд|панорама|следование|ручная; light дневной|ночной|закат|"
                            "рассвет|контровой|холодный верхний|туман. parent_number только для coverage_kind.",
    "project.resume": "ceiling_credits только если человек назвал сумму; иначе опусти, не спрашивай.",
    "project.continue_pipeline": "ceiling_credits только если человек назвал сумму; иначе опусти, не спрашивай. «Пусть дальше идёт само/автоматически» без «продолжи/запусти» - project.set_auto_mode.",
    "publish.send": "caption сервер подставит. platforms только названные (yt_shorts = ютуб/шортс; ig_reels = инстаграм/рилс; vk_clips = вк/клипы); не названы - опусти.",
    "project.set_auto_mode": "enabled=true: включить автопродвижение, false: выключить. Не запускает; continue_pipeline без слов «продолжи/запусти» не добавляй.",
    "project.patch_settings": "Только названные значения: генератор и разрешение не придумывай, нет значения - вопрос. «Кадры в 2k» без «запусти» - настройка, не step.run_step.",
    "step.run_step": "Платный запуск только по «запусти/сгенерируй/прогони»; разрешение без них - project.patch_settings.",
    "montage.set_assemble_params": "Передай только названные настройки. bgm_level: шкала 0..100, доля 0.2 = 20.",
    "montage.set_voiceover_span": "Только «закрепи кусок закадра за сценой»; сменить слова закадра = montage.set_voiceover.",
    "montage.insert_frame": "Без уточнения kind=parent (новая сцена после кадра), voiceover не нужен.",
    "montage.set_scene_chain": "action = цепочка действий из запроса («А → Б»).",
    "montage.set_anchors": "anchors = [{anchor}] по названным якорям; change и main не нужны.",
    "montage.align_audio": "method не спрашивай (по умолчанию nemo_auto).",
    "group.insert": "after не нужен (группа в конец канваса).",
    "node.run_step": "Лимиты опусти: сервер подставит из расчёта по проекту (названный «не больше N» - тоже оставь, сервер возьмёт из слов).",
    "node.reset_from_step": "step_code: названный шаг; «текущий» = defaults.step_code.",
    "media.release_unknown": "key (clip_005, frame_012, predmet3, music) и provider (kie|outsee) назови из запроса; "
                             "reason = слова человека о сверке кабинета провайдера, не выдумывай; нет причины, "
                             "провайдера или ключа - вопрос.",
    "change.apply": "«предложение N» = proposals n=N: его id и digest.",
    "change.rollback": "target и v из versions (свежая цель нужного вида); нет данных - вопрос.",
    "change.get": "target из versions (свежая цель нужного вида) или proposal_id.",
    "change.propose": "Нужно содержимое (candidate); без него - вопрос.",
    "prompt.get_master": "step_code: из слов запроса («шага script»), иначе где есть названный вариант; "
                         "неясно - вопрос. name: выбранный в проекте (prompts.<шаг>.sel) или default.",
    "prompt.save_master": "step_code как у get_master; content = новый текст из запроса. «Замени/поменяй главный (мастер) промт шага X»; имя варианта не названо - name опусти.",
    "prompt.create_master": "step_code из слов запроса; name и content из запроса, иначе вопрос.",
    "prompt.delete_master": "step_code из слов запроса или где есть вариант; выбранный в проекте и default "
                            "не удаляются. «Вариант W из библиотеки шага X выкинь/удали» - это действие, не group.delete.",
    "shot.edit_cell_voiceover": "parent_uuid из frame_uuids.",
    "shot.edit_field": "frame_uuid из frame_uuids. Только если в запросе есть «меню съёмки»/«ячейка»: тогда "
                       "движение/крупность/стык = field move/size/stitch. Без этих слов - montage.set_coverage. "
                       "Убрать одного персонажа/предмет по id - montage.unlink_ref.",
    "shot.add_cell": "Позицию не спрашивай: без before_index ячейка идёт в конец.",
    "group.get": "«Открой описание/состав группы X» = group_id X (group.list не нужен).",
    "kill_switch.engage": "Только аварийный стоп всех платных вызовов (слова «аварийный», «рубильник», "
                          "«экстренно», причина инцидента). Просто «останови/стоп/хватит» = project.stop.",
    "media.get_recovery_assessment": "Состояние задач провайдера медиа-ноды (node_type: videos|music|hero|...). "
                                     "Про Outsee и галерею - montage.recover_status / recover_outsee.",
    "montage.recover_status": "Статус забора из галереи Outsee: без параметров. «Забери/вытащи» из галереи - recover_outsee.",
    "hitl.regenerate": "hitl_id = номер запроса из просьбы; ceiling_credits = названная сумма, иначе опусти. "
                       "Ничего больше не спрашивай.",
    "project.create": "Только при «создай/новый проект»; title из просьбы (в кавычках или после «проект»); hero_mode: «с героем» = hero, «без героя» = no_hero, иначе опусти. Название и тема открытого проекта без этих слов - patch_settings.",
    "montage.get_cost_basis": "Модель оплаты и очередь правок одним вызовом. Просили и доску, и цену очереди - два шага; только цену - get_board не нужен. «Идёт ли очередь» - apply_status.",
    "node.get_run_cost_basis": "Только цена запуска одной ноды (node_key); цена очереди или проекта - montage.get_cost_basis.",
    "montage.apply_status": "Идёт ли очередь правок или уже закончилась: без параметров. Сборка/монтаж ролика - montage_status.",
    "montage.montage_status": "Идёт ли сборка (монтаж) ролика или уже закончилась: без параметров.",
    "montage.unlink_ref": "«Убери из ячейки/кадра персонажа или предмет <id>» (hero_1, item_2): kind character|item, ref_id из запроса; shot.edit_field не нужен.",
    "montage.delete_manual_ref": "«Ручной реф <id> у кадра N»: kind всегда manual, тип не спрашивай.",
    "montage.link_ref": "file и frame_number из запроса; list_ref_assets не нужен.",
    "montage.suggest_scene_variants": "kind: формат/шаблон сцены = template, действие = action, якоря = anchors; count = названное число. Не переспрашивай.",
    "node.set_config": "Меняются только переданные поля, остальные опусти. Автоисправление = checkFix, режим проверки = checkMode.",
    "group.list": "«Готовые/сохранённые наборы нод, шаблоны, заготовки» = каталог групп; названная группа - group.get.",
    "node.clear_check_agent": "node_key только названная или выбранная нода; не угадывай из списка нод - вопрос, какая нода.",
}
for _aid in UPLOAD_ACTIONS:
    ACTION_NOTES[_aid] = _UPLOAD_NOTE


def notes_for(offered) -> dict[str, str]:
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



_DELETE = ("удал", "убер", "убра", "сотр", "снес", "снос", "выкин", "очист", "delete", "remove",
           "стер", "стир", "выбрас", "выброс", "вычист", "затр",
           "нафиг", "нахер", "нахрен", "долой", "корзин", "почист", "подчист",
           "дроп", "избав", "выпил", "ликвидир", "прибер", "прибр", "уничтож", "вымар",
           "вычерк", "вырез", "выреж", "утиль", "утилиз", "оставляй", "зачист")

GUARD_HINTS: dict[str, tuple[str, ...]] = {
    "montage.run_montage": ("смонтир", "монтир", "монтаж", "собер", "собери", "собра", "сборк",
                            "рендер", "финальн", "собират", "пересобер", "пересобр"),
    "montage.align_audio": ("разбер", "разбор", "таймкод", "выровн", "синхрон", "asr", "nemo",
                            "align", "разметь", "подгон", "подстро", "привяз", "пауз"),
    "montage.apply_queue": ("примен", "apply", "запуст", "сгенер", "генерир", "генерац", "выполн", "прогон",
                            "прогна", "отработ", "обработ", "пусти", "стартуй", "жми", "нажм", "гони", "погон"),
    "montage.generate_scene_images": ("сгенер", "генерир", "нарису", "созда", "сдела", "отрис", "дорис",
                                      "изобрази", "накид", "намал", "набросай", "нарис", "перегенер",
                                      "перерис", "пересозд"),
    "montage.generate_scene": ("сгенер", "генерир", "придум", "пересобер", "сочин", "созда",
                               "напиш", "пересоздай", "перепиш", "перепис", "сочини", "заказ"),
    "montage.improve_scene": ("улучш", "лучше", "живее", "динамик", "динамич", "доработ", "усиль", "прокач",
                              "оживи", "оживл", "детализ", "обогат", "отполир"),
    "montage.suggest_scene_variants": ("вариант", "подбер", "предлож", "иде", "подскаж", "накид", "наброс",
                                       "формат", "шаблон"),
    "montage.recover_outsee": ("outsee", "аутси", "галере", "забер", "восстанов", "достань", "достан", "вытащ"),
    "montage.merge_scenes": ("объедин", "склей", "слей", "слить", "соедин", "склеи", "слит", "сплав"),
    "montage.split_scene": ("раздел", "отдел", "раздро", "разбей", "разбить", "раздр", "ножниц",
                            "отреж", "разреж", "режь", "оторв", "вынес", "вынь", "выдел", "отсоедин",
                            "отдельн", "правее", "правей", "начат", "начин", "начн"),
    "montage.explode_scene": ("раздел", "разбей", "разбить", "кажд", "отдельн", "раздро", "разнес",
                              "разлож", "одиночн", "дроби"),
    "montage.delete_frame": _DELETE, "montage.delete_child_frame": _DELETE,
    "montage.delete_image": _DELETE, "montage.delete_video": _DELETE,
    "montage.delete_manual_ref": _DELETE, "group.delete": _DELETE,
    "prompt.delete_master": _DELETE, "node.clear_check_agent": _DELETE + ("сброс", "сбрас"),
    "node.run_step": ("запуст", "прогон", "прогна", "старт", "run", "выполн", "гони", "пуск", "жми", "нажм",
                      "погон"),
    "node.reset_from_step": ("сброс", "сбрас", "обнул", "заново", "перезапуст", "reset", "откат", "откач",
                             "отмот", "вернись", "верни", "пересчит", "нуля", "сотр", "стер", "очист", "удал",
                             "зачист", "вычист", "обнул"),
    "project.continue_pipeline": ("продолж", "дальш", "далее", "конвейер", "пайплайн", "запуст"),
    "project.resume": ("возобнов", "продолж", "сними", "снять", "resume", "разблок", "выход", "выйд", "снят"),
    "project.reload_xlsx": ("xlsx", "эксел", "excel", "таблиц", "перечит", "перезагруз", "обнов"),
    "prompt.save_master": ("сохран", "перепиш", "измен", "правк", "обнов", "замен", "запиш",
                           "поправ", "исправ", "отредакт", "помен", "смен", "переде", "напиш", "перезапис", "перезапиш"),
    "media.resume_poll": ("докач", "возобнов", "дожд", "скач", "подтян", "доведи", "забер", "достан",
                          "продолж", "опрос", "дотян", "заверш"),
    "media.get_recovery_assessment": ("провер", "задач", "kie", "киэ", "кие", "состоян", "статус", "восстанов",
                                      "зависл", "завис", "докач", "возобнов", "узна", "посмотр", "покаж", "оцен", "стряс", "случил",
                                      "вытян", "вытащ", "реальн", "возможн"),
    "media.release_unknown": ("блок", "разблок", "неизвестн", "исход", "release", "unknown", "отпуст",
                              "освобод", "ключ", "списан"),
    "media.list_unknown_outcomes": ("блок", "заблок", "неизвестн", "неясн", "неопредел", "исход", "потерян",
                                    "застр", "unknown"),
    "change.apply": ("примен", "apply", "внес", "внеси", "примени", "прмен", "накат", "активир"),
    "change.rollback": ("откат", "откач", "верни", "вернуть", "назад", "отмен", "прежн"),
    "project.set_auto_mode": ("автопродвиж", "автореж", "автомат", "auto", "само", "самост", "автопил",
                              "автоход", "автозапуск", "автопрогон"),
    "step.run_step": ("запуст", "прогон", "прогна", "старт", "run", "выполн", "гони", "пуск", "сгенер",
                      "генерир", "запусти", "погон"),
    "hitl.approve": ("одобр", "подтверд", "принят", "принять", "утверд", "согласу", "approve", "аппрув", "апрув",
                    "приним", "прими", "принял", "устраив", "годит", "положит", "позитив"),
    "hitl.reject": ("отклон", "забрак", "отказ", "reject", "завал", "зареж", "отшей", "бракуй", "подход",
                    "нрав", "негодн", "отрицат", "негатив"),
    "hitl.regenerate": ("перегенер", "заново", "переделай", "переделат", "повтор", "regenerate", "пересозд",
                        "пересдел", "созда", "сгенер"),
    "history.rollback": ("откат", "откач", "верни", "вернуть", "назад", "отмен", "прежн", "rollback",
                         "отмот", "возвр", "снимк"),
    "massfactory.start": ("фабрик", "массов", "серию", "серия", "партию", "партия", "запуст", "старт"),
    "project.upload_xlsx": ("xlsx", "эксел", "excel", "таблиц", "загруз", "залей", "залить", "upload",
                            "замен", "подмен", "использ", "приложен"),
    "hero.replace_image": ("загруз", "залей", "залить", "upload", "замен", "подмен", "положи", "прикреп",
                           "использ", "приложен", "постав", "закин", "помен", "подстав", "смен", "обнов", "аттач"),
    "storage.clear_files": _DELETE + ("сброс", "сбрас"),
    "publish.send": ("опублик", "публикац", "публик", "выложи", "выкла", "запост", "постни", "publish",
                     "разошли", "отправ", "залей", "размест", "выгруз", "выпуст", "релиз"),
    "montage.set_anchors": ("якор", "anchor", "заякор"),
    "montage.set_scene_chain": ("цепоч", "последовательн", "разбер", "действи"),
}
_UPLOAD_HINT = ("загруз", "загруж", "upload", "залей", "залить", "залив", "прикреп", "подмен", "замен", "положи",
                "добав", "постав", "закин", "кинь", "брос", "помест", "встав", "использ", "прилож",
                "вложен", "вложи", "возьм", "подгруз", "закач", "держи", "скин", "засун", "помен", "подстав", "смен",
                "аттач", "прикрепл")
for _aid in UPLOAD_ACTIONS:
    GUARD_HINTS.setdefault(_aid, _UPLOAD_HINT)


def is_guarded(spec: Any) -> bool:
    return spec.id in GUARD_HINTS


def unguarded_risky(registry: Any) -> list[str]:
    return [s.id for s in registry.list()
            if (s.risk in ("paid", "destructive") or s.is_job) and s.id not in GUARD_HINTS]


_TYPO_EQUAL_MIN = 5


def _typo_stem(word: str, stem: str) -> bool:
    n = len(stem)
    if n < 4 or len(word) < n or (len(word) == n and n < _TYPO_EQUAL_MIN) or word[0] != stem[0]:
        return False
    head = word[:n]
    return sum(1 for a, b in zip(head, stem) if a != b) == 1


def _has_stem_or_typo(words: list[str], stems: tuple[str, ...]) -> bool:
    return _has_stem(words, stems) or any(_typo_stem(w, s) for w in words for s in stems)


_READ_WORDS = ("покаж", "посмотр", "открой", "прочит", "выведи", "скаж", "какой", "какая", "какие",
               "есть", "сколько", "статус", "провер", "найди", "список")


_NOT_WORDS = frozenset({"не", "нет", "ни"})
_AGREE_STEMS = ("приним", "принят", "принял", "прими", "одобр", "утверд", "подтверд", "согласу", "согласен",
                "согласн", "устраив", "годит", "годен", "подходит", "подойд")
_FILE_SOURCE = re.compile(r"(?<![а-я])(?:из|с|от)\s+(?:файл|компьютер|диск|пк|флешк)\w*|(?<![а-я])файл(?:ом|а|у)(?![а-я])")


def _negated_words(words: list[str]) -> list[str]:
    return [w for prev, w in zip(words, words[1:]) if prev in _NOT_WORDS]


def request_hints_action(text: str, spec: Any) -> bool:
    words = _words(text)
    if spec.id == "hitl.approve":
        words = [w for i, w in enumerate(words) if i == 0 or words[i - 1] not in _NOT_WORDS]
    elif spec.id == "hitl.reject" and _has_stem(_negated_words(words), _AGREE_STEMS):
        return True
    if _has_stem_or_typo(words, tuple(GUARD_HINTS.get(spec.id) or ())):
        return True
    if spec.id not in UPLOAD_ACTIONS:
        return False
    nouns = next((n for aid, n, _ex in _UPLOAD_NOUNS if aid == spec.id), ())
    if _has_stem(words, nouns) and _FILE_SOURCE.search(_norm(text)) and not _has_stem(words, _READ_WORDS):
        return True
    if _has_stem(words, _DEICTIC_OBJECT):
        return bool(_PLACE_TARGET.search(_norm(text))) or _has_stem(words, nouns)
    return (_has_stem(words, nouns) and bool(_PLACE_TARGET.search(_norm(text)))
            and not _has_stem(words, _READ_WORDS))



_EMERGENCY = re.compile(r"аварийн|рубильник|\bkill\b|экстренн|авари|стоп-?кран|утечк|взлом|компромет"
                        r"|все\s+платн|платн\w+\s+(?:вызов|запрос|трат|генерац)|сжига|двойн\w+\s+списан"
                        r"|красн\w*\s+кнопк|(?:бюджет|деньг\w*)\s+теч|теч\w+\s+(?:бюджет|деньг\w*)")
_MENU_WORDS = re.compile(r"меню|съем|ячейк")
_EDIT_FIELD_TO_COVERAGE = {"size": "coverage_plan", "move": "coverage_move", "stitch": "coverage_stitch"}
_RUN_WORDS = re.compile(r"запуст|стартуй|старт|прогон|прогна|гони|пуск|выполн|сгенер|рендер|погнал|жми|нажм|сейчас")
_IMG_RES_TIGHT = re.compile(r"(?<![\d.])([1-4])[kк](?![a-zа-я0-9])")
_IMG_RES = re.compile(r"(?<![\d.])([1-4])\s*[kк](?![a-zа-я])")
_VID_RES = re.compile(r"(?<!\d)(720|1080)\s*[pр]?(?!\d)")
_RUN_KEYS = frozenset({"project_id", "step_code", "image_resolution", "video_resolution",
                       "image_generator", "video_generator"})


def pick_similar_action(action_id: str, params: dict, text: str, facts: dict | None,
                        offered: Any, registry: Any) -> tuple[str, dict, str] | None:
    low = _norm(text)
    if action_id == "step.run_step" and params.get("step_code") in ("img", "video")             and set(params) <= _RUN_KEYS and not _RUN_WORDS.search(low):
        image = params["step_code"] == "img"
        m = (_IMG_RES if image else _VID_RES).search(low)
        target = "project.patch_settings"
        if m and registry.find(target) is not None and (offered is None or target in offered):
            key, val = ("image_resolution", f"{m.group(1)}k") if image else ("video_resolution", f"{m.group(1)}p")
            out = {key: val}
            if "project_id" in params:
                out["project_id"] = params["project_id"]
            if not registry.validate_params(target, {"project_id": 1, **out}):
                return target, out, f"настройка проекта ({key}={val}) вместо запуска шага (слов о запуске нет)"
    if action_id == "kill_switch.engage" and not _EMERGENCY.search(low):
        stop = "project.stop"
        if registry.find(stop) is not None and (offered is None or stop in offered):
            out = {"project_id": params["project_id"]} if "project_id" in params else {}
            return stop, out, "остановка проекта вместо аварийного стопа (слов об аварии в запросе нет)"
    if action_id == "shot.edit_field" and not _MENU_WORDS.search(low) and facts:
        ctype = _EDIT_FIELD_TO_COVERAGE.get(str(params.get("field")))
        target = "montage.set_coverage"
        number = next((int(n) for n, u in (facts.get("frame_uuids") or {}).items()
                       if u == params.get("frame_uuid")), None)
        if (ctype and number is not None and registry.find(target) is not None
                and (offered is None or target in offered)):
            out = {"frame_number": number, "type": ctype, "value": params.get("value", "")}
            if "project_id" in params:
                out["project_id"] = params["project_id"]
            return target, out, f"покрытие кадра {number} вместо поля меню съёмки (слов «меню съёмки» нет)"
    return create_instead_of_settings(action_id, params, text, facts, offered, registry)


_SHOW_WORDS = ("покаж", "посмотр", "открой", "открыт", "выведи", "вывед", "прочит", "глян", "взгляни", "загляни",
               "какие", "какой", "список", "дай")
_COMPANIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("project.continue_pipeline", "project.set_auto_mode",
     ("продолж", "запуст", "конвейер", "пайплайн", "возобнов", "стартуй", "поехали", "жми", "нажм")),
    ("montage.list_ref_assets", "montage.link_ref", _SHOW_WORDS),
    ("montage.get_board", "step.get_run_cost_basis", _SHOW_WORDS),
    ("montage.get_board", "node.get_run_cost_basis", _SHOW_WORDS),
    ("project.reload_xlsx", "project.upload_xlsx", ("перечит", "перезагруз", "reload", "импорт", "прочита")),
)


def _companion_drops(steps: list[dict], text: str) -> set[int]:
    words = _words(text)
    out: set[int] = set()
    for extra, base, wanted in _COMPANIONS:
        if any(st["action_id"] == base for st in steps) and not _has_stem(words, wanted):
            out |= {i for i, st in enumerate(steps) if st["action_id"] == extra}
    return out


def unhinted_titles(steps: list[dict], text: str, registry: Any) -> list[str]:
    out: list[str] = []
    for st in steps:
        spec = registry.find(st["action_id"])
        if spec is not None and is_guarded(spec) and not request_hints_action(text, spec):
            out.append(spec.title_ru)
    return out


def drop_unrequested(steps: list[dict], text: str, registry: Any,
                     confirmed: bool = False) -> tuple[list[dict], list[str]]:
    dropped: set[int] = set()
    for i, st in enumerate(steps):
        spec = registry.find(st["action_id"])
        if spec is None or not is_guarded(spec):
            continue
        if not confirmed and not request_hints_action(text, spec):
            dropped.add(i)
    companions = _companion_drops(steps, text) - dropped
    if not (dropped or companions):
        return steps, []
    changed = True
    while changed:
        changed = False
        for i, st in enumerate(steps):
            if i not in dropped and i not in companions and any(d in dropped for d in st["depends_on"]):
                dropped.add(i)
                changed = True
    dropped |= companions
    remap: dict[int, int] = {}
    kept: list[dict] = []
    for i, st in enumerate(steps):
        if i in dropped:
            continue
        remap[i] = len(kept)
        kept.append({**st, "depends_on": sorted({remap[d] for d in st["depends_on"] if d in remap})})
    names = [registry.find(steps[i]["action_id"]).title_ru for i in sorted(dropped)]
    return kept, names



_FORMATS = {
    "montage.upload_image": "PNG", "montage.upload_video": "MP4", "montage.upload_voice": "аудио",
    "montage.upload_music": "аудио", "montage.upload_ref": "картинка",
    "node.upload_file": "xlsx, txt или md", "node.upload_check_agent": "txt или md",
    "project.upload_xlsx": "xlsx", "hero.replace_image": "картинка (png, jpg, webp)",
    "storage.upload_file": "xlsx, txt, md или json"}


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
    if action_id in ("node.upload_file", "storage.upload_file"):
        return f"нода {p.get('node_key', '?')}"
    if action_id == "project.upload_xlsx":
        return "таблица проекта (project.xlsx)"
    if action_id == "hero.replace_image":
        return "картинка героя проекта"
    return f"агент проверки ноды {p.get('node_key', '?')}"


def upload_clarification(action_id: str, params: dict, title: str) -> dict:
    clean = {k: v for k, v in params.items() if k not in FILE_PARAMS and k != "project_id"}
    question = (f"Прикрепите файл кнопкой загрузки. Он нужен для действия «{title}» "
                f"({_target_text(action_id, clean)}; формат: {_FORMATS.get(action_id, 'файл')}).")
    return {"question": question[:500], "options": ["Прикрепить файл"],
            "attachment": {"action_id": action_id, "params": clean,
                           "formats": _FORMATS.get(action_id, "файл")}}


_UPLOAD_INTENT = ("загруз", "загруж", "залей", "залить", "залив", "upload", "прикреп", "подгруз", "положи",
                  "подмен", "закач")
_NODE_WORDS = ("нод", "n_", "хранилищ")
_UPLOAD_NOUNS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("montage.upload_ref", ("реф", "референс"), ()),
    ("node.upload_check_agent", ("агент",), ()),
    ("storage.upload_file", ("хранилищ",), ()),
    ("hero.replace_image", ("геро", "портрет", "персонаж"), ()),
    ("montage.upload_voice", ("озвуч", "голос", "аудио", "voice", "диктор"), ()),
    ("montage.upload_music", ("музык", "bgm", "трек"), ()),
    ("montage.upload_video", ("видео", "клип", "mp4"), ()),
    ("montage.upload_image", ("картин", "изображ", "фото", "png", "image", "рисун"), ()),
    ("project.upload_xlsx", ("excel", "эксел", "xlsx", "таблиц"), _NODE_WORDS),
    ("node.upload_file", ("excel", "эксел", "xlsx", "файл", "таблиц"), ()),
)


def upload_intent_action(text: str, offered: frozenset[str] | set[str]) -> str | None:
    words = _words(text)
    if not _has_stem(words, _UPLOAD_INTENT):
        return None
    for aid, nouns, excluded in _UPLOAD_NOUNS:
        if aid in offered and _has_stem(words, nouns) and not _has_stem(words, excluded):
            return aid
    return None


_DEICTIC_OBJECT = ("это", "эту", "этот", "этим", "эти", "вот")
_PLACE_VERBS = _UPLOAD_INTENT + ("постав", "закин", "кинь", "вставь", "помест", "брос", "засуни")
_PLACE_TARGET = re.compile(r"\b(?:в|во|на|для)\s+(?:кадр|слот|шот|нод|втор|перв)\w*")


def deictic_file_request(text: str) -> str:
    norm = _norm(text)
    words = _words(norm)
    if not (_has_stem(words, _DEICTIC_OBJECT) and _has_stem(words, _PLACE_VERBS)
            and _PLACE_TARGET.search(norm)):
        return ""
    if any(_has_stem(words, nouns) for aid, nouns, _ex in _UPLOAD_NOUNS if aid != "node.upload_file"):
        return ""
    m = re.search(r"(?:кадр\w*|слот\w*|шот\w*)\s*(?:№|#|номер\s*)?(\d+)", norm)
    target = f" в кадр {m.group(1)}" if m else ""
    return (f"Какой файл поставить{target}? Прикрепите файл кнопкой загрузки (картинка, видео или аудио) "
            "и повторите запрос.")


def upload_params_from_text(action_id: str, text: str) -> dict:
    p: dict = {}
    m = re.search(r"(?:кадр\w*|сцен\w*|frame)\s*(?:№|#|номер\s*)?(\d+)", _norm(text))
    if m and action_id in ("montage.upload_image", "montage.upload_video", "montage.upload_ref"):
        p["frame_number"] = int(m.group(1))
    node = re.search(r"\b(n_[\w.\-]+)", text or "")
    if node and action_id.startswith(("node.", "storage.")):
        p["node_key"] = node.group(1)
    return p


def clean_options(raw: Any) -> list[str]:
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



_SAFETY_STEMS = ("удал", "убер", "убра", "сотр", "снес", "снос", "выкин", "очист", "сброс", "сбрас",
                 "обнул", "перезапис", "причин", "подтверд", "согласи", "увер", "точно", "сумм",
                 "сколько", "кредит", "бюджет", "лимит", "стоим", "цен", "оплат", "плат", "баланс",
                 "потрат", "блокир", "разблок", "откат", "откач", "delete", "remove", "reason",
                 "approve", "confirm", "destroy", "budget", "cost")
_DEICTIC = ("этот", "эт", "текущ", "выбран", "выделен", "данн", "нынешн")


def _publish_known(qw: list[str], facts: dict) -> list[dict]:
    server = facts.get("publish_server") or {}
    if _has_stem(qw, ("удал", "убер", "delete", "remove")):
        return []
    found: list[dict] = []
    if server.get("caption") and _has_stem(qw, ("подпис", "описани", "caption")):
        found.append({"param": "caption", "value": "server-side", "source": "default"})
    if server.get("platforms") and _has_stem(qw, ("площадк", "платформ", "platform", "соцсет")):
        found.append({"param": "platforms", "value": "server-side", "source": "default"})
    return found


def resolve_clarification(question: str, request_text: str, facts: dict | None) -> list[dict]:
    if not facts or facts.get("project_id") is None:
        return []
    qw, rw = _words(question), _words(request_text)
    pub = _publish_known(qw, facts)
    if pub:
        return pub
    if _has_stem(qw, _SAFETY_STEMS) or _has_stem(rw, _SAFETY_STEMS):
        return []
    defaults = facts.get("defaults") or {}
    found: list[dict] = []

    def kinds() -> list[str]:
        vid = _has_stem(qw, ("видео", "клип", "video"))
        img = _has_stem(qw, ("картин", "изображ", "фото", "image", "кадр"))
        return ["video"] if vid and not img else ["image"] if img and not vid else ["image", "video"]

    for key, stems in (("generator", ("генератор", "модел", "generator")),
                       ("resolution", ("разрешен", "качеств", "resolution"))):
        if _has_stem(qw, stems):
            for kind in kinds():
                name = f"{kind}_{key}"
                if defaults.get(name):
                    found.append({"param": name, "value": defaults[name], "source": "default"})
    if _has_stem(qw, ("проект", "project")) and _has_stem(qw, ("как", "котор", "which", "what")):
        found.append({"param": "project_id", "value": facts["project_id"], "source": "context"})
    if _has_stem(qw, ("геро", "hero")) and _has_stem(qw, ("режим", "mode")) and not _has_stem(rw, ("геро", "hero")):
        found.append({"param": "hero_mode", "value": "auto", "source": "default"})
    if (_has_stem(qw, ("метод", "method")) and _has_stem(qw, ("аудио", "разбор", "выравн", "таймкод"))):
        found.append({"param": "method", "value": "nemo_auto", "source": "default"})
    selected = facts.get("selected") or {}
    if selected and _has_stem(rw, _DEICTIC) and _has_stem(qw, ("кадр", "сцен", "frame", "scene")):
        found.append({"param": "selected", "value": selected, "source": "context"})
    return found
