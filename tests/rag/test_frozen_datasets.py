"""Замороженные датасеты не переписываются без явного флага."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evals.orchestrator.frozen_datasets import FROZEN_SHA256, FrozenDatasetError, assert_frozen, file_sha256
from evals.orchestrator.label_expand import AMBIGUOUS_PATH, EXPANDED_PATH, write_expanded_file
from evals.orchestrator.label_transfer import TRANSFERRED_PATH, write_label_files
from evals.orchestrator.query_split import SPLIT_PATH, write_split_file

DATA = Path("evals/orchestrator/datasets")


def test_pins_match_files_and_cite_the_freeze_commits() -> None:
    assert FROZEN_SHA256["precision_live_queries.json"]["commit"] == "a79f70a9"
    assert FROZEN_SHA256["precision_live_split.json"]["commit"] == "a79f70a9"
    assert FROZEN_SHA256["precision_live_labels_transferred.json"]["commit"] == "1396ea43"
    assert FROZEN_SHA256["precision_live_label_ambiguous.json"]["commit"] == "1396ea43"
    assert FROZEN_SHA256["precision_live_labels_s0_s7.json"]["commit"] == "19d050c2"
    assert FROZEN_SHA256["precision_live_holdout.json"]["commit"] == "5d1fa048"
    assert FROZEN_SHA256["precision_live_holdout3.json"]["commit"] == "3faf1a5d"
    assert FROZEN_SHA256["precision_live_holdout.json"]["sha256"] == file_sha256(
        DATA / "precision_live_holdout.json"
    )
    for name, pin in FROZEN_SHA256.items():
        assert file_sha256(DATA / name) == pin["sha256"]
        assert_frozen(DATA / name)


def test_crlf_and_lf_share_the_normalized_pin(tmp_path: Path) -> None:
    name = "precision_live_queries.json"
    original = (DATA / name).read_bytes().replace(b"\r\n", b"\n")
    lf_path = tmp_path / name
    crlf_path = tmp_path / "crlf" / name
    crlf_path.parent.mkdir()
    lf_path.write_bytes(original)
    crlf_path.write_bytes(original.replace(b"\n", b"\r\n"))
    pin = FROZEN_SHA256[name]["sha256"]
    assert file_sha256(lf_path) == pin
    assert file_sha256(crlf_path) == pin
    assert hashlib.sha256(crlf_path.read_bytes()).hexdigest() != pin
    assert_frozen(lf_path)
    assert_frozen(crlf_path)
    attributes = Path(".gitattributes").read_text(encoding="utf-8")
    assert "evals/orchestrator/datasets/*.json -text" in attributes


def test_mismatched_hash_raises(tmp_path: Path) -> None:
    path = tmp_path / "precision_live_queries.json"
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(FrozenDatasetError):
        assert_frozen(path)


def test_expand_does_not_append_to_the_frozen_ambiguous_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frozen = tmp_path / "precision_live_label_ambiguous.json"
    expanded = tmp_path / "precision_live_labels_s0_s7.json"
    frozen.write_bytes((DATA / frozen.name).read_bytes())
    expanded.write_bytes((DATA / expanded.name).read_bytes())
    monkeypatch.setattr("evals.orchestrator.label_expand.AMBIGUOUS_PATH", frozen)
    monkeypatch.setattr("evals.orchestrator.label_expand.EXPANDED_PATH", expanded)
    monkeypatch.setattr(
        "evals.orchestrator.label_expand.expand_labels",
        lambda **_kwargs: ({"queries": []}, [{"query_id": "live-99", "old_chunk_id": "abc", "reason": "new"}]),
    )
    before = frozen.read_bytes()
    write_expanded_file()
    assert frozen.read_bytes() == before
    fresh = json.loads((tmp_path / "precision_live_label_ambiguous_new.json").read_text(encoding="utf-8"))
    assert fresh["count"] == 1
    assert fresh["items"][0]["query_id"] == "live-99"
    assert file_sha256(DATA / "precision_live_label_ambiguous.json") == FROZEN_SHA256[AMBIGUOUS_PATH.name]["sha256"]
    assert file_sha256(DATA / "precision_live_labels_s0_s7.json") == FROZEN_SHA256[EXPANDED_PATH.name]["sha256"]


def test_transfer_and_split_do_not_rewrite_existing_files() -> None:
    before_transfer = TRANSFERRED_PATH.read_bytes()
    before_ambiguous = AMBIGUOUS_PATH.read_bytes()
    before_split = SPLIT_PATH.read_bytes()
    write_label_files()
    write_split_file()
    assert TRANSFERRED_PATH.read_bytes() == before_transfer
    assert AMBIGUOUS_PATH.read_bytes() == before_ambiguous
    assert SPLIT_PATH.read_bytes() == before_split


def test_query_rewrite_requires_an_explicit_flag() -> None:
    source = Path("evals/orchestrator/precision_live.py").read_text(encoding="utf-8")
    start = source.index("def main")
    body = source[start:]
    assert "--write-queries" in body
    assert "if args.write_queries:" in body
    assert "assert_frozen(QUERY_PATH)" in body
