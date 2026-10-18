"""Буст семейства, если запрос называет ноду её именем из реестра.

Имя — подпись и local_key, не описание. Проверка наследует семейство ноды,
на которую указывает ребро группы. Слово, которое есть у двух семейств,
никого не поднимает.
"""

from __future__ import annotations

from functools import lru_cache

from app.orchestrator.rag.lexical import _lemma, lexical_tokens, registry_synonyms
from app.orchestrator.rag.types import Chunk


@lru_cache(maxsize=1)
def _family_by_local_key() -> dict[str, str]:
    from app.services.node_groups import NODE_GROUPS

    family: dict[str, str] = {}
    for group in NODE_GROUPS.values():
        for spec in group.nodes:
            family[spec.local_key] = spec.local_key
        for source, dest, _kind in group.internal_edges:
            if dest.startswith("check_") and source in family:
                family[dest] = family[source]
    return family


@lru_cache(maxsize=1)
def _token_family() -> dict[str, str]:
    """Лемма имени ноды → семейство, только если семейство одно."""
    from app.services.node_groups import NODE_GROUPS

    owners: dict[str, set[str]] = {}
    families = _family_by_local_key()
    for group in NODE_GROUPS.values():
        for spec in group.nodes:
            family = families.get(spec.local_key, spec.local_key)
            text = " ".join((spec.local_key or "", spec.label or ""))
            for token in lexical_tokens(text):
                if token.isascii() and len(token) < 5:
                    continue
                if not token.isascii() and len(token) < 3:
                    continue
                owners.setdefault(_lemma(token) if not token.isascii() else token, set()).add(family)
    return {token: next(iter(keys)) for token, keys in owners.items() if len(keys) == 1}


def explicit_families(query: str) -> set[str]:
    """Семейства, которые запрос назвал однозначно."""
    table = _token_family()
    synonyms = registry_synonyms()
    found: set[str] = set()
    for token in lexical_tokens(query):
        keys = {_lemma(token)}
        keys.update(synonyms.get(_lemma(token), ()))
        for key in keys:
            family = table.get(key)
            if family:
                found.add(family)
    return found


def family_of_node(node_key: str) -> str:
    from app.orchestrator.rag.loaders import group_role_index

    role = group_role_index().get(node_key) or {}
    local = str(role.get("local_key") or "")
    if not local:
        return ""
    return _family_by_local_key().get(local, local)


def boost_scores(
    scores: dict[str, float],
    by_id: dict[str, Chunk],
    query: str,
    factor: float,
) -> dict[str, float]:
    """Умножает оценку чанков названного семейства. factor=1 ничего не меняет."""
    if factor == 1:
        return scores
    families = explicit_families(query)
    if not families:
        return scores
    boosted = dict(scores)
    for chunk_id, score in scores.items():
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue
        node_key = str(chunk.metadata.get("node_key") or chunk.source_id)
        if family_of_node(node_key) in families:
            boosted[chunk_id] = score * factor
    return boosted
