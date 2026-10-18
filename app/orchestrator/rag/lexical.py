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
LEXICAL_ANALYZER = "unicode61-snake-stem-prefix-syn-v1"
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


def _lemma(token: str) -> str:
    folded = _fold(token)
    if not _russian(folded):
        return folded
    return _fold(_morph().parse(folded)[0].normal_form)


def _one_insertion(short: str, long: str) -> bool:
    """Одна буква внутри слова, не суффикс: общий последний символ."""
    if not short or len(long) != len(short) + 1 or not long.endswith(short[-1]):
        return False
    index = skipped = 0
    for char in long:
        if index < len(short) and short[index] == char:
            index += 1
        else:
            skipped += 1
            if skipped > 1:
                return False
    return index == len(short) and skipped == 1


def spelling_synonyms(field_texts: list[str], name_texts: list[str]) -> dict[str, frozenset[str]]:
    """Пары лемм: кусок поля ``промт_картинки`` и слово названия ``промпт``.

    Берём только идентификаторы с подчёркиванием и слова названий нод.
    Склеиваем, если это одна вставленная буква не в конце и морфология
    сама их не объединила. Склонения и чужие пары вроде «сцена»/«смена» не входят.
    """
    ident = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+")
    field_lemmas: set[str] = set()
    name_lemmas: set[str] = set()
    for text in field_texts:
        for raw in ident.findall(text or ""):
            if "_" not in raw:
                continue
            for piece in split_snake(raw):
                form = _lemma(piece)
                if len(form) >= 4:
                    field_lemmas.add(form)
    for text in name_texts:
        for raw in _WORD.findall(text or ""):
            form = _lemma(raw)
            if len(form) >= 4:
                name_lemmas.add(form)
    groups: dict[str, set[str]] = {}
    for field in field_lemmas:
        for name in name_lemmas:
            if field == name or field[:3] != name[:3]:
                continue
            short, long = (field, name) if len(field) <= len(name) else (name, field)
            if not _one_insertion(short, long):
                continue
            if _ru_stemmer().stemWord(field) == _ru_stemmer().stemWord(name):
                continue
            groups.setdefault(field, set()).add(name)
            groups.setdefault(name, set()).add(field)
    return {key: frozenset(values) for key, values in groups.items()}


@lru_cache(maxsize=1)
def registry_synonyms() -> dict[str, frozenset[str]]:
    """Синонимы реестра нод. Тексты запросов сюда не входят."""
    from pathlib import Path

    from app.orchestrator.rag.loaders import iter_git_nodes
    from app.project_root import find_project_root
    from app.services.node_groups import NODE_GROUPS

    names: list[str] = []
    fields: list[str] = []
    for group in NODE_GROUPS.values():
        names.append(group.title or "")
        names.append(group.description or "")
        for spec in group.nodes:
            names.append(spec.local_key or "")
            names.append(spec.label or "")
            names.append(spec.description or "")
            names.append(spec.preferred_id or "")
            fields.append(spec.preferred_id or "")
    root = find_project_root()
    for node in iter_git_nodes(Path(root)):
        names.append(node.title or "")
        names.append(node.description or "")
        fields.append(node.node_key or "")
        fields.append(node.prompt_template or "")
    return spelling_synonyms(fields, names)


def _synonym_terms(text: str, table: dict[str, frozenset[str]]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in _WORD.findall(text or ""):
        for piece in split_snake(raw):
            for partner in table.get(_lemma(piece), ()):
                if partner in seen:
                    continue
                seen.add(partner)
                found.append(partner)
    return found


def fts_or_query(text: str, *, limit: int = 48) -> str:
    exact = _exact_tokens(text, limit=limit)
    prefixes = [stem for stem in _prefix_stems(text) if stem not in {_fold(token) for token in exact}]
    partners = _synonym_terms(text, registry_synonyms())
    parts = [f'"{token}"' for token in exact]
    parts.extend(f'"{partner}"' for partner in partners if _fold(partner) not in {_fold(token) for token in exact})
    parts.extend(f"{stem}*" for stem in prefixes[:limit])
    if not parts:
        return ""
    return " OR ".join(parts)
