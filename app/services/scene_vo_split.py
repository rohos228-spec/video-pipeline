"""Нарезка закадра сцены: 13–90, цель 50–70, одна цифра = 4 символа."""

from __future__ import annotations

import re

from app.services.scene_design.camera_expand import _clause_units, _sentence_units

VO_MIN = 13
VO_MAX = 90
VO_TARGET_LO = 50
VO_TARGET_HI = 70
DIGIT_WEIGHT = 4

_WORD_SPLIT = re.compile(r"\s+")


def vo_weight(text: str) -> int:
    """Длина для лимитов: буква/пробел/знак = 1, цифра = 4."""
    s = " ".join((text or "").split())
    return sum(DIGIT_WEIGHT if ch.isdigit() else 1 for ch in s)


def _join(parts: list[str]) -> str:
    return " ".join(p for p in parts if p)


def _units(text: str) -> list[str]:
    raw = " ".join((text or "").split())
    if not raw:
        return []
    sents = _sentence_units(raw)
    if not sents:
        return [raw]
    clauses = _clause_units(sents)
    out: list[str] = []
    for unit in clauses or sents:
        if vo_weight(unit) <= VO_MAX:
            out.append(unit)
            continue
        words = unit.split()
        buf: list[str] = []
        for word in words:
            cand = _join(buf + [word])
            if buf and vo_weight(cand) > VO_MAX:
                out.append(_join(buf))
                buf = [word]
            else:
                buf.append(word)
        if buf:
            out.append(_join(buf))
    return out or [raw]


def _flush_short(parts: list[str]) -> list[str]:
    i = 0
    while i < len(parts):
        if vo_weight(parts[i]) >= VO_MIN or len(parts) == 1:
            i += 1
            continue
        if i + 1 < len(parts):
            merged = _join([parts[i], parts[i + 1]])
            if vo_weight(merged) <= VO_MAX or vo_weight(parts[i]) < VO_MIN:
                parts[i] = merged
                parts.pop(i + 1)
                continue
        if i > 0:
            merged = _join([parts[i - 1], parts[i]])
            if vo_weight(merged) <= VO_MAX or vo_weight(parts[i]) < VO_MIN:
                parts[i - 1] = merged
                parts.pop(i)
                i -= 1
                continue
        i += 1
    return parts


def split_scene_vo(text: str) -> list[str]:
    """Куски закадра: не короче 13, не длиннее 90 (по весу), цель 50–70.

    Склейка кусков по словам = исходный текст. Цифра весит как 4 символа.
    """
    raw = " ".join((text or "").split())
    if not raw:
        return []
    if vo_weight(raw) <= VO_MAX:
        return [raw]
    units = _units(raw)
    parts: list[str] = []
    buf: list[str] = []
    for unit in units:
        cand = _join(buf + [unit])
        cw = vo_weight(cand)
        bw = vo_weight(_join(buf)) if buf else 0
        if not buf:
            buf = [unit]
            continue
        if bw < VO_TARGET_LO and cw <= VO_MAX:
            buf.append(unit)
            continue
        if cw <= VO_TARGET_HI:
            buf.append(unit)
            continue
        if bw < VO_MIN and cw <= VO_MAX:
            buf.append(unit)
            continue
        parts.append(_join(buf))
        buf = [unit]
    if buf:
        parts.append(_join(buf))
    parts = [p for p in parts if p]
    parts = _flush_short(parts)
    glued = " ".join(parts).split()
    if glued != raw.split():
        return [raw]
    return parts or [raw]


def split_scene_vo_n(text: str, n: int) -> list[str]:
    """Разрезать на n кусков, затем подтянуть к 13–90 / 50–70."""
    from app.services.scene_design.camera_expand import split_text_into_parts

    raw = " ".join((text or "").split())
    n = max(1, int(n))
    if n == 1 or vo_weight(raw) <= VO_MAX:
        return [raw] if raw else []
    parts = [" ".join((p or "").split()) for p in split_text_into_parts(raw, n)]
    parts = [p for p in parts if p]
    if not parts:
        return split_scene_vo(raw)
    parts = _flush_short(parts)
    glued = " ".join(parts).split()
    if glued != raw.split():
        return split_scene_vo(raw)
    return parts
