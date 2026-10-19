"""Holdout-4 заморожен и закрыт для тюнинга. Retrieve в тесте нет."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.orchestrator.rag.loaders import group_role_index
from app.orchestrator.rag.store import chunk_id_for
from evals.orchestrator.frozen_datasets import FROZEN_SHA256, file_sha256
from evals.orchestrator.holdout import HOLDOUT4_NAME, HoldoutLeakError, _index_docs, is_holdout_id, read_for_tuning
from evals.orchestrator.holdout4 import (
    HOLDOUT4_PATH,
    MIN_QUERIES,
    build_holdout4_document,
    load_holdout4_queries,
    write_holdout4_file,
)
from tests.rag.test_holdout_guard import _jaccard, _stems

DATA = Path("evals/orchestrator/datasets")


def _norm(text: str) -> str:
    folded = text.lower().replace("ё", "е")
    return " ".join(re.sub(r"[^0-9a-zа-я]+", " ", folded).split())


def _prior_queries() -> list[dict]:
    live = json.loads((DATA / "precision_live_queries.json").read_text(encoding="utf-8"))
    if isinstance(live, dict):
        live = live["queries"]
    hold2 = json.loads((DATA / "precision_live_holdout.json").read_text(encoding="utf-8"))["queries"]
    hold3 = json.loads((DATA / "precision_live_holdout3.json").read_text(encoding="utf-8"))["queries"]
    return [*live, *hold2, *hold3]


def _catalog_identity_phrases() -> set[str]:
    """Id и имена нод и групп из каталога. Обычные русские слова задач сюда не входят."""
    phrases: set[str] = set()
    for doc in _index_docs():
        if doc.corpus != "nodes":
            continue
        phrases.add(doc.source_id.lower())
        phrases.add(doc.source_id.replace("_", "").lower())
        title = (doc.title or "").strip().lower().replace("ё", "е")
        if title:
            phrases.add(title)
        meta = doc.metadata or {}
        for key in ("node_key", "group_key"):
            value = str(meta.get(key) or "").strip().lower()
            if value:
                phrases.add(value)
    for role in group_role_index().values():
        for key in ("group_id", "group_title"):
            value = str(role.get(key) or "").strip().lower().replace("ё", "е")
            if value:
                phrases.add(value)
        local = str(role.get("local_key") or "").strip().lower()
        if len(local) >= 4:
            phrases.add(local)
    phrases.update({"chrono_dyn", "веер", "fanout"})
    return {phrase for phrase in phrases if phrase}


def test_holdout4_is_frozen_and_tuning_cannot_load_it() -> None:
    pin = FROZEN_SHA256[HOLDOUT4_NAME]
    assert file_sha256(HOLDOUT4_PATH) == pin["sha256"]
    with pytest.raises(HoldoutLeakError):
        read_for_tuning(HOLDOUT4_PATH)
    with pytest.raises(HoldoutLeakError):
        read_for_tuning(Path("/tmp") / HOLDOUT4_NAME)
    source = Path("evals/orchestrator/precision_live.py").read_text(encoding="utf-8")
    tune = source[source.index("def run_tune"): source.index("def contour_c_acceptance")]
    assert "load_holdout4" not in tune
    assert "holdout4" not in tune
    assert "precision_live_holdout4" not in tune
    queries = load_holdout4_queries()
    assert len(queries) >= MIN_QUERIES
    assert [query["id"] for query in queries] == [f"h4-{number:02d}" for number in range(1, len(queries) + 1)]
    assert is_holdout_id("h4-01") is True
    assert build_holdout4_document()["queries"] == queries
    before = HOLDOUT4_PATH.read_bytes()
    write_holdout4_file()
    assert HOLDOUT4_PATH.read_bytes() == before
    with pytest.raises(Exception):
        write_holdout4_file(rewrite=True)


def test_holdout4_gold_is_the_answering_chunks_only() -> None:
    document = json.loads(HOLDOUT4_PATH.read_text(encoding="utf-8"))
    by_source: dict[str, set[str]] = {}
    for doc in _index_docs():
        by_source.setdefault(doc.source_id, set()).add(doc.part_key)
    counts: dict[str, int] = {}
    kinds: set[str] = set()
    for query in document["queries"]:
        counts[query["family"]] = counts.get(query["family"], 0) + 1
        kinds.add(query["kind"])
        justification = query["justification"]
        assert justification.strip() == justification
        assert justification and "\n" not in justification
        assert query["method"]
        assert query["source"]
        assert query["relevant"]
        got = set()
        for item in query["relevant"]:
            assert item["chunk_id"] == chunk_id_for(item["corpus"], item["source_id"], item["part_key"])
            assert item["grade"] == 1
            got.add((item["source_id"], item["part_key"]))
        universe = {
            (source_id, part_key)
            for source_id in {item["source_id"] for item in query["relevant"]}
            for part_key in by_source[source_id]
        }
        assert got < universe
        if query["family"] == "rules":
            assert query["kind"] == "rule"
            assert len(query["relevant"]) == 1
            assert query["relevant"][0]["part_key"].startswith("rule-")
    assert kinds == {"howto", "prompt", "rule"}
    assert sum(counts.values()) == len(document["queries"])
    assert len(counts) >= 2
    assert max(counts.values()) != min(counts.values())


def test_holdout4_texts_do_not_repeat_dev70_or_earlier_holdouts() -> None:
    """Dev-70 — live-01..50 (в том числе бывший чётный test) и hold-01..20. Плюс holdout-3."""
    prior = _prior_queries()
    prior_norm = [_norm(query["text"]) for query in prior]
    prior_stems = [_stems(query["text"]) for query in prior]
    fresh = json.loads(HOLDOUT4_PATH.read_text(encoding="utf-8"))["queries"]
    seen: set[str] = set()
    for query in fresh:
        folded = _norm(query["text"])
        assert folded not in seen
        seen.add(folded)
        assert folded not in prior_norm
        stems = _stems(query["text"])
        nearest = max(_jaccard(stems, previous) for previous in prior_stems)
        assert nearest < 0.34, (query["id"], nearest)


def test_holdout4_queries_do_not_name_catalog_nodes() -> None:
    phrases = _catalog_identity_phrases()
    queries = json.loads(HOLDOUT4_PATH.read_text(encoding="utf-8"))["queries"]
    for query in queries:
        text = query["text"].lower().replace("ё", "е")
        leaked = [phrase for phrase in phrases if phrase in text]
        assert not leaked, (query["id"], leaked)
        assert re.search(r"правил\w*\s*\d+", text) is None
        assert re.search(r"rule\s*-?\s*\d+", text) is None
        assert "в чате студии" not in text
