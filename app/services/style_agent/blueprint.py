"""Blueprint генерации: цепочка нод Studio + шаблоны промптов из паспорта v2.

Детерминированная основа (``default_chain`` / ``prompt_templates``) строится
из значений паспорта и работает без LLM; агент ``blueprint`` может уточнить
параметры, тексты и предложить новые ноды — ``build_blueprint`` накладывает
его ответ поверх основы (пустые поля не затирают).
"""

from __future__ import annotations

from typing import Any

from app.services.style_agent.schema import passport_value as _pv

# Назначение существующих нод (кратко, для промпта blueprint и отчёта).
NODE_ROLES: dict[str, str] = {
    "topic": "тема/бриф ролика (вход)",
    "storage": "файлы-референсы (вход)",
    "style_extract": "разбор референсов → паспорт стиля (эта нода)",
    "hitl_style": "утверждение паспорта → блоки промптов/пресет",
    "plan": "план ролика: длительность, биты, хук",
    "script": "закадровый текст (сценарист)",
    "split": "разбивка текста на фразы/ячейки = будущие планы",
    "sd_agent": "агенты сцен: персонажи / мир / стиль / камера / действие",
    "sd_assemble": "сборка сцен: шот-лист с камерой и действием",
    "hero": "референс главного героя (картинка)",
    "items": "референсы предметов",
    "excel_gpt": "проверка/обогащение таблицы GPT-агентом (checkMode)",
    "image_prompts": "промты картинок по кадрам",
    "images": "генерация картинок (стартовые кадры планов)",
    "animation_prompts": "промты анимации (движение камеры/объекта, начало→конец)",
    "videos": "генерация клипов (image-to-video)",
    "audio": "озвучка TTS по тексту (темп, голос)",
    "music": "музыкальная подложка (Suno)",
    "sfx_plan": "план звуков по кадрам",
    "sfx_gen": "генерация SFX",
    "assemble": "монтаж: склейки по фразам, субтитры, музыка, SFX",
    "publish": "экспорт/публикация",
    "hitl_hero": "проверка героя", "hitl_images": "проверка картинок", "hitl_videos": "проверка видео", "hitl_final": "проверка финала",
}

# Ноды, которых в Studio нет (кандидаты); blueprint отмечает их new=true.
NEW_NODE_CANDIDATES: dict[str, str] = {
    "shot_plan": "раскадровка: 1 фраза закадра → 1 план (начало/конец состояния, камера, переход, что иллюстрируем)",
    "vo_sync_edit": "монтаж по фразам: cut на старте фразы с измеренным смещением, jump-zoom на повторе",
    "style_check": "сверка готового ролика с паспортом (тот же экстрактор → diff)",
}


def node_catalog_for_prompt() -> list[dict[str, str]]:
    try:
        from app.orchestrator.node_registry import WORK_NODES

        work = list(WORK_NODES)
    except Exception:  # noqa: BLE001
        work = [k for k in NODE_ROLES if k not in ("topic", "storage", "style_extract", "hitl_style", "excel_gpt") and not k.startswith("hitl_")]
    out = [{"type": t, "role": NODE_ROLES.get(t, "")} for t in ("topic", "storage", "style_extract", "hitl_style", *work, "excel_gpt")]
    out += [{"type": t, "role": r, "new": "true"} for t, r in NEW_NODE_CANDIDATES.items()]
    return out


def _f(passport: dict[str, Any], path: str, default: Any = None) -> Any:
    return _pv(passport, path, default)


def _num(passport: dict[str, Any], path: str, default: float | None = None, nd: int = 1) -> float | None:
    v = _f(passport, path)
    try:
        return round(float(v), nd) if v is not None else default
    except (TypeError, ValueError):
        return default


def _lst(passport: dict[str, Any], path: str) -> list[str]:
    v = _f(passport, path)
    return [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])


def _dist_str(d: Any) -> str:
    if not isinstance(d, dict) or not d:
        return ""
    return ", ".join(f"{k.replace('_', ' ')} {int(round(float(v) * 100))}%" for k, v in sorted(d.items(), key=lambda kv: -float(kv[1])))


def _patterns_text(passport: dict[str, Any], kinds: tuple[str, ...] | None = None, limit: int = 8) -> str:
    rows = []
    for p in passport.get("patterns") or []:
        if kinds and str(p.get("kind") or "") not in kinds:
            continue
        name = p.get("name") or "pattern"
        how = p.get("how_to_reproduce") or p.get("description") or ""
        freq = f" ({p.get('frequency')})" if p.get("frequency") else ""
        rows.append(f"- {name}{freq}: {how}")
    return "\n".join(rows[:limit])


def prompt_templates(passport: dict[str, Any]) -> dict[str, str]:
    """Готовые шаблоны промптов для нод (RU-инструкции + EN-ядро там, где генератор EN)."""
    mp = passport.get("mapping") or {}
    dur = _num(passport, "format.duration_s", 60, 0)
    wpm = _num(passport, "voice.pace_wpm", 170, 0)
    wps = _num(passport, "script.words_per_sentence_median", 12, 0)
    bps = _num(passport, "script.beats_per_sec", 1.0, 2)
    spm = _num(passport, "script.sentences_per_min", 16, 0)
    shot_len = _num(passport, "motion.shot_len_s", 2.0, 1)
    shot_p10 = _num(passport, "motion.shot_len_p10_s", 0.8, 1)
    shot_p90 = _num(passport, "motion.shot_len_p90_s", 4.0, 1)
    cut_off = _num(passport, "linkage.cut_offset_ms_median", 0, 0)
    lps = _num(passport, "linkage.lines_per_shot_median", 1, 1)
    wpl = _num(passport, "linkage.words_per_line_median", 3, 0)
    structure = _f(passport, "script.structure") or _f(passport, "narrative.structure") or "hook → setup → escalation → payoff"
    hook_type = _f(passport, "script.hook_type") or _f(passport, "narrative.hook_type") or ""
    segs = _f(passport, "script.segments") or _f(passport, "narrative.segments") or []
    seg_txt = "; ".join(f"{s.get('name')}: {s.get('function') or s.get('summary') or ''}".strip(" :") for s in segs if isinstance(s, dict)) if isinstance(segs, list) else str(segs)
    templates = _lst(passport, "script.sentence_templates") or _lst(passport, "narrative.sentence_templates")
    rhet = _f(passport, "script.rhetoric_devices") or _f(passport, "narrative.rhetoric_devices") or []
    rhet_txt = "; ".join(
        f"{r.get('name')}" + (f" («{(r.get('examples') or [r.get('example')])[0]}»)" if (r.get("examples") or r.get("example")) else "")
        for r in rhet
        if isinstance(r, dict) and r.get("name")
    )
    forbidden = _lst(passport, "narrative.forbidden_phrases_seed") or list(mp.get("forbidden_phrases_ru") or [])
    address = _f(passport, "narrative.address") or _f(passport, "voice.address") or ""
    hook_ex = _f(passport, "script.example_hook") or _f(passport, "narrative.example_hook") or ""
    payoff_ex = _f(passport, "script.example_payoff") or _f(passport, "narrative.example_payoff") or ""
    words_total = int(round(float(wpm or 170) * float(dur or 60) / 60.0))
    sync = _f(passport, "linkage.pattern") or "each_phrase_new_shot"
    noun_ratio = _num(passport, "linkage.visual_illustrates_noun_ratio", None, 2)
    cam = _dist_str(_f(passport, "motion.camera_vocab"))
    trans = _dist_str(_f(passport, "motion.transitions"))
    construction = _f(passport, "motion.shot_construction") or "каждый план = одно действие: исходное состояние → одно изменение → конечное состояние"
    subj_motion = _f(passport, "motion.subject_motion") or ""
    visual_core = mp.get("visual_core_en") or ""
    lighting = mp.get("lighting_en") or ""
    composition = mp.get("composition_en") or ""
    negative = ", ".join(mp.get("negative_en") or _lst(passport, "visual.forbidden"))
    anim_neg = ", ".join(mp.get("anim_negative_en") or [])
    anatomy = mp.get("character_anatomy_en") or ""
    voice_tone = mp.get("voice_tone_ru") or ""
    voice_hint = _f(passport, "voice.voice_name_hint") or ""
    gender = _f(passport, "voice.gender") or ""
    lang = _f(passport, "voice.language") or "en"
    sub_mode = _f(passport, "motion.subtitles_mode") or "line"
    sub_pos = _f(passport, "motion.subtitles_position") or "lower_third"
    sub_style = _f(passport, "motion.subtitles_style") or "белый полужирный без фона, одна строка"
    music_level = _num(passport, "music.level_vs_voice_db", -6, 1)
    music_core = mp.get("music_suno_core_en") or ""
    sfx_kinds = ", ".join(_lst(passport, "sfx.kinds"))
    sfx_density = _f(passport, "sfx.density") or ""

    script_writer = f"""Ты — сценарист коротких вертикальных роликов в этом стиле. Тема: {{topic}}.
Структура: {structure}.{f' Хук: {hook_type}.' if hook_type else ''}
{f'Сегменты и их функция: {seg_txt}.' if seg_txt else ''}
Длительность ≈ {dur} с → ≈ {words_total} слов при темпе {wpm} слов/мин; ≈ {spm} предложений/мин, медиана {wps} слов в предложении.
Каждое предложение дробится на короткие фразы по {wpl} слова (это будущие субтитры и планы, {bps} фраз/с).
{f'Шаблоны предложений (используй как ритмический каркас): {"; ".join(templates)}.' if templates else ''}
{f'Риторика: {rhet_txt}.' if rhet_txt else ''}
{f'Обращение: {address}.' if address else ''}
{f'Пример хука из референса: «{hook_ex}».' if hook_ex else ''}
{f'Пример развязки из референса: «{payoff_ex}».' if payoff_ex else ''}
Запрещённые обороты: {", ".join(forbidden) if forbidden else "—"}.
Правило картинки: каждая фраза должна содержать конкретное существительное/действие, которое можно показать буквально в одном плане.
Выход: текст по предложениям, без ремарок; затем та же речь, разбитая на фразы (1 фраза = 1 строка)."""

    storyboard = f"""Ты — раскадровщик. Вход: фразы закадра с номерами ({{script}}). Правило синхронизации: {sync}
({lps} фразы на план; склейка на старте фразы со смещением {cut_off:+.0f} мс).
{f'Доля планов, буквально иллюстрирующих существительное фразы: {noun_ratio}.' if noun_ratio is not None else 'Картинка иллюстрирует существительное/действие фразы буквально.'}
Длина плана: медиана {shot_len} с (p10 {shot_p10} с, p90 {shot_p90} с). Построение плана: {construction}.
Словарь камеры: {cam or 'static, slow push-in'}. Переходы: {trans or 'cut'}.
{f'Движение объектов: {subj_motion}.' if subj_motion else ''}
Повторяющиеся приёмы (применяй с указанной частотой):
{_patterns_text(passport, ('camera', 'animation', 'edit', 'vfx', 'subtitle')) or '- —'}
Для каждого плана верни JSON: {{i, phrase_ids, what, framing, camera, start_state, change, end_state, transition_out, illustrates, vfx}}."""

    image_prompt = f"""[STYLE] {visual_core}
[LIGHT] {lighting}
[COMPOSITION] {composition}
{f'[CHARACTER] {anatomy}' if anatomy else ''}
[SHOT] {{shot}} (what + framing + start_state из раскадровки); subject centered, readable silhouette, room at bottom third for subtitles.
[NEGATIVE] {negative or 'text, watermark, logo'}"""

    video_prompt = f"""{{shot}}: start_state → change → end_state из раскадровки, duration {shot_len}s.
Camera: <camera из раскадровки> ({cam or 'static / slow push-in'}). {subj_motion}
Keep style: {visual_core[:300]}
No: {anim_neg or 'morphing, flicker, extra limbs, text changes'}."""

    vo_tts = f"""Голос: {gender or 'нейтральный'}{f', подсказка {voice_hint}' if voice_hint else ''}, язык {lang}. Темп {wpm} слов/мин.
{voice_tone}
Паузы: медиана {_num(passport, 'voice.pause_median_s', 0.3, 2)} с, p90 {_num(passport, 'voice.pause_p90_s', 0.6, 2)} с; без пауз внутри предложения.
TTS stability {_num(passport, 'voice.tts_stability', 0.5, 2)}, similarity {_num(passport, 'voice.tts_similarity_boost', 0.8, 2)}."""

    subtitles = f"""Режим: {sub_mode}; позиция: {sub_pos}; стиль: {sub_style}.
Разбивка: {wpl} слова на строку, 1 строка на экране, смена строки = смена фразы (≈ {bps} строк/с).
Старт/конец строки по таймкодам слов TTS; строка не переживает склейку более чем на 0.3 с."""

    edit = f"""Монтаж по фразам ({sync}): склейка на старте фразы, смещение {cut_off:+.0f} мс; длина плана медиана {shot_len} с (p10 {shot_p10}, p90 {shot_p90}).
Переходы: {trans or 'cut 100%'}. {_patterns_text(passport, ('edit',), 4)}
Музыка: подложка {music_level} dB относительно голоса; SFX: {sfx_kinds or '—'} ({sfx_density or 'sparse'}).
Субтитры: {sub_mode}, {sub_pos}. Хвост после последней фразы ≤ {_num(passport, 'script.tail_silence_s', 1.0, 1)} с."""

    music_sfx = f"""Suno: {music_core or 'instrumental bed, no vocals'}. Уровень {music_level} dB к голосу, без пауз, без вокала.
SFX по плану: {sfx_kinds or 'по действию в кадре'}; плотность {sfx_density or 'sparse'}; акцент на смену состояния в плане (удар/всплеск/щелчок)."""

    return {
        "script_writer": script_writer.strip(),
        "storyboard": storyboard.strip(),
        "image_prompt": image_prompt.strip(),
        "video_prompt": video_prompt.strip(),
        "vo_tts": vo_tts.strip(),
        "subtitles": subtitles.strip(),
        "edit": edit.strip(),
        "music_sfx": music_sfx.strip(),
    }


def default_chain(passport: dict[str, Any]) -> list[dict[str, Any]]:
    """Скелет цепочки нод (существующие типы + new-кандидаты) с параметрами из паспорта."""
    tpl = prompt_templates(passport)
    sid = str(passport.get("style_id") or "style")
    dur = _num(passport, "format.duration_s", 60, 0)
    shot_len = _num(passport, "motion.shot_len_s", 2.0, 1)
    wpl = int(_num(passport, "linkage.words_per_line_median", 3, 0) or 3)
    chain: list[dict[str, Any]] = [
        {"node_type": "topic", "label": "Тема ролика", "inputs": ["бриф"], "outputs": ["topic"], "params": {}, "prompt_template": ""},
        {"node_type": "plan", "label": "План", "inputs": ["topic"], "outputs": ["plan: биты, длительность"],
         "params": {"duration_seconds": dur, "style_profile": sid}, "prompt_template": "Структура и биты — см. script_writer; длительность {dur} с.".replace("{dur}", str(dur))},
        {"node_type": "script", "label": "Сценарий (закадр)", "inputs": ["plan"], "outputs": ["текст по предложениям"],
         "params": {"style_profile": sid, "use_blocks_v2": True}, "prompt_template": tpl["script_writer"]},
        {"node_type": "split", "label": "Разбивка на фразы", "inputs": ["script"], "outputs": ["фразы = ячейки = планы"],
         "params": {"cell_min_chars": max(8, wpl * 4), "cell_max_chars": max(24, wpl * 9)}, "prompt_template": "1 фраза = 1 ячейка; " + str(wpl) + " слова на фразу."},
        {"node_type": "shot_plan", "new": True, "label": "Раскадровка (1 фраза → 1 план)", "inputs": ["фразы"], "outputs": ["шот-лист: what/framing/camera/start→end/transition/illustrates"],
         "params": {"shot_len_s": shot_len, "sync": _f(passport, "linkage.pattern") or "each_phrase_new_shot"}, "prompt_template": tpl["storyboard"],
         "fallback_existing": "sd_agent(camera, action) + sd_assemble"},
        {"node_type": "hero", "label": "Персонажи", "inputs": ["шот-лист"], "outputs": ["референсы героев"], "params": {"style_profile": sid}, "prompt_template": tpl["image_prompt"]},
        {"node_type": "items", "label": "Предметы", "inputs": ["шот-лист"], "outputs": ["референсы предметов"], "params": {"style_profile": sid}, "prompt_template": tpl["image_prompt"]},
        {"node_type": "image_prompts", "label": "Промты картинок", "inputs": ["шот-лист", "герои", "предметы"], "outputs": ["промт стартового кадра на план"],
         "params": {"style_profile": sid, "frame_style": f"style_{sid}"}, "prompt_template": tpl["image_prompt"]},
        {"node_type": "images", "label": "Картинки", "inputs": ["промты"], "outputs": ["PNG стартовых кадров"], "params": {"aspect": _f(passport, "format.aspect") or "9:16"}, "prompt_template": ""},
        {"node_type": "hitl_images", "label": "Проверка картинок", "inputs": ["PNG"], "outputs": ["одобрено"], "params": {}, "prompt_template": ""},
        {"node_type": "animation_prompts", "label": "Промты анимации", "inputs": ["шот-лист", "PNG"], "outputs": ["промт движения на план"],
         "params": {"clip_len_s": shot_len}, "prompt_template": tpl["video_prompt"]},
        {"node_type": "videos", "label": "Видео (image-to-video)", "inputs": ["PNG", "промты анимации"], "outputs": ["клипы"], "params": {"clip_len_s": shot_len}, "prompt_template": ""},
        {"node_type": "audio", "label": "Озвучка (TTS)", "inputs": ["фразы"], "outputs": ["VO + таймкоды слов"],
         "params": {"pace_wpm": _num(passport, "voice.pace_wpm", 170, 0), "voice_hint": _f(passport, "voice.voice_name_hint") or ""}, "prompt_template": tpl["vo_tts"]},
        {"node_type": "music", "label": "Музыка", "inputs": ["plan"], "outputs": ["bed"], "params": {"level_db": _num(passport, "music.level_vs_voice_db", -6, 1)}, "prompt_template": tpl["music_sfx"]},
        {"node_type": "sfx_plan", "label": "План звуков", "inputs": ["шот-лист"], "outputs": ["SFX на план"], "params": {"density": _f(passport, "sfx.density") or "sparse"}, "prompt_template": tpl["music_sfx"]},
        {"node_type": "sfx_gen", "label": "Звуки", "inputs": ["план звуков"], "outputs": ["wav"], "params": {}, "prompt_template": ""},
        {"node_type": "assemble", "label": "Сборка", "inputs": ["клипы", "VO", "музыка", "SFX", "фразы"], "outputs": ["mp4"],
         "params": {
             "subtitles_enabled": True,
             "subtitles_mode": _f(passport, "motion.subtitles_mode") or "line",
             "subtitles_position": _f(passport, "motion.subtitles_position") or "lower_third",
             "words_per_line": wpl,
             "cut_offset_ms": _num(passport, "linkage.cut_offset_ms_median", 0, 0),
             "bgm_level_db": _num(passport, "music.level_vs_voice_db", -6, 1),
         }, "prompt_template": tpl["edit"] + "\n\n" + tpl["subtitles"]},
        {"node_type": "vo_sync_edit", "new": True, "label": "Монтаж по фразам (cut на фразе, jump-zoom)", "inputs": ["клипы", "таймкоды слов"], "outputs": ["timeline"],
         "params": {"cut_offset_ms": _num(passport, "linkage.cut_offset_ms_median", 0, 0)}, "prompt_template": tpl["edit"], "fallback_existing": "assemble (cut по ячейкам)"},
        {"node_type": "style_check", "new": True, "label": "Сверка со стилем", "inputs": ["mp4", "паспорт"], "outputs": ["diff-отчёт"], "params": {}, "prompt_template": "",
         "fallback_existing": "hitl_final"},
        {"node_type": "hitl_final", "label": "Проверка финала", "inputs": ["mp4"], "outputs": ["одобрено"], "params": {}, "prompt_template": ""},
        {"node_type": "publish", "label": "Публикация", "inputs": ["mp4"], "outputs": ["url"], "params": {}, "prompt_template": ""},
    ]
    for n in chain:
        n.setdefault("new", False)
    return chain


def build_blueprint(passport: dict[str, Any], reply: dict[str, Any] | None = None) -> dict[str, Any]:
    """Основа из паспорта + ответ агента blueprint (поверх, не затирая пустым)."""
    chain = default_chain(passport)
    templates = prompt_templates(passport)
    new_nodes = [f"{t} — {r}" for t, r in NEW_NODE_CANDIDATES.items()]
    notes: list[str] = []
    if reply:
        by_type = {str(n.get("node_type")): n for n in chain}
        for n in reply.get("chain") or []:
            if not isinstance(n, dict):
                continue
            # анкета агента: type/role/prompt_hint; внутреннее имя: node_type/label/prompt_template
            t = str(n.get("node_type") or n.get("type") or "").strip()
            if not t:
                continue
            cur = by_type.get(t)
            if cur is None:
                cur = {"node_type": t, "label": n.get("label") or n.get("role") or t, "inputs": [], "outputs": [], "params": {}, "prompt_template": "", "new": bool(n.get("new", t not in NODE_ROLES))}
                chain.append(cur)
                by_type[t] = cur
            for k, aliases in (("label", ("label", "role")), ("inputs", ("inputs",)), ("outputs", ("outputs",)), ("prompt_template", ("prompt_template",))):
                for a in aliases:
                    if n.get(a):
                        cur[k] = n[a]
                        break
            if n.get("prompt_hint"):
                cur["prompt_hint"] = str(n["prompt_hint"])
            if isinstance(n.get("params"), dict) and n["params"]:
                cur["params"] = {**(cur.get("params") or {}), **n["params"]}
        rt = reply.get("prompt_templates")
        if isinstance(rt, dict):
            for k, v in rt.items():
                if isinstance(v, str) and len(v.strip()) >= 20:
                    templates[str(k)] = v.strip()
        if reply.get("new_nodes_needed"):
            new_nodes = [str(x) for x in reply["new_nodes_needed"]]
        notes = [str(x) for x in reply.get("notes") or []]
    return {
        "chain": chain,
        "prompt_templates": templates,
        "new_nodes_needed": new_nodes,
        "notes": notes,
        "source": "llm+default" if reply else "default",
    }
