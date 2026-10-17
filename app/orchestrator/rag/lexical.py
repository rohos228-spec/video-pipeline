"""Токены BM25: snake_case режется на слова, русские формы сводятся к основе.

Snowball держит одну основу у «стыкуют» и «стыка». Лемма pymorphy
добирает формы, которые snowball не склеивает («картинок» и «картинки»).
Латиница остаётся как есть, в нижнем регистре.
"""

from __future__ import annotations

import re
from functools import lru_cache

_WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")
_MIN_LEN = 3
LEXICAL_ANALYZER = "unicode61-snake-stem-prefix-v1"
_PREFIX_MIN = 4


def _snowball():
    from snowballstemmer import stemmer

    return stemmer("russian")


@lru_cache(maxsize=1)
def _ru_stemmer():
    return _snowball()


@lru_cache(maxsize=1)
def _morph():
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


def _fold(token: str) -> str:
    return token.lower().replace("ё", "е")


def _russian(token: str) -> bool:
    return any("а" <= char <= "я" for char in token)


def split_snake(token: str) -> list[str]:
    """`тип_стыка` → тип, стыка. Слово без подчёркивания не трогаем."""
    parts = [part for part in token.split("_") if part]
    return parts or [token]


def _keep(forms: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    kept: list[str] = []
    for form in forms:
        if len(form) < _MIN_LEN or form in seen:
            continue
        seen.add(form)
        kept.append(form)
    return tuple(kept)


@lru_cache(maxsize=4096)
def index_forms(token: str) -> tuple[str, ...]:
    """Одна основа на слово: длина документа в FTS остаётся как у исходного текста."""
    folded = _fold(token)
    if not _russian(folded):
        return _keep([folded])
    return _keep([_ru_stemmer().stemWord(folded)])


@lru_cache(maxsize=4096)
def query_forms(token: str) -> tuple[str, ...]:
    """Поверхность, основа и основа леммы.

    Поверхность попадает в тот же запрос, что и старый индекс: точное слово
    не теряется, когда основа у запроса и документа одна.
    """
    folded = _fold(token)
    forms = [folded, *index_forms(folded)]
    if _russian(folded):
        lemma = _fold(_morph().parse(folded)[0].normal_form)
        forms.extend(index_forms(lemma))
    return _keep(forms)


def _collect(text: str, forms_of) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for raw in _WORD.findall(text or ""):
        for piece in split_snake(raw):
            for form in forms_of(piece):
                if form in seen:
                    continue
                seen.add(form)
                tokens.append(form)
    return tokens


def lexical_tokens(text: str) -> list[str]:
    """Формы запроса: основа и основа леммы."""
    return _collect(text, query_forms)


def lexical_index_text(text: str) -> str:
    """Текст для FTS. Подчёркивание — пробел, дальше режет unicode61.

    Отдельные основы в документ не дописываем: длина чанка остаётся прежней,
    точные слова ищутся как раньше.
    """
    return (text or "").replace("_", " ")


def _exact_tokens(text: str, *, limit: int) -> list[str]:
    seen: list[str] = []
    lowered: set[str] = set()
    raw = _WORD.findall(text or "")
    min_len = _MIN_LEN if any(len(token) >= _MIN_LEN for token in raw) else 1
    for token in raw:
        if len(token) < min_len:
            continue
        key = _fold(token)
        if key in lowered:
            continue
        lowered.add(key)
        seen.append(token.replace('"', '""'))
        if len(seen) >= limit:
            break
    return seen


def _prefix_stems(text: str) -> list[str]:
    """Основы короче слова: ``стык*`` находит и «стыкуют», и «стыка»."""
    prefixes: list[str] = []
    seen: set[str] = set()
    for raw in _WORD.findall(text or ""):
        for piece in split_snake(raw):
            for form in query_forms(piece):
                if len(form) < _PREFIX_MIN or form in seen:
                    continue
                if form == _fold(piece):
                    continue
                seen.add(form)
                prefixes.append(form)
    return prefixes


def fts_or_query(text: str, *, limit: int = 48) -> str:
    exact = _exact_tokens(text, limit=limit)
    prefixes = [stem for stem in _prefix_stems(text) if stem not in {_fold(token) for token in exact}]
    parts = [f'"{token}"' for token in exact]
    parts.extend(f"{stem}*" for stem in prefixes[:limit])
    if not parts:
        return ""
    return " OR ".join(parts)
