"""Пул holdout-3 читает выгрузку проекта 65, тексты не дописываются."""

from __future__ import annotations

import json

from evals.orchestrator.holdout3 import (
    EXPORT_PATH,
    QUOTA,
    SEED,
    filter_pool,
    load_export_records,
    records_from_export,
    draw,
)


def test_export_loader_uses_stored_texts_and_seed_65_fills_the_quota() -> None:
    data = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    records = records_from_export(data)
    assert records == load_export_records()
    assert records[0]["text"] == data["fragments"]["texts"][0]
    assert records[0]["project_id"] == 65
    assert records[0]["record_type"] == "fragment"
    stored = set(data["fragments"]["texts"])
    stored.update(scene["voiceover_text"] for scene in data["scenes"] if scene.get("voiceover_text"))
    stored.update(frame["voiceover_text"] for frame in data["frames_table"] if frame.get("voiceover_text"))
    assert data["voiceover_script"] not in {row["text"] for row in records}
    kept, report = filter_pool(records)
    assert report["pool"] == 105
    assert report["kept"] == 100
    assert report["rejected"] == {
        "vo_bracket": 0,
        "dataset_frame_id": 0,
        "single_letter": 0,
        "few_content_words": 5,
    }
    drawn = draw(SEED, records, ["rule-1", "rule-9"])
    assert len(drawn) == 24
    assert draw(SEED, records, ["rule-1", "rule-9"]) == drawn
    families = [item["family"] for item in drawn]
    for family, count in QUOTA:
        assert families.count(family) == count
    assert report["kept"] >= 24
    for item in drawn:
        assert item["text"] in stored
        assert item["text"] == kept_text(kept, item)
    assert {row["text"] for row in records} <= stored


def kept_text(kept: list[dict], item: dict) -> str:
    source = item["source"]
    match = next(row for row in kept if row["record_id"] == source["record_id"] and row["record_type"] == source["record_type"])
    return str(match["text"])
