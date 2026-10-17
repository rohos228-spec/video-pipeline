"""Связка закадра (строки субтитров) с планами + метрики сценария.

Детерминированная часть: какие строки звучат в каком плане, насколько
склейки совпадают с началом фраз (смещение в мс), сколько фраз на план,
темп «битов» (фраз/с), предложения и их длина, положение хука. Семантику
(«картинка иллюстрирует существительное фразы», риторика, сегменты
hook/setup/escalation/payoff) заполняют агенты ``shots`` / ``narrative``;
здесь — только то, что считается без модели.
"""

from __future__ import annotations

import re
import statistics
from typing import Any

PHRASE_CUT_WINDOW_S = 0.35
MIN_OVERLAP_S = 0.12  # перекрытие строки и плана короче ~3–4 кадров — не связь, а джиттер таймкодов
_SENT_START_RE = re.compile(r"^[\"“]?[A-ZА-ЯЁ]")  # цифра в начале строки («133 days») — не новое предложение
_SENT_END_RE = re.compile(r"[.!?…]$")


def _mid(x: dict[str, Any]) -> float:
    return (float(x["start"]) + float(x["end"])) / 2


def link_subtitles_to_shots(subs: list[dict[str, Any]], shots: list[dict[str, Any]]) -> dict[str, Any]:
    """→ {lines:[{i, start, end, text, shot_ids}], shots:[{i, line_ids, text}], stats:{...}}."""
    lines_out: list[dict[str, Any]] = []
    shot_lines: dict[int, list[int]] = {int(s["i"]): [] for s in shots}
    for li, ln in enumerate(subs, start=1):
        a, b = float(ln["start"]), float(ln["end"])
        ids = []
        for s in shots:
            sa, sb = float(s["start"]), float(s["end"])
            overlap = min(b, sb) - max(a, sa)
            if overlap >= MIN_OVERLAP_S or (sa <= _mid(ln) < sb):
                ids.append(int(s["i"]))
        if not ids and shots:
            # строка за пределами планов (хвост) — ближайший
            ids = [int(min(shots, key=lambda s: abs(_mid(s) - _mid(ln)))["i"])]
        for sid in ids:
            shot_lines.setdefault(sid, []).append(li)
        lines_out.append({"i": li, "start": a, "end": b, "text": ln["text"], "shot_ids": ids})
    shots_out = []
    for s in shots:
        ids = shot_lines.get(int(s["i"]), [])
        text = " ".join(subs[i - 1]["text"] for i in ids)
        shots_out.append({"i": int(s["i"]), "start": s["start"], "end": s["end"], "line_ids": ids, "text": text})

    # склейка ↔ начало фразы
    cut_times = [float(s["start"]) for s in shots[1:]]
    line_starts = [float(ln["start"]) for ln in subs]
    offsets_ms: list[float] = []
    on_phrase = 0
    for ct in cut_times:
        if not line_starts:
            break
        nearest = min(line_starts, key=lambda t: abs(t - ct))
        off = (ct - nearest) * 1000.0
        offsets_ms.append(round(off, 0))
        if abs(off) <= PHRASE_CUT_WINDOW_S * 1000:
            on_phrase += 1
    # «фраза начинается с нового плана»: начало ролика — тоже начало плана
    shot_starts = ([float(shots[0]["start"])] if shots else []) + cut_times
    phrases_with_cut = 0
    for t in line_starts:
        if any(abs(t - st) <= PHRASE_CUT_WINDOW_S for st in shot_starts):
            phrases_with_cut += 1
    lines_per_shot = [len(v) for v in shot_lines.values()] if shot_lines else []
    stats = {
        "lines": len(subs),
        "shots": len(shots),
        "cuts": len(cut_times),
        "cut_on_phrase_ratio": round(on_phrase / len(cut_times), 3) if cut_times else None,
        "phrase_starts_new_shot_ratio": round(phrases_with_cut / len(line_starts), 3) if line_starts else None,
        "cut_offset_ms_median": round(statistics.median(offsets_ms), 0) if offsets_ms else None,
        "cut_offset_ms_abs_median": round(statistics.median([abs(o) for o in offsets_ms]), 0) if offsets_ms else None,
        "lines_per_shot_median": round(statistics.median(lines_per_shot), 1) if lines_per_shot else None,
        "lines_per_shot_max": max(lines_per_shot) if lines_per_shot else None,
        "line_len_s_median": round(statistics.median([float(l["end"]) - float(l["start"]) for l in subs]), 2) if subs else None,
        "words_per_line_median": round(statistics.median([len(l["text"].split()) for l in subs]), 1) if subs else None,
    }
    stats["pattern"] = classify_sync(stats)
    return {"lines": lines_out, "shots": shots_out, "stats": stats}


def classify_sync(stats: dict[str, Any]) -> str:
    r = stats.get("phrase_starts_new_shot_ratio")
    lps = stats.get("lines_per_shot_median")
    if r is None:
        return "unknown"
    if r >= 0.6:
        return "each_phrase_new_shot"
    if lps is not None and lps >= 2:
        return "shot_spans_phrases"
    if r >= 0.35:
        return "mixed"
    return "shot_independent_of_phrases"


def sentences_from_lines(subs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Строки субтитров → предложения (начало с заглавной / конец пунктуацией)."""
    sents: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for i, ln in enumerate(subs, start=1):
        text = str(ln["text"]).strip()
        if not text:
            continue
        starts_new = cur is None or bool(_SENT_START_RE.match(text)) and not str(cur["text"]).endswith(",")
        if starts_new and cur is not None:
            sents.append(cur)
            cur = None
        if cur is None:
            cur = {"start": float(ln["start"]), "end": float(ln["end"]), "text": text, "line_ids": [i]}
        else:
            cur["end"] = float(ln["end"])
            cur["text"] = f"{cur['text']} {text}"
            cur["line_ids"].append(i)
        if _SENT_END_RE.search(text):
            sents.append(cur)
            cur = None
    if cur is not None:
        sents.append(cur)
    for k, s in enumerate(sents, start=1):
        s["i"] = k
        s["words"] = len(s["text"].split())
        s["len_s"] = round(s["end"] - s["start"], 2)
    return sents


def script_metrics(subs: list[dict[str, Any]], duration_s: float) -> dict[str, Any]:
    """Биты/с, длины предложений, хук, доли по времени."""
    if not subs:
        return {"lines": 0, "sentences": 0, "beats_per_sec": None, "hook_text": "", "sentences_list": []}
    sents = sentences_from_lines(subs)
    span = max(float(subs[-1]["end"]) - float(subs[0]["start"]), 0.5)
    words_total = sum(len(l["text"].split()) for l in subs)
    hook = [l["text"] for l in subs if float(l["start"]) < float(subs[0]["start"]) + 3.0]
    first_sentence = sents[0]["text"] if sents else ""
    openers = [s["text"].split()[0] for s in sents if s["text"].split()]
    out = {
        "lines": len(subs),
        "sentences": len(sents),
        "words": words_total,
        "beats_per_sec": round(len(subs) / span, 2),
        "sentences_per_min": round(len(sents) / span * 60.0, 1),
        "words_per_sentence_median": round(statistics.median([s["words"] for s in sents]), 1) if sents else None,
        "words_per_sentence_max": max(s["words"] for s in sents) if sents else None,
        "sentence_len_s_median": round(statistics.median([s["len_s"] for s in sents]), 2) if sents else None,
        "hook_text": " ".join(hook)[:300],
        "first_sentence": first_sentence[:300],
        "last_sentence": sents[-1]["text"][:300] if sents else "",
        "speech_starts_at_s": float(subs[0]["start"]),
        "speech_ends_at_s": float(subs[-1]["end"]),
        "tail_silence_s": round(max(0.0, duration_s - float(subs[-1]["end"])), 2),
        "sentence_openers": openers[:40],
        "sentences_list": [{k: s[k] for k in ("i", "start", "end", "text", "words", "len_s")} for s in sents],
    }
    return out


def timed_transcript(subs: list[dict[str, Any]], shots_link: list[dict[str, Any]] | None = None) -> str:
    """Текст для агентов: `[mm:ss.ss–mm:ss.ss] (shot N) text` построчно."""

    def tc(t: float) -> str:
        m = int(t // 60)
        return f"{m:02d}:{t - m * 60:05.2f}"

    by_line: dict[int, list[int]] = {}
    for s in shots_link or []:
        for li in s.get("line_ids") or []:
            by_line.setdefault(int(li), []).append(int(s["i"]))
    rows = []
    for i, ln in enumerate(subs, start=1):
        shots_s = ",".join(str(x) for x in by_line.get(i, [])) or "-"
        rows.append(f"[{tc(float(ln['start']))}–{tc(float(ln['end']))}] (shot {shots_s}) {ln['text']}")
    return "\n".join(rows)


def shots_table(shots: list[dict[str, Any]], link: dict[str, Any] | None = None) -> str:
    """Компактная таблица планов для агентов: № | start–end | len | transition_in | VO."""
    text_by_shot = {int(s["i"]): s.get("text") or "" for s in (link or {}).get("shots") or []}
    rows = ["# | start | end | len_s | transition_in | motion_energy | VO"]
    for s in shots:
        rows.append(
            f"{s['i']} | {float(s['start']):.2f} | {float(s['end']):.2f} | {float(s['len']):.2f} | "
            f"{s.get('transition_in') or ''} | {s.get('motion_energy', '')} | {text_by_shot.get(int(s['i']), '')[:120]}"
        )
    return "\n".join(rows)
