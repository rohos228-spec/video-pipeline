"""Holdout read-only, и инструменты подбора его не открывают."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.orchestrator.rag.lexical import index_forms
from app.orchestrator.rag.store import chunk_id_for
from evals.orchestrator.frozen_datasets import FROZEN_SHA256, assert_frozen, file_sha256
from evals.orchestrator.holdout import (
    FAMILY_NODES,
    HOLDOUT_PATH,
    HoldoutLeakError,
    build_holdout_document,
    dev_plus_ids,
    read_for_tuning,
    require_dev_plus,
    write_holdout_file,
)
from evals.orchestrator.precision_live import (
    SelectionLeakError,
    dev_only_queries,
    require_dev_only,
    select_on_dev,
)

_STOP = frozenset(
    {
        "это",
        "как",
        "где",
        "кто",
        "для",
        "или",
        "при",
        "что",
        "она",
        "они",
        "все",
        "уже",
        "нет",
        "про",
        "из",
        "от",
        "до",
        "по",
        "на",
        "со",
        "без",
        "если",
        "когда",
        "чем",
        "только",
        "можно",
        "нужно",
        "надо",
        "этот",
        "эта",
        "эти",
        "тот",
        "его",
        "её",
        "их",
        "под",
        "над",
        "при",
        "ли",
        "бы",
        "же",
        "не",
        "да",
        "так",
        "ещё",
        "еще",
        "один",
        "одна",
        "два",
        "три",
        "весь",
        "вся",
        "после",
        "перед",
        "между",
        "внутри",
        "потом",
        "затем",
        "чтобы",
        "который",
        "которая",
        "которые",
    }
)


def _stems(text: str) -> set[str]:
    found: set[str] = set()
    for raw in text.replace("—", " ").replace("–", " ").split():
        token = "".join(char for char in raw.lower().replace("ё", "е") if char.isalpha())
        if len(token) < 3 or token in _STOP:
            continue
        found.update(form for form in index_forms(token) if form not in _STOP)
    return found


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def test_holdout_is_frozen_and_matches_real_index_chunks() -> None:
    assert FROZEN_SHA256["precision_live_holdout.json"]["commit"]
    assert file_sha256(HOLDOUT_PATH) == FROZEN_SHA256[HOLDOUT_PATH.name]["sha256"]
    assert_frozen(HOLDOUT_PATH)
    document = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
    built = build_holdout_document()
    assert built["queries"] == document["queries"]
    queries = document["queries"]
    assert [query["id"] for query in queries] == [f"hold-{number:02d}" for number in range(1, 21)]
    families = [query["family"] for query in queries]
    assert families.count("assemble") >= 5
    assert families.count("world") >= 5
    others = {family for family in families if family not in {"assemble", "world"}}
    assert others >= {"camera", "characters", "action", "shots", "script", "rules"}
    for query in queries:
        assert query["relevant"]
        for item in query["relevant"]:
            assert item["grade"] == 1
            assert item["chunk_id"] == chunk_id_for(item["corpus"], item["source_id"], item["part_key"])
        if query["family"] == "rules":
            assert {item["part_key"] for item in query["relevant"]} == {"rule-9"}
        else:
            assert {item["source_id"] for item in query["relevant"]} == set(FAMILY_NODES[query["family"]])


def test_holdout_texts_are_not_paraphrases_of_the_fifty() -> None:
    old = json.loads(Path("evals/orchestrator/datasets/precision_live_queries.json").read_text(encoding="utf-8"))
    holdout = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))["queries"]
    old_texts = [query["text"] for query in old]
    old_stems = [_stems(text) for text in old_texts]
    for query in holdout:
        assert query["text"] not in old_texts
        stems = _stems(query["text"])
        nearest = max(_jaccard(stems, previous) for previous in old_stems)
        assert nearest < 0.34, (query["id"], nearest)


def test_rewrite_of_existing_holdout_is_refused() -> None:
    before = HOLDOUT_PATH.read_bytes()
    write_holdout_file()
    assert HOLDOUT_PATH.read_bytes() == before
    with pytest.raises(Exception):
        write_holdout_file(rewrite=True)
    assert HOLDOUT_PATH.read_bytes() == before


def test_tuning_reader_rejects_holdout_and_accepts_dev_plus() -> None:
    with pytest.raises(HoldoutLeakError):
        read_for_tuning(HOLDOUT_PATH)
    with pytest.raises(HoldoutLeakError):
        require_dev_plus(["live-01", "hold-01"])
    with pytest.raises(SelectionLeakError):
        require_dev_only(["live-02", "hold-03"])
    accepted = require_dev_only(["live-02", "live-50", "live-29"])
    assert accepted == ["live-02", "live-50", "live-29"]
    assert "live-02" in dev_plus_ids()
    chosen = dev_only_queries(
        [
            {"id": "live-02", "text": "бывший test"},
            {"id": "live-01", "text": "dev"},
        ]
    )
    assert [query["id"] for query in chosen] == ["live-02", "live-01"]
    with pytest.raises(HoldoutLeakError):
        dev_only_queries(
            [
                {"id": "live-01", "text": "dev"},
                {"id": "hold-01", "text": "отложенный"},
            ]
        )
    key, _why = select_on_dev(
        [("fit", "fit", {"ndcg_at_8": 0.7, "hit_at_5": 0.95, "p95_s": 0.2})],
        ["live-02", "live-49"],
    )
    assert key == "fit"


def test_tuning_entry_points_read_through_the_guard() -> None:
    source = Path("evals/orchestrator/precision_live.py").read_text(encoding="utf-8")
    for name in ("def _main_grid", "def run_profile_pair", "def run_abc", "def run_tune"):
        start = source.index(name)
        nxt = source.find("\ndef ", start + 1)
        body = source[start:nxt]
        assert "read_for_tuning" in body
        assert "precision_live_holdout" not in body
        assert "HOLDOUT_PATH" not in body
