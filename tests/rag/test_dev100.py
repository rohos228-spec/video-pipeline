"""Dev-100 = прежний dev-70 плюс тридцать реплик. Holdout сюда не входит."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.orchestrator.rag.store import chunk_id_for
from evals.orchestrator.dev_v2 import DEV100_PATH, EXTRA_COUNT, build_dev100_extra, load_dev100
from evals.orchestrator.holdout import dev_plus_ids, is_holdout_id, load_dev_expanded
from tests.rag.test_holdout4_guard import _catalog_identity_phrases
from tests.rag.test_holdout_guard import _jaccard, _stems

DATA = Path("evals/orchestrator/datasets")


def _norm(text: str) -> str:
    folded = text.lower().replace("ё", "е")
    return " ".join(re.sub(r"[^0-9a-zа-я]+", " ", folded).split())


def _holdout_queries() -> list[dict]:
    """Holdout 1 — чётные live. Плюс файлы 2, 3 и 4. Артефакт замера не читается."""
    live = json.loads((DATA / "precision_live_queries.json").read_text(encoding="utf-8"))
    if isinstance(live, dict):
        live = live["queries"]
    holdout1 = [query for query in live if int(str(query["id"]).split("-")[1]) % 2 == 0]
    hold2 = json.loads((DATA / "precision_live_holdout.json").read_text(encoding="utf-8"))["queries"]
    hold3 = json.loads((DATA / "precision_live_holdout3.json").read_text(encoding="utf-8"))["queries"]
    hold4 = json.loads((DATA / "precision_live_holdout4.json").read_text(encoding="utf-8"))["queries"]
    return [*holdout1, *hold2, *hold3, *hold4]


def test_dev70_stays_seventy_and_dev100_appends_thirty() -> None:
    base = load_dev_expanded()
    assert [query["id"] for query in base] == dev_plus_ids()
    assert len(base) == 70
    queries = load_dev100()
    assert len(queries) == 100
    assert queries[:70] == base
    extra = queries[70:]
    assert [query["id"] for query in extra] == [f"d2-{number:02d}" for number in range(1, EXTRA_COUNT + 1)]
    assert all(not is_holdout_id(query["id"]) for query in queries)
    assert build_dev100_extra()["queries"] == json.loads(DEV100_PATH.read_text(encoding="utf-8"))["queries"]


def test_dev100_gold_answers_and_families_weight_the_weak_slices() -> None:
    document = json.loads(DEV100_PATH.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    by_source: dict[str, set[str]] = {}
    from evals.orchestrator.holdout import _index_docs

    for doc in _index_docs():
        by_source.setdefault(doc.source_id, set()).add(doc.part_key)
    for query in document["queries"]:
        counts[query["family"]] = counts.get(query["family"], 0) + 1
        assert query["justification"] and "\n" not in query["justification"]
        got = set()
        for item in query["relevant"]:
            assert item["chunk_id"] == chunk_id_for(item["corpus"], item["source_id"], item["part_key"])
            got.add((item["source_id"], item["part_key"]))
        universe = {
            (source_id, part_key)
            for source_id in {item["source_id"] for item in query["relevant"]}
            for part_key in by_source[source_id]
        }
        assert got < universe
    assert counts["camera"] >= 8
    assert counts["world"] >= 8
    assert counts["characters"] >= 8
    assert sum(counts.values()) == EXTRA_COUNT


def test_dev100_does_not_repeat_holdouts_or_name_nodes() -> None:
    prior = _holdout_queries()
    prior_norm = [_norm(query["text"]) for query in prior]
    prior_stems = [_stems(query["text"]) for query in prior]
    phrases = _catalog_identity_phrases()
    seen: set[str] = set()
    for query in json.loads(DEV100_PATH.read_text(encoding="utf-8"))["queries"]:
        folded = _norm(query["text"])
        assert folded not in seen
        seen.add(folded)
        assert folded not in prior_norm
        nearest = max(_jaccard(_stems(query["text"]), previous) for previous in prior_stems)
        assert nearest < 0.34, (query["id"], nearest)
        text = query["text"].lower().replace("ё", "е")
        leaked = [phrase for phrase in phrases if phrase in text]
        assert not leaked, (query["id"], leaked)
        assert re.search(r"правил\w*\s*\d+", text) is None
