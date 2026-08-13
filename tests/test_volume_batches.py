"""volume_batches: добор / нарезка ~80% после частичного ответа."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.volume_batches import (
    inferred_batch_size,
    load_expected_frame_uuids,
    merge_apply_ops_payloads,
    missing_frame_uuids_for_volume,
    plan_remainder_batches,
    rechunk_tail,
    volume_complete_apply_ops_reply,
)


def test_inferred_batch_size_80pct() -> None:
    assert inferred_batch_size(10) == 8
    assert inferred_batch_size(25) == 20
    assert inferred_batch_size(1) == 1
    assert inferred_batch_size(0) == 1


def test_plan_remainder_smaller_than_delivered_one_batch() -> None:
    # выдали 15, осталось 10 → один добор
    parts = plan_remainder_batches(list(range(10)), delivered=15)
    assert parts == [list(range(10))]


def test_plan_remainder_larger_splits_at_80pct() -> None:
    # выдали 10, осталось 25 → пачки по 8
    parts = plan_remainder_batches(list(range(25)), delivered=10)
    assert [len(p) for p in parts] == [8, 8, 8, 1]
    assert [x for p in parts for x in p] == list(range(25))


def test_plan_remainder_empty() -> None:
    assert plan_remainder_batches([], delivered=5) == []


def test_rechunk_tail() -> None:
    assert rechunk_tail(list(range(10)), size=4) == [
        [0, 1, 2, 3],
        [4, 5, 6, 7],
        [8, 9],
    ]


def test_load_expected_frame_uuids(tmp_path: Path) -> None:
    path = tmp_path / "db_frames_b01.json"
    path.write_text(
        json.dumps(
            {
                "frames": [
                    {"uuid": "aaa", "number": 1},
                    {"uuid": "bbb", "number": 2},
                    {"uuid": "aaa", "number": 1},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert load_expected_frame_uuids([path]) == ["aaa", "bbb"]


def test_missing_frame_uuids_partial_ops() -> None:
    text = json.dumps(
        {
            "ops": [
                {"frame_uuid": "a", "fields": {"место": "1"}},
                {"frame_uuid": "b", "fields": {"место": "2"}},
            ]
        }
    )
    missing, delivered, data = missing_frame_uuids_for_volume(
        text, ["a", "b", "c", "d"]
    )
    assert delivered == 2
    assert missing == ["c", "d"]
    assert data is not None


def test_missing_skips_scenes_only_payload() -> None:
    text = json.dumps(
        {"characters": [{"id": "c01"}], "scenes": [{"id_scene": "scene_01"}], "ops": []}
    )
    missing, delivered, _ = missing_frame_uuids_for_volume(text, ["a", "b"])
    assert missing == []
    assert delivered == 0


def test_merge_apply_ops_payloads() -> None:
    merged = merge_apply_ops_payloads(
        {"ops": [{"frame_uuid": "a", "fields": {"x": "1"}}], "characters": [{"id": "c"}]},
        {"ops": [{"frame_uuid": "b", "fields": {"x": "2"}}]},
        {"ops": [{"frame_uuid": "a", "fields": {"x": "9"}}]},
    )
    ops = {o["frame_uuid"]: o["fields"]["x"] for o in merged["ops"]}
    assert ops == {"a": "9", "b": "2"}
    assert merged["characters"][0]["id"] == "c"


@pytest.mark.asyncio
async def test_volume_complete_apply_ops_reply(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "db_frames.json"
    uuids = [f"{i:024x}" for i in range(5)]
    db.write_text(
        json.dumps({"frames": [{"uuid": u, "number": i + 1} for i, u in enumerate(uuids)]}),
        encoding="utf-8",
    )
    partial = {
        "ops": [
            {"frame_uuid": uuids[0], "fields": {"место": "a"}},
            {"frame_uuid": uuids[1], "fields": {"место": "b"}},
        ]
    }
    calls: list[str] = []

    async def fake_chat(**kw):
        calls.append(kw.get("prompt") or "")
        assert kw.get("volume_complete") is False
        # добор на 3 оставшихся
        miss = [u for u in uuids if u not in {uuids[0], uuids[1]}]
        return SimpleNamespace(
            text=json.dumps(
                {
                    "ops": [
                        {"frame_uuid": u, "fields": {"место": "x"}} for u in miss
                    ]
                }
            )
        )

    monkeypatch.setattr("app.services.gpt_api.chat", fake_chat)

    text, did = await volume_complete_apply_ops_reply(
        json.dumps(partial),
        input_paths=[db],
        prompt="fill",
        accompanying="",
    )
    assert did is True
    assert len(calls) >= 1
    data = json.loads(text)
    got = {o["frame_uuid"] for o in data["ops"]}
    assert got == set(uuids)
