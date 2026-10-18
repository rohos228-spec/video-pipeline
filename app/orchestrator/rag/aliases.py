"""Индекс алиасов имён людей и мест.

Пары берутся из строк корпуса, где два имени названы одной сущностью
(«Павел» / «механик», «кухня» = «столовая»). Текст запроса в индекс не входит.
В FTS к чанку дописываются только те алиасы, которых в его тексте нет.
"""

from __future__ import annotations

import re

from app.orchestrator.rag.lexical import _fold, _lemma

_QUOTE = re.compile(r"«([^»]{2,40})»")
_CHAIN = re.compile(r"(?:«[^»]{2,40}»\s*(?:/|=)\s*)+«[^»]{2,40}»")
_IDENTITY = ("сущност", "кореферен", "один id", "не две", "одно место", "одна локац", "один топоним")
_SKIP_POS = frozenset({"VERB", "INFN", "PREP", "CONJ", "PRCL", "NPRO", "INTJ"})


def _content_name(span: str) -> str:
    """Одно имя из кавычек: без глаголов, местоимений и чисел."""
    folded = _fold(span).strip()
    if not folded or any(char.isdigit() for char in folded):
        return ""
    words = [word for word in re.findall(r"[a-zа-я]+", folded) if len(word) >= 3]
    if len(words) != 1:
        return ""
    word = words[0]
    from app.orchestrator.rag.lexical import _morph

    parsed = _morph().parse(word)[0]
    if parsed.tag.POS in _SKIP_POS:
        return ""
    return _lemma(word)


def alias_groups(texts: list[str]) -> dict[str, frozenset[str]]:
    """Лемма имени → другие леммы той же сущности. Запросы сюда не передают."""
    groups: dict[str, set[str]] = {}
    for text in texts:
        for line in (text or "").splitlines():
            folded = _fold(line)
            if not any(cue in folded for cue in _IDENTITY):
                continue
            for chain in _CHAIN.findall(line):
                names = [name for name in (_content_name(span) for span in _QUOTE.findall(chain)) if name]
                unique = list(dict.fromkeys(names))
                if len(unique) < 2:
                    continue
                for name in unique:
                    groups.setdefault(name, set()).update(other for other in unique if other != name)
    return {key: frozenset(values) for key, values in groups.items()}


def names_in_text(text: str, table: dict[str, frozenset[str]]) -> set[str]:
    found: set[str] = set()
    for raw in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", text or ""):
        lemma = _lemma(_fold(raw))
        if lemma in table:
            found.add(lemma)
    return found


def alias_suffix(text: str, table: dict[str, frozenset[str]]) -> str:
    """Алиасы, которых нет в тексте чанка. Пусто, если называть нечего."""
    if not table:
        return ""
    present = names_in_text(text, table)
    extra: list[str] = []
    seen: set[str] = set()
    for name in sorted(present):
        for partner in sorted(table[name]):
            if partner in present or partner in seen:
                continue
            seen.add(partner)
            extra.append(partner)
    return " ".join(extra)
