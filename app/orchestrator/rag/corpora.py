"""Единственное место, где имена корпусов RAG сопоставляются индексу.

Канон пишется в ``chunks.corpus``. Алиасы плана — только на входе поиска.
"""

from __future__ import annotations

CANONICAL_CORPORA: tuple[str, ...] = ("rules", "history", "nodes", "prompts")

# ``chats``: окна ``orch_messages`` пишет ``loaders._chats`` в корпус history.
# ``projects``: строки таблицы ``projects`` пишет ``loaders._projects`` туда же
# (``source_id`` вида ``project:{id}``, ``kind=project``). Отдельного индекса
# projects нет — данные проекта уже лежат в history, это не пустой корпус.
CORPUS_ALIASES: dict[str, str] = {
    "chats": "history",
    "projects": "history",
}


class UnknownCorpus(ValueError):
    """Имя набора нет ни в каноне, ни в алиасах. Это не пустая выдача."""

    def __init__(self, names: list[str]) -> None:
        self.names = list(names)
        allowed = ", ".join([*CANONICAL_CORPORA, *CORPUS_ALIASES])
        super().__init__(f"неизвестный корпус: {', '.join(self.names)}; допустимо: {allowed}")


def resolve_corpora(names: list[str] | tuple[str, ...] | None) -> list[str]:
    """Канонические имена индекса. ``None`` — все четыре корпуса.

    Алиас заменяется на индекс, куда реально пишутся данные. Неизвестное
    имя — ошибка, не пустая выдача. Повторы после замены схлопываются.
    """
    if names is None:
        return list(CANONICAL_CORPORA)
    resolved: list[str] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name in CANONICAL_CORPORA:
            canonical = name
        elif name in CORPUS_ALIASES:
            canonical = CORPUS_ALIASES[name]
        else:
            unknown.append(str(name))
            continue
        if canonical not in seen:
            seen.add(canonical)
            resolved.append(canonical)
    if unknown:
        raise UnknownCorpus(unknown)
    return resolved
