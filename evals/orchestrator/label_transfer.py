"""Механический перенос меток контура B с нарезки max_parts=8 на max_parts=3.

Правило зафиксировано и после прогона контура B не меняется.
Исходный файл ``precision_live_queries.json`` это правило не переписывает.
live-29 не правится вручную: к нему применяется тот же перенос, что и к остальным.

Оценка старой метки — 1, если чанк перечислен в ``relevant``, иначе метки нет.
Новый чанк получает grade = max(grade) тех старых размеченных чанков, чей текст
в него вошёл целиком. Неоднозначные соответствия в новую разметку не попадают.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.orchestrator.rag.chunking import split_prompt
from app.orchestrator.rag.loaders import iter_git_nodes, load_rules
from app.orchestrator.rag.node_store import NODE_KEY_RE
from app.orchestrator.rag.store import chunk_id_for

ROOT = Path(__file__).resolve().parents[2]
QUERY_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_queries.json"
TRANSFERRED_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_labels_transferred.json"
AMBIGUOUS_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_label_ambiguous.json"
RULES_PATH = ROOT / "docs" / "fable" / "user_rules.md"

OLD_MAX_PARTS = 8
NEW_MAX_PARTS = 3


def prompt_sections(text: str, *, max_parts: int) -> list[tuple[str, str, tuple[int, ...]]]:
    """Та же укладка, что ``split_prompt``, плюс индексы секций ``##`` в каждой части.

    Индекс секции — позиция после ``strip`` и разреза по ``##``, как в ``split_prompt``.
    """
    body = (text or "").strip()
    if not body:
        return []
    from app.orchestrator.rag.chunking import _H2

    sections = [part.strip() for part in _H2.split(body) if part.strip()]
    if not sections:
        return []
    if len(sections) <= max_parts:
        packed = [(f"s{index}", section, (index,)) for index, section in enumerate(sections)]
    else:
        groups: dict[int, list[int]] = {index: [] for index in range(max_parts)}
        for index in range(len(sections)):
            bucket = min(max_parts - 1, index * max_parts // len(sections))
            groups[bucket].append(index)
        packed = []
        for bucket in range(max_parts):
            indexes = groups[bucket]
            if not indexes:
                continue
            joined = "\n\n".join(sections[index] for index in indexes)
            packed.append((f"s{bucket}", joined, tuple(indexes)))
    plain = split_prompt(body, max_parts=max_parts)
    if [(key, part) for key, part, _indexes in packed] != plain:
        raise RuntimeError("расклад секций разошёлся со split_prompt")
    return packed


def _templates(repo_root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in iter_git_nodes(repo_root):
        if not NODE_KEY_RE.match(node.node_key):
            continue
        found[node.node_key] = node.prompt_template or ""
    return found


def _transfer_prompt(
    source_id: str,
    part_key: str,
    templates: dict[str, str],
) -> tuple[dict[str, Any] | None, str | None]:
    template = templates.get(source_id)
    if template is None:
        return None, "промпт ноды не найден"
    old_parts = prompt_sections(template, max_parts=OLD_MAX_PARTS)
    new_parts = prompt_sections(template, max_parts=NEW_MAX_PARTS)
    old = next((item for item in old_parts if item[0] == part_key), None)
    if old is None:
        return None, "в старой нарезке max_parts=8 нет такого part_key"
    _old_key, old_body, old_indexes = old
    by_sections = [item for item in new_parts if set(old_indexes) <= set(item[2])]
    by_text = [item for item in new_parts if old_body and old_body in item[1]]
    touched = [item for item in new_parts if set(old_indexes) & set(item[2])]
    if len(touched) > 1:
        return None, "текст старого чанка попал в несколько новых частей"
    if len(by_sections) != 1 or len(by_text) != 1 or by_sections[0][0] != by_text[0][0]:
        return None, "пересечение текста старого чанка с новой нарезкой неоднозначно"
    new_key = by_sections[0][0]
    return {"corpus": "prompts", "source_id": source_id, "part_key": new_key}, None


def _transfer_identity(
    corpus: str,
    source_id: str,
    part_key: str,
    existing: set[tuple[str, str, str]],
) -> tuple[dict[str, Any] | None, str | None]:
    if (corpus, source_id, part_key) not in existing:
        return None, "в новой нарезке нет чанка с тем же ключом"
    return {"corpus": corpus, "source_id": source_id, "part_key": part_key}, None


def transfer_labels(
    queries: list[dict[str, Any]] | None = None,
    *,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Вернуть (перенесённая разметка, список неоднозначных).

    Неоднозначная старая метка не попадает в ``relevant`` нового файла.
    """
    root = repo_root or ROOT
    if queries is None:
        queries = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    templates = _templates(root)
    rules = load_rules(RULES_PATH if root == ROOT else root / "docs" / "fable" / "user_rules.md")
    existing = {("rules", doc.source_id, doc.part_key) for doc in rules}
    for source_id in templates:
        existing.add(("nodes", source_id, "catalog"))
        for part_key, _body, _indexes in prompt_sections(templates[source_id], max_parts=NEW_MAX_PARTS):
            existing.add(("prompts", source_id, part_key))
    rule_source = next((doc.source_id for doc in rules), "")
    transferred_queries: list[dict[str, Any]] = []
    ambiguous: list[dict[str, str]] = []
    for query in queries:
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in query.get("relevant") or []:
            corpus = str(item.get("corpus") or ("rules" if query.get("family") == "rules" else ""))
            source_id = str(item.get("source_id") or (rule_source if corpus == "rules" else ""))
            part_key = str(item.get("part_key") or "")
            old_chunk_id = chunk_id_for(corpus, source_id, part_key) if corpus and source_id and part_key else ""
            if corpus == "prompts":
                target, reason = _transfer_prompt(source_id, part_key, templates)
            elif corpus in {"nodes", "rules"} and source_id and part_key:
                target, reason = _transfer_identity(corpus, source_id, part_key, existing)
            else:
                target, reason = None, "у старой метки не хватает корпуса, source_id или part_key"
            if target is None:
                ambiguous.append(
                    {
                        "query_id": str(query.get("id") or ""),
                        "old_chunk_id": old_chunk_id,
                        "reason": reason or "неоднозначно",
                    }
                )
                continue
            key = (target["corpus"], target["source_id"], target["part_key"])
            slot = grouped.get(key)
            if slot is None:
                grouped[key] = {
                    "corpus": target["corpus"],
                    "source_id": target["source_id"],
                    "part_key": target["part_key"],
                    "chunk_id": chunk_id_for(*key),
                    "grade": 1,
                    "from": [old_chunk_id],
                }
            else:
                slot["grade"] = max(int(slot["grade"]), 1)
                if old_chunk_id not in slot["from"]:
                    slot["from"].append(old_chunk_id)
        transferred_queries.append(
            {
                "id": query.get("id"),
                "family": query.get("family"),
                "text": query.get("text"),
                "relevant": list(grouped.values()),
            }
        )
    transferred = {
        "source": "precision_live_queries.json",
        "old_max_parts": OLD_MAX_PARTS,
        "new_max_parts": NEW_MAX_PARTS,
        "grade": "max grade старых меток, чей текст целиком вошёл в новый чанк; отсутствие метки — не 0, а пропуск",
        "queries": transferred_queries,
    }
    ambiguous_doc = {"count": len(ambiguous), "items": ambiguous}
    return transferred, ambiguous_doc


def write_label_files(repo_root: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    transferred, ambiguous = transfer_labels(repo_root=repo_root)
    TRANSFERRED_PATH.write_text(
        json.dumps(transferred, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    AMBIGUOUS_PATH.write_text(
        json.dumps(ambiguous, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return transferred, ambiguous


def main() -> None:
    transferred, ambiguous = write_label_files()
    labels = sum(len(query["relevant"]) for query in transferred["queries"])
    print(f"transferred_labels={labels} ambiguous={ambiguous['count']}")


if __name__ == "__main__":
    main()
