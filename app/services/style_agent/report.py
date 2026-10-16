"""Человекочитаемый отчёт (Markdown) по паспорту стиля v2.

Рендерится в ноде «Проверка стиля» (вкладка «Отчёт») и отдаётся
``GET /projects/{id}/style-agent/report``. Содержит: сводку стиля, разбор
планов по роликам с таймкодами, связку закадра и картинки, структуру
сценария, приёмы, особенности и blueprint с шаблонами промптов.
"""

from __future__ import annotations

from typing import Any

from app.services.style_agent import schema
from app.services.style_agent.schema import passport_value as _pv


def _tc(t: Any) -> str:
    try:
        t = float(t)
    except (TypeError, ValueError):
        return "—"
    m = int(t // 60)
    return f"{m:02d}:{t - m * 60:05.2f}"


def _s(v: Any, n: int = 160) -> str:
    if v is None:
        return ""
    s = str(v).replace("|", "/").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _val(passport: dict[str, Any], path: str, nd: int | None = None) -> str:
    v = _pv(passport, path)
    if v is None:
        return "—"
    if isinstance(v, float) and nd is not None:
        return f"{v:.{nd}f}"
    if isinstance(v, dict):
        return ", ".join(f"{k} {int(round(float(x) * 100))}%" for k, x in sorted(v.items(), key=lambda kv: -float(kv[1])))
    if isinstance(v, list):
        return ", ".join(str(x) for x in v[:12])
    return str(v)


def _conf(passport: dict[str, Any], path: str) -> str:
    return schema.passport_confidence(passport, path)


def _row(passport: dict[str, Any], label: str, path: str, nd: int | None = None) -> str:
    raw = schema.get_path(passport, path)
    if not isinstance(raw, dict) or "value" not in raw:
        return ""
    note = f" — {_s(raw.get('note'), 90)}" if raw.get("note") else ""
    return f"| {label} | {_s(_val(passport, path, nd), 120)} | {raw.get('confidence')} ({raw.get('support')}){note} |"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_s(c) for c in r) + " |")
    return out


def render_summary(passport: dict[str, Any]) -> list[str]:
    L = [f"# Паспорт стиля — {passport.get('label') or passport.get('style_id') or 'стиль'}", ""]
    src = passport.get("sources") or {}
    llm = passport.get("llm") or {}
    L.append(
        f"`style_id`: **{passport.get('style_id')}** · схема `{passport.get('$schema')}` · роликов: {src.get('count', 0)} "
        f"({', '.join(src.get('videos') or [])}) · LLM: {llm.get('calls_used', 0)}/{llm.get('calls_limit', 0)} вызовов"
        f"{' · модель ' + str(llm.get('model')) if llm.get('model') else ''} · транскрипт: {_val(passport, 'script.transcript_source')}"
    )
    L.append("")
    L.append("## 1. Сводка стиля")
    L.append("")
    L.append("| Поле | Значение | Уверенность (поддержка) |")
    L.append("|---|---|---|")
    rows = [
        ("Формат", "format.aspect"), ("Длительность, с", "format.duration_s"), ("Разрешение", "format.resolution"),
        ("Рисовка", "visual.medium"), ("Палитра", "visual.palette_hex"), ("Насыщенность", "visual.saturation"), ("Контраст", "visual.contrast"),
        ("Контур", "visual.line"), ("Свет", "visual.light"), ("Композиция", "visual.composition"), ("Крупности", "visual.framing_mix"),
        ("Герои/объекты", "visual.subjects"), ("Мир", "visual.world"), ("Настроение", "visual.mood"),
        ("Язык", "voice.language"), ("Голос", "voice.gender"), ("Тембр", "voice.timbre"), ("Интонация", "voice.intonation"),
        ("Темп, слов/мин", "voice.pace_wpm"), ("Паузы медиана, с", "voice.pause_median_s"), ("Обращение", "voice.address"),
        ("Музыка", "music.genre"), ("Роль музыки", "music.role"), ("Уровень музыки к голосу, dB", "music.level_vs_voice_db"),
        ("SFX", "sfx.kinds"), ("Плотность SFX", "sfx.density"),
        ("Длина плана медиана, с", "motion.shot_len_s"), ("Длина плана p10 / p90, с", "motion.shot_len_p10_s"), ("Склеек в минуту", "motion.cuts_per_min"),
        ("Камера", "motion.camera_vocab"), ("Переходы", "motion.transitions"), ("Движение объектов", "motion.subject_motion"),
        ("Построение плана", "motion.shot_construction"), ("Субтитры", "motion.subtitles_mode"), ("Позиция субтитров", "motion.subtitles_position"), ("Стиль субтитров", "motion.subtitles_style"),
        ("Структура", "narrative.structure"), ("Хук", "narrative.hook_type"), ("Тон", "narrative.tone_notes"),
    ]
    for label, path in rows:
        r = _row(passport, label, path, 2 if path.endswith(("_s", "_db", "wpm")) else None)
        if r:
            L.append(r)
    L.append("")
    return L


def render_video(v: dict[str, Any]) -> list[str]:
    name = v.get("video") or "ролик"
    L = [f"### {name} — {_tc(v.get('duration_s'))}, транскрипт: {v.get('transcript_source') or '—'}", ""]
    link = v.get("linkage") or {}
    mo = v.get("motion") or {}
    L.append(
        f"Планов: **{mo.get('shots', len(v.get('shots') or []))}**, склеек/мин {mo.get('cuts_per_min', '—')}, медиана плана {mo.get('shot_len_s', '—')} с "
        f"(min {mo.get('shot_len_min_s', '—')}, max {mo.get('shot_len_max_s', '—')}); строк субтитров {link.get('lines', '—')}; "
        f"фраз, начинающихся новой склейкой: {link.get('phrase_starts_new_shot_ratio', '—')}; склеек на старте фразы: {link.get('cut_on_phrase_ratio', '—')}; "
        f"смещение склейки к фразе: медиана {link.get('cut_offset_ms_median', '—')} мс; паттерн: `{link.get('pattern', '—')}`."
    )
    L.append("")
    L.append("#### Планы (таймкоды из детектора; описания — vision-агент)")
    L.append("")
    headers = ["#", "tc", "len", "in", "крупн.", "камера", "что в кадре", "анимация объекта", "начало → изменение → конец", "переход →", "иллюстрирует", "закадр"]
    rows: list[list[Any]] = []
    for s in v.get("shots") or []:
        construct = " → ".join(x for x in (_s(s.get("start_state"), 60), _s(s.get("change"), 60), _s(s.get("end_state"), 60)) if x)
        tin = str(s.get("transition_in") or "")
        if s.get("uncertain"):
            tin += "?"
        rows.append(
            [
                s.get("i"),
                f"{_tc(s.get('start'))}–{_tc(s.get('end'))}",
                f"{float(s.get('len') or 0):.1f}",
                tin,
                s.get("framing") or "",
                s.get("camera") or "",
                _s(s.get("what"), 120),
                _s(s.get("subject_anim"), 90),
                construct,
                " ".join(x for x in (str(s.get("transition_out") or ""), _s(s.get("transition_note"), 60)) if x),
                _s(s.get("illustrates"), 60),
                _s(s.get("vo_text") or s.get("text"), 110),
            ]
        )
    L.extend(_table(headers, rows))
    L.append("")
    L.append("`in` — тип входящей склейки по детектору: hard / cue (принята по смене субтитра) / gradual (плавная); `?` — слабая, не подтверждена.")
    L.append("")
    sents = v.get("sentences") or []
    if sents:
        L.append("#### Предложения закадра")
        L.append("")
        L.extend(_table(["#", "tc", "слов", "текст"], [[s.get("i"), f"{_tc(s.get('start'))}–{_tc(s.get('end'))}", s.get("words"), _s(s.get("text"), 220)] for s in sents]))
        L.append("")
    nar = v.get("narrative") or {}
    segs = nar.get("segments") or []
    if segs:
        L.append("#### Сегменты сценария (агент)")
        L.append("")
        L.extend(_table(["сегмент", "tc", "суть", "цитата"], [[g.get("name"), f"{_tc(g.get('start'))}–{_tc(g.get('end'))}", _s(g.get("function") or g.get("summary"), 160), _s(g.get("quote"), 120)] for g in segs if isinstance(g, dict)]))
        L.append("")
    probs = v.get("problems") or {}
    if probs:
        L.append("Проблемы: " + "; ".join(f"{k}: {', '.join(map(str, vv)) if isinstance(vv, list) else vv}" for k, vv in probs.items()))
        L.append("")
    return L


def render_linkage(passport: dict[str, Any]) -> list[str]:
    L = ["## 3. Связка закадра и картинки", ""]
    L.append("| Метрика | Значение | Уверенность |")
    L.append("|---|---|---|")
    for label, path in (
        ("Паттерн синхронизации", "linkage.pattern"),
        ("Доля фраз, начинающихся новой склейкой", "linkage.phrase_starts_new_shot_ratio"),
        ("Доля склеек на старте фразы (±0.35 с)", "linkage.cut_on_phrase_ratio"),
        ("Смещение склейки к началу фразы, мс (медиана, знак)", "linkage.cut_offset_ms_median"),
        ("|смещение|, мс (медиана)", "linkage.cut_offset_ms_abs_median"),
        ("Строк субтитров на план (медиана)", "linkage.lines_per_shot_median"),
        ("Слов в строке субтитра (медиана)", "linkage.words_per_line_median"),
        ("Длительность строки, с (медиана)", "linkage.line_len_s_median"),
        ("Картинка буквально иллюстрирует существительное фразы (доля)", "linkage.visual_illustrates_noun_ratio"),
    ):
        r = _row(passport, label, path)
        if r:
            L.append(r)
    L.append("")
    notes = []
    for v in passport.get("videos") or []:
        rep = v.get("patterns") or {}
        for k in ("linkage_notes", "timing_notes"):
            if rep.get(k):
                notes.append(f"- **{v.get('video')}** · {k}: {rep[k]}")
    if notes:
        L.extend(notes)
        L.append("")
    # пример построчной связки (первые 24 строки первого ролика)
    for v in passport.get("videos") or []:
        lines = v.get("linkage_lines") or []
        if not lines:
            continue
        L.append(f"Пример связки строк и планов — {v.get('video')} (первые {min(24, len(lines))} строк):")
        L.append("")
        L.extend(_table(["строка", "tc", "планы", "текст"], [[ln.get("i"), f"{_tc(ln.get('start'))}–{_tc(ln.get('end'))}", ",".join(map(str, ln.get("shot_ids") or [])), _s(ln.get("text"), 120)] for ln in lines[:24]]))
        L.append("")
        break
    return L


def render_script(passport: dict[str, Any]) -> list[str]:
    L = ["## 4. Сценарий и повествование", ""]
    L.append("| Метрика | Значение | Уверенность |")
    L.append("|---|---|---|")
    for label, path in (
        ("Структура", "narrative.structure"),
        ("Тип хука", "narrative.hook_type"),
        ("Обращение", "narrative.address"),
        ("Фраз (битов) в секунду", "script.beats_per_sec"),
        ("Предложений в минуту", "script.sentences_per_min"),
        ("Слов в предложении (медиана / max)", "script.words_per_sentence_median"),
        ("Длительность предложения, с (медиана)", "script.sentence_len_s_median"),
        ("Речь начинается, с", "script.speech_starts_at_s"),
        ("Хвост после речи, с", "script.tail_silence_s"),
        ("Первые 3 с (хук)", "script.hook_text"),
        ("Пример хука (агент)", "narrative.example_hook"),
        ("Пример развязки (агент)", "narrative.example_payoff"),
        ("Тон", "narrative.tone_notes"),
    ):
        r = _row(passport, label, path)
        if r:
            L.append(r)
    L.append("")
    segs = _pv(passport, "narrative.segments") or []
    if isinstance(segs, list) and segs:
        L.append("### Сегменты (объединение по роликам)")
        L.append("")
        L.extend(_table(["сегмент", "роликов", "tc (первый)", "суть", "цитата"], [[g.get("name"), g.get("videos", 1), f"{_tc(g.get('start'))}–{_tc(g.get('end'))}", _s(g.get("function") or g.get("summary"), 160), _s(g.get("quote"), 120)] for g in segs if isinstance(g, dict)]))
        L.append("")
    tpls = _pv(passport, "narrative.sentence_templates") or []
    if tpls:
        L.append("### Шаблоны предложений")
        L.append("")
        L.extend(f"- {t}" for t in tpls)
        L.append("")
    rh = _pv(passport, "narrative.rhetoric_devices") or []
    if isinstance(rh, list) and rh:
        L.append("### Риторические приёмы")
        L.append("")
        L.extend(
            _table(
                ["приём", "описание", "частота", "примеры (цитаты с tc)"],
                [
                    [
                        r.get("name"),
                        _s(r.get("description"), 160),
                        r.get("frequency") if r.get("frequency") is not None else "",
                        _s("; ".join(map(str, r.get("examples") or [])) or r.get("example") or "", 220),
                    ]
                    for r in rh
                    if isinstance(r, dict)
                ],
            )
        )
        L.append("")
    fb = _pv(passport, "narrative.forbidden_phrases_seed") or []
    if fb:
        L.append("Запрещённые обороты (seed): " + ", ".join(f"«{x}»" for x in fb))
        L.append("")
    return L


def render_patterns(passport: dict[str, Any]) -> list[str]:
    L = ["## 5. Повторяющиеся приёмы и фирменные трюки", ""]
    pats = passport.get("patterns") or []
    if not pats:
        L.append("_Агент patterns не отработал (нет LLM или лимит) — приёмы не названы._")
        L.append("")
        return L
    rows = []
    for p in pats:
        freq = p.get("frequency")
        freq_s = "; ".join(f"{k}: {v}" for k, v in freq.items() if v) if isinstance(freq, dict) else str(freq or "")
        ex = p.get("examples") or []
        ex_s = "; ".join(f"{e.get('video', '')} {e.get('tc', '')}".strip() if isinstance(e, dict) else str(e) for e in ex[:6])
        rows.append([("★ " if p.get("signature") else "") + str(p.get("name")), p.get("kind"), p.get("support", ""), freq_s, _s(p.get("description"), 200), ex_s, _s(p.get("how_to_reproduce"), 240)])
    L.extend(_table(["приём", "тип", "роликов", "частота", "описание", "примеры (ролик tc)", "как воспроизвести"], rows))
    L.append("")
    L.append("★ — фирменный приём (signature).")
    L.append("")
    return L


def render_peculiarities(passport: dict[str, Any]) -> list[str]:
    L = ["## 6. Особенности стиля", ""]
    pec = passport.get("peculiarities") or []
    L.extend(f"- {p}" for p in pec) if pec else L.append("- —")
    L.append("")
    return L


def render_blueprint(passport: dict[str, Any]) -> list[str]:
    bp = passport.get("blueprint") or {}
    L = ["## 7. Blueprint генерации (цепочка нод Studio)", ""]
    if not bp:
        L.append("_Blueprint не собран._")
        return L
    L.append(f"Источник: {bp.get('source', 'default')}. Новые ноды (нет в Studio): " + ("; ".join(bp.get("new_nodes_needed") or []) or "—"))
    L.append("")
    rows = []
    for n in bp.get("chain") or []:
        params = n.get("params") or {}
        p_s = ", ".join(f"{k}={v}" for k, v in params.items()) if isinstance(params, dict) else str(params)
        rows.append([
            ("NEW " if n.get("new") else "") + str(n.get("node_type")),
            n.get("label"),
            ", ".join(map(str, n.get("inputs") or [])),
            ", ".join(map(str, n.get("outputs") or [])),
            _s(p_s, 160),
            _s(n.get("prompt_hint") or "", 160),
            _s(n.get("fallback_existing") or "", 80),
        ])
    L.extend(_table(["нода", "роль", "входы", "выходы", "параметры", "требования к промпту", "чем заменить сейчас"], rows))
    L.append("")
    L.append("### Шаблоны промптов")
    L.append("")
    titles = {
        "script_writer": "Сценарист (script)",
        "storyboard": "Раскадровка (shot_plan / sd_agent camera+action)",
        "image_prompt": "Промт картинки (image_prompts)",
        "video_prompt": "Промт анимации (animation_prompts)",
        "vo_tts": "Озвучка (audio)",
        "subtitles": "Субтитры (assemble)",
        "edit": "Монтаж (assemble / vo_sync_edit)",
        "music_sfx": "Музыка и SFX (music / sfx_plan)",
    }
    for key, text in (bp.get("prompt_templates") or {}).items():
        L.append(f"#### {titles.get(key, key)}")
        L.append("")
        L.append("```text")
        L.append(str(text).strip())
        L.append("```")
        L.append("")
    if bp.get("notes"):
        L.append("Заметки blueprint:")
        L.extend(f"- {n}" for n in bp["notes"])
        L.append("")
    return L


def render_variance(passport: dict[str, Any]) -> list[str]:
    L = ["## 8. Разброс и что проверить вручную", ""]
    low = []
    for axis in schema.AXES:
        for key, raw in (passport.get(axis) or {}).items():
            if isinstance(raw, dict) and raw.get("confidence") == "low":
                low.append(f"- `{axis}.{key}` = {_s(_val(passport, f'{axis}.{key}'), 80)} ({raw.get('support')}){' — ' + _s(raw.get('note'), 100) if raw.get('note') else ''}")
    L.extend(low or ["- все поля medium/high"])
    L.append("")
    for n in passport.get("variance_notes") or []:
        L.append(f"- {n}")
    errs = passport.get("errors") or {}
    if errs.get("videos") or errs.get("synth") or errs.get("blueprint") or errs.get("stopped"):
        L.append("")
        L.append("Ошибки прогона: " + _s(errs, 400))
    L.append("")
    return L


def render_report(passport: dict[str, Any]) -> str:
    L = render_summary(passport)
    L.append("## 2. Разбор по роликам: планы и таймкоды")
    L.append("")
    for v in passport.get("videos") or []:
        L.extend(render_video(v))
    L.extend(render_linkage(passport))
    L.extend(render_script(passport))
    L.extend(render_patterns(passport))
    L.extend(render_peculiarities(passport))
    L.extend(render_blueprint(passport))
    L.extend(render_variance(passport))
    return "\n".join(L).rstrip() + "\n"
