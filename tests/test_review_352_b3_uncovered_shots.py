"""П.3 ревью #352: места без покрытия в #354/#365/#368 — шоты не вырезаются."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.scene_plan import _renumber_scene
from app.services.scene_shot_grammar import ensure_wide_before_dialogue
from app.services.vo_shot_expand import (
    apply_shot_voiceover_to_cells,
    strip_non_scene_kadry,
)


def test_strip_non_scene_kadry_archives_and_keeps_list() -> None:
    stub = SimpleNamespace(attrs={"кадры": [{"id": "B01-K1", "действие": "ищут"}]})
    assert strip_non_scene_kadry(stub) is True
    assert stub.attrs.get("кадры") == []
    archived = stub.attrs.get("кадры_archived") or []
    assert [row["id"] for row in archived] == ["B01-K1"]
    assert strip_non_scene_kadry(stub) is False
    assert [row["id"] for row in stub.attrs["кадры_archived"]] == ["B01-K1"]


def test_apply_voiceover_keeps_qc_pieces_when_ladder_is_longer() -> None:
    pu = "cc" * 12
    full = "Первая фраза целиком. Вторая тоже на месте. Третья остаётся в лестнице."
    kadry = [
        {"id": "1-K1", "закадр": "Первая фраза целиком."},
        {"id": "1-K2", "закадр": "Вторая тоже на месте."},
        {"id": "1-K3", "закадр": "Третья остаётся в лестнице."},
    ]
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=full,
        attrs={
            "кадры": [dict(row) for row in kadry],
            "vo_cell_full": full,
            "camera_subdivide": {"role": "vo_parent", "parent_uuid": pu},
        },
    )
    child = SimpleNamespace(
        uuid="dd" * 12,
        voiceover_text="",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": pu}},
    )
    apply_shot_voiceover_to_cells([parent, child])
    pieces = [row.get("закадр") for row in parent.attrs["кадры"]]
    assert pieces == [row["закадр"] for row in kadry]


def test_ensure_wide_keeps_ids_and_clears_insert_prompts() -> None:
    shots = [
        {
            "id": "1-S1-K1",
            "говорит": "Аня",
            "реплика": "да",
            "план": "КРУПНЫЙ",
            "image_prompt": "keep-a",
            "parent_id": None,
        },
        {
            "id": "1-S1-K2",
            "говорит": "Боря",
            "реплика": "нет",
            "план": "КРУПНЫЙ",
            "image_prompt": "keep-b",
            "parent_id": "1-S1-K1",
        },
    ]
    notes = ensure_wide_before_dialogue(shots)
    assert notes
    by_id = {shot["id"]: shot for shot in shots}
    assert "1-S1-K1" in by_id and "1-S1-K2" in by_id
    assert by_id["1-S1-K1"]["image_prompt"] == "keep-a"
    assert by_id["1-S1-K2"]["parent_id"] == "1-S1-K1"
    inserted = [shot for shot in shots if shot["id"] not in {"1-S1-K1", "1-S1-K2"}]
    assert len(inserted) == 1
    assert not inserted[0].get("image_prompt")
    assert inserted[0]["id"] not in {"1-S1-K1", "1-S1-K2"}


def test_renumber_scene_does_not_rewrite_unique_ids() -> None:
    shots = [
        {"id": "1-S1-K2", "parent_id": None, "image_prompt": "a"},
        {"id": "1-S1-K5", "parent_id": "1-S1-K2", "image_prompt": "b"},
    ]
    _renumber_scene(shots, "1-S1")
    assert shots[0]["id"] == "1-S1-K2"
    assert shots[1]["id"] == "1-S1-K5"
    assert shots[1]["parent_id"] == "1-S1-K2"
    dup = [{"id": "1-S1-K1"}, {"id": "1-S1-K1", "image_prompt": "copied"}]
    _renumber_scene(dup, "1-S1")
    assert dup[0]["id"] == "1-S1-K1"
    assert dup[1]["id"] != "1-S1-K1"


def test_promote_merges_nonempty_child_ladder_by_id() -> None:
    from app.services.montage_board_frames import _promote_first_child

    parent = SimpleNamespace(
        number=1,
        uuid="p" * 12,
        sort_key=1.0,
        voiceover_text="голова",
        attrs={
            "кадры": [
                {"id": "1-K1", "план": "ОБЩИЙ"},
                {"id": "1-K2", "план": "СРЕДНИЙ"},
            ]
        },
    )
    child = SimpleNamespace(
        number=2,
        uuid="c" * 12,
        sort_key=2.0,
        voiceover_text="",
        attrs={"кадры": [{"id": "1-K2", "план": "КРУПНЫЙ"}]},
    )
    head = _promote_first_child(parent, [child])
    ladder = (head.attrs or {}).get("кадры") or []
    by_id = {row["id"]: row for row in ladder}
    assert list(by_id) == ["1-K1", "1-K2"]
    assert by_id["1-K1"]["план"] == "ОБЩИЙ"
    assert by_id["1-K2"]["план"] == "КРУПНЫЙ"
    assert by_id["1-K1"].get("активен") is not False


def test_promote_first_child_keeps_head_kadry() -> None:
    from app.services.montage_board_frames import _promote_first_child

    parent = SimpleNamespace(
        number=1,
        uuid="p" * 12,
        sort_key=1.0,
        voiceover_text="голова",
        attrs={
            "кадры": [{"id": "1-K1", "план": "ОБЩИЙ", "ракурс": "фронт"}],
            "главное_действие": "1. комната — смотрит",
        },
    )
    child = SimpleNamespace(
        number=2,
        uuid="c" * 12,
        sort_key=2.0,
        voiceover_text="",
        attrs={"camera_subdivide": {"role": "shot"}},
    )
    head = _promote_first_child(parent, [child])
    assert head is child
    ladder = (head.attrs or {}).get("кадры") or []
    assert ladder and ladder[0]["id"] == "1-K1"
    assert ladder[0]["план"] == "ОБЩИЙ"


@pytest.mark.asyncio
async def test_merge_archives_right_scene_chain(tmp_path) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models import Base, Frame, Project
    from app.services import db_v2
    from app.services.montage_board_frames import merge_montage_scenes

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'merge.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = Project(slug="merge-chain", topic="t", hero_mode="auto")
        session.add(project)
        await session.flush()
        left = Frame(
            project_id=project.id,
            number=1,
            uuid="aa" * 12,
            voiceover_text="лево",
            sort_key=10.0,
            status="planned",
            attrs={
                "главное_действие": "1. сени — входит",
                "camera_subdivide": {"role": "vo_parent", "parent_uuid": "aa" * 12},
            },
        )
        right = Frame(
            project_id=project.id,
            number=2,
            uuid="bb" * 12,
            voiceover_text="право",
            sort_key=20.0,
            status="planned",
            attrs={
                "главное_действие": "2. комната — садится",
                "camera_subdivide": {"role": "vo_parent", "parent_uuid": "bb" * 12},
            },
        )
        session.add_all([left, right])
        await session.flush()
        await merge_montage_scenes(
            session, project, left_frame_id=int(left.id), right_frame_id=int(right.id)
        )
        await session.commit()
        live = await session.get(Frame, right.id)
        assert live is not None
        archived = str((live.attrs or {}).get("главное_действие_архив") or "")
        assert "садится" in archived
    await engine.dispose()
