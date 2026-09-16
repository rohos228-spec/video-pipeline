"""rewrite / rebuild / manual=1 / input_hash."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select, text

from app.models import Frame
from app.services.scene_space.rewrite import (
    coverage_dialogue,
    isolated_session,
    load_fixture,
    rebuild,
    rewrite,
    seed_fixture,
    _list_frame_spaces,
)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "rewrite.db"


async def test_seed_dialogue_coverage(db_path):
    async with isolated_session(db_path) as session:
        result = await seed_fixture(session, load_fixture("fix:dialogue"))
        assert result["seeded"] is True
        rows = await _list_frame_spaces(session, "fix:dialogue")
        assert 8 <= len(rows) <= 12
        fx = load_fixture("fix:dialogue")
        accents = [fr["attrs"]["accent"] for fr in fx["frames"]]
        assert coverage_dialogue(rows, accents) == []
        assert any(int(r["manual"]) == 1 for r in rows)


async def test_rewrite_idempotent_and_manual_preserved(db_path):
    meaning = "A wins then loses the folder"
    async with isolated_session(db_path) as session:
        await seed_fixture(session, load_fixture("fix:dialogue"))
        rows = await _list_frame_spaces(session, "fix:dialogue")
        manual = next(r for r in rows if int(r["manual"]) == 1)
        original_size = manual["shot_size"]
        await session.execute(
            text(
                "UPDATE frames_space SET shot_size = :sz WHERE uuid = :u"
            ),
            {"sz": "EWS", "u": manual["uuid"]},
        )
        await session.flush()

        first = await rewrite(session, "fix:dialogue", meaning)
        assert manual["uuid"] in first["changed"]["skipped"]
        assert manual["uuid"] not in first["changed"]["updated"]
        assert manual["uuid"] not in first["changed"]["inserted"]

        after = await _list_frame_spaces(session, "fix:dialogue")
        still = next(r for r in after if r["uuid"] == manual["uuid"])
        assert still["shot_size"] == "EWS"
        assert still["shot_size"] != original_size or original_size == "EWS"

        second = await rewrite(session, "fix:dialogue", meaning)
        assert second["changed"] == {"inserted": [], "updated": [], "skipped": []}

        third = await rewrite(session, "fix:dialogue", meaning)
        assert third["changed"]["inserted"] == []
        assert third["changed"]["updated"] == []


async def test_rebuild_idempotent(db_path):
    async with isolated_session(db_path) as session:
        await seed_fixture(session, load_fixture("fix:dialogue"))
        a = await rebuild(session, "fix:dialogue")
        b = await rebuild(session, "fix:dialogue")
        assert b["changed"] == {"inserted": [], "updated": [], "skipped": []}
        _ = a


async def test_seed_does_not_touch_missing_twice(db_path):
    async with isolated_session(db_path) as session:
        first = await seed_fixture(session, load_fixture("fix:cross"))
        second = await seed_fixture(session, load_fixture("fix:cross"))
        assert first["seeded"] is True
        assert second["seeded"] is False
        frames = (
            await session.execute(select(Frame).where(Frame.uuid.like("c205%")))
        ).scalars().all()
        assert 8 <= len(frames) <= 12
        assert all(len(fr.uuid or "") == 24 for fr in frames)
        assert all("PROMPT:" in (fr.image_prompt or "") for fr in frames)
        assert all("NEGATIVE PROMPT:" in (fr.image_prompt or "") for fr in frames)


async def test_fixture_json_roundtrip_hash_stable(db_path):
    fx = load_fixture("fix:turn")
    raw = json.dumps(fx["frames"][6]["space"]["space_delta_json"])
    assert "turn" in raw
    async with isolated_session(db_path) as session:
        await seed_fixture(session, fx)
        rows = await _list_frame_spaces(session, "fix:turn")
        smash_or_turn = False
        for r in rows:
            delta = r["space_delta_json"]
            if isinstance(delta, str):
                delta = json.loads(delta)
            if delta.get("turn"):
                smash_or_turn = True
        assert smash_or_turn
