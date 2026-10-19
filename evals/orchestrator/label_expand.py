"""Уточнение к правилу 1396ea43: метки s0–s7 на нарезке max_parts=8.

Правило ``label_transfer.py`` не меняется. Оно по-прежнему переносит метку
только туда, куда текст старого чанка вошёл целиком, и целевая нарезка там — 3.

Здесь целевая нарезка — та же ``max_parts=8``, что и у старой метки.
Для s0–s2 проверяется тот же предикат: секции целиком в одной части, строка
тела в одной части, и это одна и та же часть. На этой нарезке часть совпадает
сама с собой.

s3–s7 этим предикатом не размечаются: текста s0–s2 в них нет. Уточнение:
если у ``source_id`` есть хотя бы одна однозначно прошедшая метка s0–s2,
каждая существующая часть ``split_prompt(..., max_parts=8)`` этого промпта
получает grade = max(grade) таких меток. Части, которой нет в нарезке, нет
и в разметке. Вручную часть не выбирается.

Каталог и правило переносятся тождеством ключа, как в замороженном правиле.
Исходный ``precision_live_queries.json`` не переписывается. live-29 не правится.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.orchestrator.rag.chunking import split_prompt
from app.orchestrator.rag.loaders import load_rules
from app.orchestrator.rag.node_store import NODE_KEY_RE
from app.orchestrator.rag.store import chunk_id_for
from evals.orchestrator.label_transfer import (
    AMBIGUOUS_PATH,
    QUERY_PATH,
    _templates,
    prompt_sections,
)

ROOT = Path(__file__).resolve().parents[2]
EXPANDED_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_labels_s0_s7.json"
RULES_PATH = ROOT / "docs" / "fable" / "user_rules.md"
MAX_PARTS = 8


def _same_cut_prompt(
    source_id: str,
    part_key: str,
    templates: dict[str, str],
) -> tuple[dict[str, str] | None, str | None]:
    """Тот же предикат, что в замороженном правиле, целевая нарезка тоже 8."""
    template = templates.get(source_id)
    if not template:
        return None, "промпт ноды не найден"
    parts = prompt_sections(template, max_parts=MAX_PARTS)
    old = next((item for item in parts if item[0] == part_key), None)
    if old is None:
        return None, "в нарезке max_parts=8 нет такого part_key"
    _key, body, indexes = old
    by_sections = [item for item in parts if set(indexes) <= set(item[2])]
    by_text = [item for item in parts if body and body in item[1]]
    touched = [item for item in parts if set(indexes) & set(item[2])]
    if len(touched) > 1:
        return None, "текст старого чанка попал в несколько новых частей"
    if len(by_sections) != 1 or len(by_text) != 1 or by_sections[0][0] != by_text[0][0]:
        return None, "пересечение текста старого чанка с новой нарезкой неоднозначно"
    return {"corpus": "prompts", "source_id": source_id, "part_key": by_sections[0][0]}, None


def expand_labels(
    queries: list[dict[str, Any]] | None = None,
    *,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Вернуть разметку s0–s7 и только новые неоднозначные строки.

    Старый список из 12 строк эта функция не читает и не переписывает.
    """
    root = repo_root or ROOT
    if queries is None:
        queries = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    templates = {
        key: text
        for key, text in _templates(root).items()
        if NODE_KEY_RE.match(key)
    }
    rules = load_rules(RULES_PATH if root == ROOT else root / "docs" / "fable" / "user_rules.md")
    existing = {("rules", doc.source_id, doc.part_key) for doc in rules}
    for source_id, template in templates.items():
        existing.add(("nodes", source_id, "catalog"))
        for part_key, _body in split_prompt(template, max_parts=MAX_PARTS):
            existing.add(("prompts", source_id, part_key))
    rule_source = next((doc.source_id for doc in rules), "")
    expanded_queries: list[dict[str, Any]] = []
    fresh_ambiguous: list[dict[str, str]] = []
    for query in queries:
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        seeds: dict[str, list[str]] = {}
        for item in query.get("relevant") or []:
            corpus = str(item.get("corpus") or ("rules" if query.get("family") == "rules" else ""))
            source_id = str(item.get("source_id") or (rule_source if corpus == "rules" else ""))
            part_key = str(item.get("part_key") or "")
            old_chunk_id = chunk_id_for(corpus, source_id, part_key) if corpus and source_id and part_key else ""
            if corpus == "prompts":
                target, reason = _same_cut_prompt(source_id, part_key, templates)
            elif corpus in {"nodes", "rules"} and source_id and part_key:
                if (corpus, source_id, part_key) not in existing:
                    target, reason = None, "в новой нарезке нет чанка с тем же ключом"
                else:
                    target, reason = (
                        {"corpus": corpus, "source_id": source_id, "part_key": part_key},
                        None,
                    )
            else:
                target, reason = None, "у старой метки не хватает корпуса, source_id или part_key"
            if target is None:
                fresh_ambiguous.append(
                    {
                        "query_id": str(query.get("id") or ""),
                        "old_chunk_id": old_chunk_id,
                        "reason": reason or "неоднозначно",
                    }
                )
                continue
            key = (target["corpus"], target["source_id"], target["part_key"])
            _store(grouped, key, old_chunk_id)
            if target["corpus"] == "prompts":
                seeds.setdefault(target["source_id"], [])
                if old_chunk_id not in seeds[target["source_id"]]:
                    seeds[target["source_id"]].append(old_chunk_id)
        for source_id, old_ids in seeds.items():
            template = templates.get(source_id) or ""
            for part_key, _body in split_prompt(template, max_parts=MAX_PARTS):
                key = ("prompts", source_id, part_key)
                if key in grouped:
                    continue
                _store(grouped, key, old_ids[0])
                slot = grouped[key]
                for old_id in old_ids:
                    if old_id not in slot["from"]:
                        slot["from"].append(old_id)
        expanded_queries.append(
            {
                "id": query.get("id"),
                "family": query.get("family"),
                "text": query.get("text"),
                "relevant": list(grouped.values()),
            }
        )
    document = {
        "source": "precision_live_queries.json",
        "max_parts": MAX_PARTS,
        "base_rule": "1396ea43",
        "clarification": (
            "s0–s2, каталог и правила — тот же предикат вхождения текста, "
            "целевая нарезка max_parts=8. s3–s7 — все существующие части промпта, "
            "у которого хотя бы одна метка s0–s2 прошла этот предикат; "
            "grade = max(grade) таких меток."
        ),
        "queries": expanded_queries,
    }
    return document, fresh_ambiguous


def _store(
    grouped: dict[tuple[str, str, str], dict[str, Any]],
    key: tuple[str, str, str],
    old_chunk_id: str,
) -> None:
    slot = grouped.get(key)
    if slot is None:
        grouped[key] = {
            "corpus": key[0],
            "source_id": key[1],
            "part_key": key[2],
            "chunk_id": chunk_id_for(*key),
            "grade": 1,
            "from": [old_chunk_id] if old_chunk_id else [],
        }
        return
    slot["grade"] = max(int(slot["grade"]), 1)
    if old_chunk_id and old_chunk_id not in slot["from"]:
        slot["from"].append(old_chunk_id)


def write_expanded_file(repo_root: Path | None = None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Не переписывает замороженные файлы.

    Новые неоднозначные строки идут в ``precision_live_label_ambiguous_new.json``.
    """
    from evals.orchestrator.frozen_datasets import assert_frozen

    document, fresh = expand_labels(repo_root=repo_root)
    if EXPANDED_PATH.exists():
        assert_frozen(EXPANDED_PATH)
    else:
        EXPANDED_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if fresh:
        new_path = AMBIGUOUS_PATH.with_name("precision_live_label_ambiguous_new.json")
        new_path.write_text(
            json.dumps({"count": len(fresh), "items": fresh}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return document, fresh


def main() -> None:
    document, fresh = write_expanded_file()
    labels = sum(len(query["relevant"]) for query in document["queries"])
    print(f"expanded_labels={labels} new_ambiguous={len(fresh)}")


if __name__ == "__main__":
    main()
