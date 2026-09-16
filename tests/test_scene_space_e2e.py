"""Fixture e2e: three scenes, 2.9 coverage, rewrite/rebuild twice, isolated sqlite."""

from __future__ import annotations

import json
from app.services.scene_space.rewrite import (
    FIXTURE_DIR,
    FIXTURE_IDS,
    coverage_cross,
    coverage_dialogue,
    coverage_turn,
    fixture_frame_counts,
    isolated_session,
    load_fixture,
    missing_modules,
    run_e2e,
    seed_all_fixtures,
    _list_frame_spaces,
)


def test_fixture_files_exist_with_frame_counts():
    counts = fixture_frame_counts()
    for sid in FIXTURE_IDS:
        name = {"fix:dialogue": "dialogue.json", "fix:cross": "cross.json", "fix:turn": "turn.json"}[sid]
        path = FIXTURE_DIR / name
        assert path.is_file(), path
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["scene_id"] == sid
        n = len(data["frames"])
        assert 8 <= n <= 12, (sid, n)
        assert counts[sid] == n
        uuids = [fr["uuid"] for fr in data["frames"]]
        assert len(set(uuids)) == n
        accents = [fr["attrs"]["accent"] for fr in data["frames"]]
        assert len(set(accents)) == n
        for fr in data["frames"]:
            assert len(fr["uuid"]) == 24
            assert all(c in "0123456789abcdef" for c in fr["uuid"])
            assert "PROMPT:" in fr["image_prompt"]
            assert "NEGATIVE PROMPT:" in fr["image_prompt"]
            assert "давайте разберёмся" not in fr["image_prompt"].lower()
            space = fr["space"]
            assert space["shot_size"] in {"EWS", "WS", "FS", "MWS", "MS", "MCU", "CU", "ECU"}
            assert space["axis_pair"] and "|" in space["axis_pair"]
            assert space["axis_side"] in {"A", "B"}
            lo, hi = space["axis_pair"].split("|")
            assert lo < hi


def test_dialogue_fixture_two_shot_coverage():
    data = load_fixture("fix:dialogue")
    rows = [{"uuid": fr["uuid"], **fr["space"]} for fr in data["frames"]]
    accents = [fr["attrs"]["accent"] for fr in data["frames"]]
    assert coverage_dialogue(rows, accents) == []
    assert data["value_charge"] == {"start": "+", "end": "-"}


def test_cross_fixture_move_reestablish_method():
    data = load_fixture("fix:cross")
    rows = [{"uuid": fr["uuid"], **fr["space"]} for fr in data["frames"]]
    assert coverage_cross(rows) == []


def test_turn_fixture_physical_turn_and_smash():
    data = load_fixture("fix:turn")
    rows = [{"uuid": fr["uuid"], **fr["space"]} for fr in data["frames"]]
    assert coverage_turn(rows) == []


async def test_e2e_seed_rewrite_rebuild_isolated(tmp_path):
    db = tmp_path / "e2e.db"
    async with isolated_session(db) as session:
        report = await run_e2e(session)
    assert report["frame_counts"]["fix:dialogue"] >= 8
    assert report["frame_counts"]["fix:cross"] >= 8
    assert report["frame_counts"]["fix:turn"] >= 8
    assert report["coverage"]["fix:dialogue"] == []
    assert report["coverage"]["fix:cross"] == []
    assert report["coverage"]["fix:turn"] == []
    assert report["rewrite_second"]["changed"]["inserted"] == []
    assert report["rewrite_second"]["changed"]["updated"] == []
    assert report["rebuild_second"]["changed"]["inserted"] == []
    assert report["rebuild_second"]["changed"]["updated"] == []
    assert "data/state.db" not in str(db)
    missing = missing_modules()
    assert isinstance(missing, dict)


async def test_seed_all_three_projects(tmp_path):
    async with isolated_session(tmp_path / "all.db") as session:
        seeds = await seed_all_fixtures(session)
        for sid in FIXTURE_IDS:
            rows = await _list_frame_spaces(session, sid)
            assert 8 <= len(rows) <= 12
            assert seeds[sid]["frames"] == len(rows)
