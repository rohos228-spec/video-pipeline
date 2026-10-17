"""R0c-B: проверки не удаляют сцены и кадры — пометка + WARN, данные на месте."""

from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Artifact,
    ArtifactKind,
    Base,
    Frame,
    FrameStatus,
    FrameText,
    Project,
    ProjectStatus,
    PromptVersion,
)


@pytest.fixture
def warn_log():
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    try:
        yield lines
    finally:
        logger.remove(sink)


def _ids(rows: list[dict]) -> list[str]:
    return [str(row.get("id")) for row in rows]


def test_align_marks_foreign_room_and_keeps_shot_ids(warn_log) -> None:
    from app.services.scene_shot_grammar import align_shots_to_action_chain

    action = (
        "1. кабинет следствия — следователь открывает папку\n"
        "(Следователь открыл толстую следственную папку на столе.)"
    )
    shots = [
        {"id": "a", "сцена": 1, "место": "кабинет следствия", "действие": "открывает"},
        {"id": "lab", "сцена": 1, "место": "лаборатория криминалистики", "действие": "снимает"},
        {"id": "c", "сцена": 1, "место": "кабинет следствия", "действие": "кладёт"},
    ]
    ops = [{"frame_uuid": "u1", "fields": {"кадры": shots}}]
    frames = [{"uuid": "u1", "attrs": {"главное_действие": action}}]
    assert align_shots_to_action_chain(ops, frames) == 1
    kept = ops[0]["fields"]["кадры"]
    assert _ids(kept) == ["a", "lab", "c"]
    marked = kept[1]
    assert marked["карантин"] is True
    assert "чужое место" in marked["пометка"]
    assert any("WARN align_shots_to_action_chain" in line for line in warn_log)


def test_vo_cap_marks_extra_shots_without_dropping_them(warn_log) -> None:
    from app.services.scene_shot_grammar import merge_gpt_shots_with_action

    action = "1. кабинет — следователь открывает папку"
    shots = [
        {"id": "s1", "действие": "следователь открывает папку", "закадр": "Открыл папку."},
        {"id": "s2", "действие": "кладёт папку на стол", "закадр": "Положил папку."},
        {"id": "s3", "действие": "смотрит в окно", "закадр": "Смотрит в окно кабинета."},
    ]
    out, _err = merge_gpt_shots_with_action(shots, action, cell_number=1, vo="")
    assert out is not None
    assert _ids(out) == ["s1", "s2", "s3"]
    marked = [s for s in out if s.get("карантин")]
    assert {s["id"] for s in marked} >= {"s2", "s3"}
    assert any("WARN merge_gpt_shots_with_action" in line and "сверх лимита" in line for line in warn_log)


def test_obshiy_vid_shot_is_marked_not_dropped(warn_log) -> None:
    from app.services.scene_shot_grammar import merge_gpt_shots_with_action

    action = "1. кабинет — следователь открывает папку"
    shots = [
        {"id": "est", "действие": "общий вид кабинета без людей"},
        {"id": "act", "действие": "следователь открывает папку"},
    ]
    out, _err = merge_gpt_shots_with_action(shots, action, vo="")
    assert out is not None
    assert set(_ids(out)) == {"est", "act"}
    est = next(s for s in out if s["id"] == "est")
    assert est["карантин"] is True
    assert "общий вид" in est["пометка"]
    assert any("общий вид" in line for line in warn_log)


def test_repair_shot_vo_marks_empty_shell_and_keeps_it(warn_log) -> None:
    from app.services.scene_shot_grammar import repair_shot_vo_lengths

    vo = "Следователь открыл толстую папку на столе."
    shots = [
        {"id": "real", "действие": "открывает папку", "закадр": vo},
        {"id": "shell"},
    ]
    repair_shot_vo_lengths(shots, vo)
    assert _ids(shots) == ["real", "shell"]
    assert shots[1]["карантин"] is True
    assert "пустая оболочка" in shots[1]["пометка"]
    assert any("WARN repair_shot_vo_lengths" in line for line in warn_log)


def test_repair_shot_vo_keeps_replica_speaker_and_items() -> None:
    from app.services.scene_shot_grammar import repair_shot_vo_lengths

    shots = [
        {"id": "reply-only", "реплика": "Где вы были в десять?"},
        {"id": "speaker-only", "говорит": "Следователь"},
        {"id": "items-only", "предметы": ["папка"]},
        {"id": "shell"},
    ]
    repair_shot_vo_lengths(shots, "")
    assert _ids(shots) == ["reply-only", "speaker-only", "items-only", "shell"]
    for shot in shots[:3]:
        assert shot.get("карантин") is not True
        assert "пометка" not in shot
    assert shots[0]["реплика"] == "Где вы были в десять?"
    assert shots[1]["говорит"] == "Следователь"
    assert shots[2]["предметы"] == ["папка"]
    assert shots[3]["карантин"] is True
    assert "пустая оболочка" in shots[3]["пометка"]


def test_spread_vo_sentences_keeps_shot_ids() -> None:
    """L1793 в ревью — spread_vo_sentences: кадры не выкидывает."""
    from app.services.scene_shot_grammar import spread_vo_sentences

    shots = [
        {"id": "k1", "закадр": "Первое предложение. Второе предложение."},
        {"id": "k2", "закадр": ""},
    ]
    spread_vo_sentences(shots)
    assert _ids(shots) == ["k1", "k2"]
    assert len(shots) == 2


def test_trim_to_cap_marks_bridges_and_keeps_every_id(warn_log) -> None:
    from app.services.montage_scene_improve import _trim_to_cap

    shots = [
        {"id": "a", "роль": "действие"},
        {"id": "b", "роль": "мост"},
        {"id": "c", "роль": "перебивка"},
    ]
    out = _trim_to_cap(shots, 1)
    assert _ids(out) == ["a", "b", "c"]
    assert out[0].get("карантин") is not True
    assert out[1]["карантин"] is True and out[2]["карантин"] is True
    assert any("WARN _trim_to_cap" in line for line in warn_log)


def test_repair_shots_keeps_duplicate_and_unmatched_anchor(warn_log) -> None:
    from app.services.montage_scene_improve import repair_shots

    units = [{"n": 1, "бюджет": 2, "закадр": "Он идёт домой и открывает дверь."}]
    shots = [
        {
            "id": "a",
            "якорь_n": 1,
            "действие": "идёт домой",
            "закадр": "Он идёт домой",
            "роль": "действие",
            "план": "СРЕДНИЙ",
            "ракурс": "3/4",
            "объект": "тело",
            "порядок": 1,
        },
        {
            "id": "dup",
            "якорь_n": 1,
            "действие": "идёт домой",
            "закадр": "и открывает дверь.",
            "роль": "действие",
            "план": "КРУПНЫЙ",
            "ракурс": "фронт",
            "объект": "тело",
            "порядок": 2,
        },
        {
            "id": "lost",
            "якорь_n": 9,
            "действие": "смотрит в окно",
            "закадр": "смотрит",
            "роль": "следствие",
            "план": "КРУПНЫЙ",
            "ракурс": "сверху",
            "объект": "взгляд",
            "порядок": 3,
        },
    ]
    repair_shots(shots, units)
    assert _ids(shots) == ["a", "dup", "lost"]
    assert shots[1]["карантин"] is True
    assert "повтор" in shots[1]["пометка"]
    assert shots[1].get("fw_qc")
    assert shots[2]["карантин"] is True
    assert "не совпал" in shots[2]["пометка"]
    assert any("WARN repair_shots" in line for line in warn_log)


def test_repair_shots_keeps_anchor_order_when_input_is_scrambled() -> None:
    """Хвост и чужой порядок GPT не возвращают кадры в исходный порядок."""
    from app.services.montage_scene_improve import qc_reasons, repair_shots

    units = [
        {"n": 1, "бюджет": 2, "закадр": "Он идёт домой и открывает дверь."},
        {"n": 2, "бюджет": 1, "закадр": "Девушка входит в комнату."},
    ]

    def row(sid: str, anchor: int, action: str, vo: str, *, plan: str) -> dict:
        return {
            "id": sid,
            "якорь_n": anchor,
            "действие": action,
            "закадр": vo,
            "роль": "действие",
            "план": plan,
            "ракурс": "3/4",
            "объект": "тело",
            "порядок": 1,
        }

    shots = [
        row("late", 2, "девушка входит", "Девушка входит в комнату.", plan="СРЕДНИЙ"),
        row("a", 1, "идёт домой", "Он идёт домой", plan="ОБЩИЙ"),
        row("b", 1, "открывает дверь", "и открывает дверь.", plan="КРУПНЫЙ"),
        row("lost", 9, "смотрит в окно", "смотрит", plan="ДЕТАЛЬ"),
    ]
    repair_shots(shots, units)
    assert _ids(shots)[:3] == ["a", "b", "late"]
    assert shots[-1]["id"] == "lost"
    assert shots[-1]["карантин"] is True
    hard, _soft = qc_reasons(shots, units)
    assert not any("не разложены" in item for item in hard)
    assert not any("повтор действия" in item for item in hard)


def test_repair_angles_use_last_live_shot_and_second_pass_is_stable() -> None:
    """Помеченный кадр между двумя живыми не сдвигает точку отсчёта 30°."""
    from app.services.montage_scene_improve import qc_reasons, repair_shots

    units = [{"n": 1, "бюджет": 3, "закадр": "Он стоит у стола и берёт папку."}]

    def row(sid: str, action: str, vo: str, plan: str, angle: str, *, marked: bool = False) -> dict:
        shot = {
            "id": sid,
            "якорь_n": 1,
            "действие": action,
            "закадр": vo,
            "роль": "действие",
            "план": plan,
            "ракурс": angle,
            "объект": "тело",
            "движение": "статика",
            "стык": "cut",
            "порядок": 1,
            "место": "дом",
        }
        if marked:
            shot["карантин"] = True
            shot["пометка"] = "повтор — кадр сохранён"
            shot["активен"] = False
        return shot

    shots = [
        row("a", "стоит у стола", "Он стоит у стола", "СРЕДНИЙ", "фронт"),
        row("m", "смотрит в сторону", "мимо", "КРУПНЫЙ", "сверху", marked=True),
        row("b", "берёт папку", "и берёт папку.", "КРУПНЫЙ", "фронт"),
    ]
    repair_shots(shots, units)
    assert [s["id"] for s in shots] == ["a", "m", "b"]
    assert shots[1]["карантин"] is True
    assert shots[1]["ракурс"] == "сверху"
    hard, _soft = qc_reasons(shots, units)
    assert hard == []
    assert shots[0]["ракурс"] == "фронт"
    assert shots[2]["ракурс"] != shots[0]["ракурс"]
    snapshot = [
        (s["id"], s.get("план"), s.get("ракурс"), s.get("закадр"), s.get("карантин"), s.get("пометка"))
        for s in shots
    ]
    second = repair_shots(shots, units)
    assert [
        (s["id"], s.get("план"), s.get("ракурс"), s.get("закадр"), s.get("карантин"), s.get("пометка"))
        for s in shots
    ] == snapshot
    assert not any("30°" in item for item in second)
    hard_again, _soft_again = qc_reasons(shots, units)
    assert hard_again == []


def test_assign_scene_vo_keeps_shots_when_voiceover_empty(warn_log) -> None:
    from app.services.shot_templates import _assign_scene_vo, fill_kadry_from_catalog

    shots = [
        {"id": "a", "закадр": "уже было", "действие": "стоит"},
        {"id": "b", "закадр": "", "действие": "садится"},
    ]
    out = _assign_scene_vo(shots, "")
    assert _ids(out) == ["a", "b"]
    assert all(s.get("карантин") for s in out)
    assert any("WARN _assign_scene_vo" in line for line in warn_log)
    filled = fill_kadry_from_catalog(
        [{"id": "keep-me", "сцена": 1, "действие": "герой стоит у стола", "закадр": ""}],
        "1. комната — герой стоит у стола",
        cell_number=1,
    )
    assert filled
    assert len(filled) >= 1


def test_fill_kadry_keeps_unmatched_shots_and_source_fields(warn_log) -> None:
    from app.services.shot_templates import fill_kadry_from_catalog

    filled = fill_kadry_from_catalog(
        [
            {
                "id": "keep-me",
                "сцена": 1,
                "действие": "Герой долго стоит у стола и смотрит в окно.",
                "реплика": "алло",
                "говорит": "герой",
                "select_fix": "x",
                "закадр": "стоит",
            },
            {
                "id": "no-scene",
                "действие": "без номера",
                "реплика": "эхо",
                "закадр": "хвост",
            },
            {
                "id": "other-scene",
                "сцена": 9,
                "действие": "чужая сцена",
                "закадр": "там",
            },
        ],
        "1. комната — герой стоит у стола",
        cell_number=1,
    )
    ids = [str(s.get("id")) for s in filled]
    assert "no-scene" in ids
    assert "other-scene" in ids
    assert any(
        s.get("реплика") == "алло"
        and s.get("говорит") == "герой"
        and s.get("select_fix") == "x"
        for s in filled
    )
    assert any(s.get("id") == "no-scene" and s.get("карантин") for s in filled)
    assert any(s.get("id") == "other-scene" and s.get("карантин") for s in filled)
    assert any("WARN fill_kadry_from_catalog" in line for line in warn_log)


def test_fill_kadry_keeps_later_scenes_when_chain_is_empty(warn_log) -> None:
    """Пустая цепь не выбрасывает шоты сцен 2..N."""
    from app.services.shot_templates import fill_kadry_from_catalog

    incoming = [
        {
            "id": "s1",
            "сцена": 1,
            "действие": "Герой долго стоит у стола и смотрит в окно.",
            "закадр": "один",
        },
        {
            "id": "s2",
            "сцена": 2,
            "действие": "Герой долго смотрит в окно и садится.",
            "закадр": "два",
            "реплика": "алло",
        },
    ]
    filled = fill_kadry_from_catalog(
        incoming,
        "герой просто стоит у стола",
        cell_number=1,
    )
    assert len(filled) >= len(incoming)
    kept = next(s for s in filled if s.get("id") == "s2")
    assert kept["карантин"] is True
    assert kept["активен"] is False
    assert "сцена 2" in str(kept.get("пометка") or "")
    assert kept.get("реплика") == "алло"
    assert any("WARN fill_kadry_from_catalog" in line for line in warn_log)


def test_drop_extra_stops_at_required_and_keeps_ids(warn_log) -> None:
    from app.services.shot_templates import _drop_extra_shots

    rows = [
        {"shot_id": "T1-K1", "required": 0},
        {"shot_id": "T1-K2", "required": 1},
        {"shot_id": "T1-K3", "required": 1},
        {"shot_id": "T1-K4", "required": 0},
    ]
    shots = [{"id": f"s{i}", "шаблон": "T1", "закадр": ""} for i in range(4)]
    out = _drop_extra_shots(shots, 1, rows)
    assert _ids(out) == ["s0", "s1", "s2", "s3"]
    assert out[1].get("карантин") is not True
    assert out[2].get("карантин") is not True
    assert out[3]["карантин"] is True
    assert any("required=1" in line for line in warn_log)


def test_qc_coalesce_keeps_ids_when_incoming_is_shorter(warn_log) -> None:
    from app.services.apply_ops_batches import coalesce_cell_shot_ops

    uid = "abc"
    frames = [
        {
            "uuid": uid,
            "кадры": [
                {"id": "1-S1-K1", "закадр": "раз", "действие": "старое"},
                {"id": "1-S1-K2", "закадр": "", "действие": "пауза"},
                {"id": "1-S1-K3", "закадр": "три", "действие": "хвост"},
            ],
        }
    ]
    ops = [
        {
            "frame_uuid": uid,
            "fields": {"кадры": [{"id": "1-S1-K1", "закадр": "раз", "действие": "новое"}]},
        }
    ]
    merged = coalesce_cell_shot_ops(ops, frames, patch=True)
    rows = merged[0]["fields"]["кадры"]
    assert _ids(rows) == ["1-S1-K1", "1-S1-K2", "1-S1-K3"]
    assert rows[0]["действие"] == "новое"
    assert rows[0].get("карантин") is not True
    assert rows[1]["карантин"] is True
    assert "QC-список короче" in rows[1]["пометка"]
    assert rows[2]["карантин"] is True
    assert "QC-список короче" in rows[2]["пометка"]
    assert any("WARN coalesce_cell_shot_ops" in line for line in warn_log)

    # Пустой закадр у кадра, который QC только что вернул, — норма, не карантин.
    fresh = coalesce_cell_shot_ops(
        [
            {
                "frame_uuid": uid,
                "fields": {
                    "кадры": [
                        {"id": "1-S1-K1", "закадр": "", "действие": "пауза QC"},
                        {"id": "1-S1-K2", "закадр": "", "действие": "пауза"},
                        {"id": "1-S1-K3", "закадр": "три", "действие": "хвост"},
                    ]
                },
            }
        ],
        frames,
        patch=True,
    )[0]["fields"]["кадры"]
    assert fresh[0]["действие"] == "пауза QC"
    assert fresh[0].get("карантин") is not True
    assert fresh[0]["закадр"] == ""


def test_apply_generated_scenario_keeps_operator_blocks(warn_log) -> None:
    from app.services.nii67_card import apply_generated_scenario, default_card, insert_block

    card = insert_block(default_card(), "door1", 6, "custom")
    door = card["doors"][0]
    world = next(b for b in door["blocks"] if b["kind"] == "world_frame")
    world["text"] = "текст оператора"
    world["image_prompt"] = "промт оператора"
    world["image_name"] = "slot.png"
    before_ids = [b["id"] for b in door["blocks"]]
    scenario = {
        "theme": "Мокрый цех",
        "law": "Капли падают сами",
        "illustration": "герой выходит",
        "door_design": "стекло",
        "door_anim": "пар",
        "beats": [
            {
                "id": "entry",
                "text": "сгенерированный вход",
                "frames": [{"start_frame": "новый промт кадра"}],
            },
            {"id": "hook", "text": "сгенерированный выход"},
        ],
    }
    out = apply_generated_scenario(card, {"door1": scenario}, {"standard": "финал"})
    blocks = out["doors"][0]["blocks"]
    after_ids = [b["id"] for b in blocks]
    assert before_ids == [i for i in after_ids if i in before_ids]
    assert len(after_ids) == len(set(after_ids))
    saved = next(b for b in blocks if b["id"] == world["id"])
    assert saved["text"] == "текст оператора"
    assert saved["image_prompt"] == "промт оператора"
    assert saved["image_name"] == "slot.png"
    assert saved.get("пометка")
    custom = next(b for b in blocks if b["kind"] == "custom")
    assert custom["id"] in before_ids
    assert custom.get("пометка")
    assert custom.get("карантин") is not True
    assert any("WARN apply_generated_scenario" in line for line in warn_log)


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'keep.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    return Project(
        slug="keep-frames",
        topic="t",
        hero_mode="auto",
        status=ProjectStatus.frames_ready,
        meta={},
    )


@pytest.mark.asyncio
async def test_coverage_delete_in_auto_apply_marks_frame(
    session: AsyncSession, project: Project, warn_log
) -> None:
    from app.services.frame_tombstones import frame_is_active
    from app.services.montage_coverage_ops import apply_coverage_op

    parent_uid = "aa" * 12
    parent = Frame(
        project_id=1,
        number=1,
        uuid=parent_uid,
        voiceover_text="ячейка",
        status=FrameStatus.planned,
        attrs={"camera_subdivide": {"role": "vo_parent", "parent_uuid": parent_uid}},
    )
    child = Frame(
        project_id=1,
        number=2,
        uuid="bb" * 12,
        voiceover_text="кусок",
        status=FrameStatus.image_generated,
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "coverage_kind": "child",
            }
        },
    )
    session.add(project)
    await session.flush()
    parent.project_id = project.id
    child.project_id = project.id
    session.add_all([parent, child])
    await session.flush()
    session.add_all(
        [
            PromptVersion(
                project_id=project.id,
                frame_id=child.id,
                kind="img",
                version=1,
                text="промт ребёнка",
                is_active=True,
            ),
            FrameText(
                project_id=project.id,
                frame_id=child.id,
                kind="voiceover",
                text="кусок",
            ),
            Artifact(
                project_id=project.id,
                frame_id=child.id,
                kind=ArtifactKind.scene_image,
                uuid="art-child",
                path="scenes/frame_002.png",
            ),
        ]
    )
    await session.flush()
    child_id = int(child.id)
    await apply_coverage_op(
        session, project, {"type": "coverage_delete", "frame_number": 2}
    )
    await session.flush()
    rows = (
        await session.execute(select(Frame).where(Frame.project_id == project.id))
    ).scalars().all()
    assert sorted(fr.number for fr in rows) == [1, 2]
    live = await session.get(Frame, child_id)
    assert live is not None
    assert (live.attrs or {}).get("удалён") is True
    assert frame_is_active(live) is False
    assert live.status == FrameStatus.image_generated
    prompt = (
        await session.execute(select(PromptVersion).where(PromptVersion.frame_id == child_id))
    ).scalar_one()
    text = (
        await session.execute(select(FrameText).where(FrameText.frame_id == child_id))
    ).scalar_one()
    art = (
        await session.execute(select(Artifact).where(Artifact.uuid == "art-child"))
    ).scalar_one()
    assert prompt.text == "промт ребёнка"
    assert text.text == "кусок"
    assert art.frame_id == child_id
    assert 2 in (project.meta or {}).get("deleted_frame_numbers", [])
    assert any("coverage_delete" in line and "помечен" in line for line in warn_log)
    assert not any("запрещён в авто-apply" in line for line in warn_log)


@pytest.mark.asyncio
async def test_delete_montage_frame_marks_parent_and_keeps_children(
    session: AsyncSession, project: Project, warn_log
) -> None:
    from app.services.frame_tombstones import frame_is_active
    from app.services.montage_board_frames import delete_montage_frame

    session.add(project)
    await session.flush()
    parent = Frame(
        project_id=project.id,
        number=1,
        uuid="cc" * 12,
        voiceover_text="голова",
        status=FrameStatus.planned,
        attrs={"camera_subdivide": {"role": "vo_parent", "parent_uuid": "cc" * 12}},
    )
    child = Frame(
        project_id=project.id,
        number=2,
        uuid="dd" * 12,
        voiceover_text="шот",
        status=FrameStatus.planned,
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": "cc" * 12,
            }
        },
    )
    session.add_all([parent, child])
    await session.flush()
    parent_id, child_id = int(parent.id), int(child.id)
    result = await delete_montage_frame(session, project, parent_id)
    assert result["deleted"] == 0 and result["marked"] == 1
    left = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    assert [fr.id for fr in left] == [parent_id, child_id]
    assert frame_is_active(left[0]) is False
    assert (left[0].attrs or {}).get("пометка")
    assert frame_is_active(left[1]) is True
    assert any("WARN delete_montage_frame" in line for line in warn_log)


@pytest.mark.asyncio
async def test_marked_frame_is_not_revived_or_duplicated_by_xlsx_import(
    session: AsyncSession, project: Project, tmp_path: Path, warn_log
) -> None:
    from openpyxl import Workbook

    from app.services.frame_tombstones import (
        deleted_numbers,
        forget_deleted,
        frame_is_active,
        frame_marked_deleted,
        mark_frame_deleted,
    )
    from app.services.xlsx_sync import reload_from_xlsx
    from app.services.xlsx_v8_import import (
        ROW_IMAGE_PROMPT_V8,
        ROW_VOICEOVER_V8,
        bootstrap_frames_for_image_step,
        import_v8_xlsx,
    )
    from app.storage.project_sheet import (
        ROW_HEADER,
        ROW_IMAGE_PROMPT,
        ROW_VOICEOVER,
        SHEET_FRAMES,
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "план"
    for i, text in enumerate(["один", "два", "три новых"], start=3):
        ws.cell(row=ROW_VOICEOVER_V8, column=i, value=text)
        ws.cell(row=ROW_IMAGE_PROMPT_V8, column=i, value=f"промт-{i}")
    kadry = wb.create_sheet(SHEET_FRAMES)
    for col, number in ((2, 1), (3, 2), (4, 3)):
        kadry.cell(row=ROW_HEADER, column=col, value=number)
    kadry.cell(row=ROW_VOICEOVER, column=4, value="три из листа Кадры")
    kadry.cell(row=ROW_IMAGE_PROMPT, column=4, value="промт который не должен ожить")
    xlsx = tmp_path / "project.xlsx"
    wb.save(xlsx)

    session.add(project)
    await session.flush()
    frames = []
    for number, text in ((1, "один"), (2, "два"), (3, "три старых")):
        frames.append(
            Frame(
                project_id=project.id,
                number=number,
                uuid=f"{number:02d}" * 12,
                voiceover_text=text,
                image_prompt="старый промт" if number == 3 else None,
                status=FrameStatus.planned,
            )
        )
    session.add_all(frames)
    await session.flush()
    marked = frames[2]
    marked_id = int(marked.id)
    mark_frame_deleted(marked, project, "тест: кадр помечен удалён")
    # Список в meta пуст: импорт обязан остановиться по пометке на кадре,
    # а не завести активную копию с тем же номером.
    forget_deleted(project, 3)
    await session.flush()
    assert deleted_numbers(project) == set()
    assert frame_marked_deleted(marked) is True

    summary = await import_v8_xlsx(
        session, project, xlsx, keep_fields=True, update_frames_voiceover=True
    )
    await bootstrap_frames_for_image_step(session, project, xlsx)
    sync = await reload_from_xlsx(session, project, xlsx)
    await session.flush()
    assert 3 in summary.get("frames_kept_marked", [])
    assert 3 not in sync.get("frames_changed", [])
    assert 3 not in sync.get("frames_created", [])

    rows = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    assert [fr.number for fr in rows] == [1, 2, 3]
    assert [fr.id for fr in rows] == [frames[0].id, frames[1].id, marked_id]
    live = rows[2]
    assert live.voiceover_text == "три старых"
    assert live.image_prompt == "старый промт"
    assert live.status == FrameStatus.planned
    assert (live.attrs or {}).get("удалён") is True
    assert frame_is_active(live) is False
    assert deleted_numbers(project) == {3}
    assert sum(1 for fr in rows if fr.number == 3) == 1
    assert any("помечен «удалён»" in line for line in warn_log)


@pytest.mark.asyncio
async def test_coverage_delete_refuses_non_child(
    session: AsyncSession, project: Project, warn_log
) -> None:
    from app.services.montage_coverage_ops import apply_coverage_op

    session.add(project)
    await session.flush()
    parent = Frame(
        project_id=project.id,
        number=1,
        uuid="ee" * 12,
        voiceover_text="ячейка",
        status=FrameStatus.image_generated,
        image_prompt="промт родителя",
        attrs={"camera_subdivide": {"role": "vo_parent", "parent_uuid": "ee" * 12}},
    )
    session.add(parent)
    await session.flush()
    art = Artifact(
        project_id=project.id,
        frame_id=parent.id,
        kind=ArtifactKind.scene_image,
        uuid="art-parent",
        path="scenes/frame_001.png",
    )
    session.add(art)
    await session.flush()
    result = await apply_coverage_op(
        session, project, {"type": "coverage_delete", "frame_number": 1}
    )
    await session.flush()
    assert result["ok"] is False and result["refused"] == "not_child"
    live = await session.get(Frame, parent.id)
    assert live is not None
    assert (live.attrs or {}).get("удалён") is not True
    assert live.image_prompt == "промт родителя"
    assert live.status == FrameStatus.image_generated
    kept = (
        await session.execute(select(Artifact).where(Artifact.uuid == "art-parent"))
    ).scalar_one()
    assert kept.frame_id == parent.id
    assert any("не дочерний" in line for line in warn_log)


def test_full_vo_on_first_shot_clears_empty_quarantine(monkeypatch, warn_log) -> None:
    from app.services.shot_templates import _assign_scene_vo

    monkeypatch.setattr(
        "app.services.vo_shot_expand.kadry_vo_partition",
        lambda vo, shots: ["", ""],
    )
    monkeypatch.setattr(
        "app.services.vo_shot_expand.kadry_vo_partition_aligned",
        lambda vo, shots: ["", ""],
    )
    monkeypatch.setattr(
        "app.services.scene_design.camera_expand.split_text_into_parts",
        lambda text, n: ["", ""],
    )
    out = _assign_scene_vo(
        [{"id": "a", "закадр": ""}, {"id": "b", "закадр": ""}],
        "Целое предложение сцены.",
    )
    assert out[0]["закадр"] == "Целое предложение сцены."
    assert out[0].get("карантин") is not True
    assert out[1].get("карантин") is True
    assert any("карантин снят" in line for line in warn_log)


def test_deleted_first_shot_stays_inactive_when_full_vo_lands(monkeypatch, warn_log) -> None:
    """Чужой удалён и чужая пометка не снимаются, даже если в причине есть «пуст»."""
    from app.services.frame_tombstones import shot_is_active
    from app.services.shot_templates import _assign_scene_vo, fill_kadry_from_catalog

    monkeypatch.setattr(
        "app.services.vo_shot_expand.kadry_vo_partition",
        lambda vo, shots: ["", ""],
    )
    monkeypatch.setattr(
        "app.services.vo_shot_expand.kadry_vo_partition_aligned",
        lambda vo, shots: ["", ""],
    )
    monkeypatch.setattr(
        "app.services.scene_design.camera_expand.split_text_into_parts",
        lambda text, n: ["", ""],
    )
    foreign = {
        "id": "a",
        "закадр": "",
        "удалён": True,
        "карантин": True,
        "пометка": "кадр пуст — удалил оператор",
        "активен": False,
    }
    out = _assign_scene_vo(
        [foreign, {"id": "b", "закадр": ""}],
        "Целое предложение сцены.",
    )
    assert out[0]["закадр"] == "Целое предложение сцены."
    assert out[0]["удалён"] is True
    assert out[0]["карантин"] is True
    assert out[0]["активен"] is False
    assert out[0]["пометка"] == "кадр пуст — удалил оператор"
    assert shot_is_active(out[0]) is False

    marked_only = _assign_scene_vo(
        [{
            "id": "foreign-mark",
            "закадр": "",
            "карантин": True,
            "пометка": "кадры сцены сохранены оператором",
            "активен": False,
        }],
        "Целое предложение сцены.",
    )
    assert marked_only[0]["карантин"] is True
    assert marked_only[0]["пометка"] == "кадры сцены сохранены оператором"
    assert shot_is_active(marked_only[0]) is False

    filled = fill_kadry_from_catalog(
        [{
            "id": "gone",
            "сцена": 1,
            "удалён": True,
            "карантин": True,
            "пометка": "нет куска — снял монтажёр",
            "активен": False,
            "действие": "Герой долго стоит у стола и смотрит в окно.",
            "закадр": "",
        }],
        "1. комната — герой стоит у стола\n(Целое предложение сцены.)",
        cell_number=1,
    )
    kept = next(s for s in filled if s.get("удалён") is True)
    assert kept.get("id") == "gone" or str(kept.get("id") or "").startswith("gone")
    assert kept["карантин"] is True
    assert kept["активен"] is False
    assert "нет куска — снял монтажёр" in str(kept.get("пометка") or "")
    assert shot_is_active(kept) is False
    assert any("WARN" in line for line in warn_log)


@pytest.mark.asyncio
async def test_marked_frame_skips_paid_image_generation(
    session: AsyncSession, project: Project, tmp_path: Path, warn_log, monkeypatch
) -> None:
    from app.orchestrator.steps.generate_images import (
        _claim_shot1_batch,
        _generate_and_send,
    )
    from app.services.frame_tombstones import mark_frame_deleted

    session.add(project)
    await session.flush()
    live = Frame(
        project_id=project.id,
        number=1,
        uuid="11" * 12,
        voiceover_text="живой",
        image_prompt="промт живого",
        status=FrameStatus.planned,
    )
    marked = Frame(
        project_id=project.id,
        number=2,
        uuid="22" * 12,
        voiceover_text="помеченный",
        image_prompt="промт помеченного",
        status=FrameStatus.planned,
    )
    session.add_all([live, marked])
    await session.flush()
    mark_frame_deleted(marked, project, "тест")
    await session.flush()
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    claimed = await _claim_shot1_batch(
        session, project.id, scenes, project=project, limit=4
    )
    assert [fr.number for fr in claimed] == [1]
    await session.refresh(marked)
    assert marked.status == FrameStatus.planned
    assert marked.image_prompt == "промт помеченного"
    assert (marked.attrs or {}).get("img_gen_inflight") is None
    assert (marked.attrs or {}).get("удалён") is True

    async def _boom(*_a, **_k):
        raise AssertionError("платный image API вызван")

    monkeypatch.setattr(
        "app.orchestrator.steps.generate_images.generate_image_with_retries",
        _boom,
    )
    await _generate_and_send(
        session, None, None, None, project, marked, scenes  # type: ignore[arg-type]
    )
    await session.refresh(marked)
    assert marked.image_prompt == "промт помеченного"
    assert marked.status == FrameStatus.planned
    assert any("WARN generate_images" in line and "платная генерация" in line for line in warn_log)


@pytest.mark.asyncio
async def test_marked_frame_skips_paid_video_generation(
    session: AsyncSession, project: Project, warn_log, monkeypatch
) -> None:
    from contextlib import asynccontextmanager

    from app.orchestrator.steps.generate_videos import (
        _claim_shot1_video_batch,
        _shot1_job,
    )
    from app.services.frame_tombstones import PaidFrameSkipped, mark_frame_deleted
    from app.services.montage_board_regen import (
        ImageRegenPrep,
        VideoRegenPrep,
        execute_image_regen,
        execute_video_regen,
        prepare_image_regen,
        prepare_video_regen,
    )

    session.add(project)
    await session.flush()
    live = Frame(
        project_id=project.id,
        number=1,
        uuid="33" * 12,
        voiceover_text="живой",
        animation_prompt="slow pan",
        image_prompt="кадр",
        status=FrameStatus.animation_prompt_ready,
    )
    marked = Frame(
        project_id=project.id,
        number=2,
        uuid="44" * 12,
        voiceover_text="помеченный",
        animation_prompt="не снимать",
        image_prompt="кадр помеченный",
        status=FrameStatus.animation_prompt_ready,
    )
    session.add_all([live, marked])
    await session.flush()
    mark_frame_deleted(marked, project, "тест")
    await session.flush()
    (project.data_dir / "videos").mkdir(parents=True, exist_ok=True)
    claimed = await _claim_shot1_video_batch(session, project.id, limit=4)
    assert [fr.number for fr in claimed] == [1]
    await session.refresh(marked)
    assert marked.status == FrameStatus.animation_prompt_ready
    assert marked.animation_prompt == "не снимать"
    assert (marked.attrs or {}).get("video_gen_inflight") is None

    async def _boom(*_a, **_k):
        raise AssertionError("платный video API вызван")

    monkeypatch.setattr(
        "app.orchestrator.steps.generate_videos.generate_video_with_retries",
        _boom,
    )

    @asynccontextmanager
    async def _slot():
        yield None

    monkeypatch.setattr(
        "app.orchestrator.steps.generate_videos.acquire_outsee_slot",
        _slot,
    )

    class _SM:
        def __call__(self):
            return self

        async def __aenter__(self):
            return session

        async def __aexit__(self, *_a):
            return False

    assert await _shot1_job(
        project_id=project.id,
        frame_id=marked.id,
        out_dir=project.data_dir / "videos",
        scenes_dir=project.data_dir / "scenes",
        outsee=None,
        gpt=None,
        session_clip_paths=[],
        clips_lock=__import__("asyncio").Lock(),
        session_maker=_SM(),
    ) is False
    await session.refresh(marked)
    assert marked.animation_prompt == "не снимать"
    assert marked.status == FrameStatus.animation_prompt_ready

    with pytest.raises(PaidFrameSkipped):
        await prepare_image_regen(session, project, 2, shot=1, mode="same_prompt")
    with pytest.raises(PaidFrameSkipped):
        await prepare_video_regen(session, project, 2, shot=1, mode="same_prompt")
    await session.refresh(marked)
    assert marked.image_prompt == "кадр помеченный"
    assert marked.animation_prompt == "не снимать"

    @asynccontextmanager
    async def _scope(_project_id=None):
        yield session

    monkeypatch.setattr("app.services.montage_board_apply.session_scope", _scope)
    monkeypatch.setattr(
        "app.services.montage_board_regen.generate_image_with_retries",
        _boom,
    )
    monkeypatch.setattr(
        "app.services.montage_board_regen.generate_video_with_retries",
        _boom,
    )
    with pytest.raises(PaidFrameSkipped):
        await execute_image_regen(
            ImageRegenPrep(
                project_id=project.id,
                frame_number=2,
                shot=1,
                prompt_text="api",
                file_path=project.data_dir / "scenes" / "x.png",
            )
        )
    with pytest.raises(PaidFrameSkipped):
        await execute_video_regen(
            VideoRegenPrep(
                project_id=project.id,
                frame_number=2,
                shot=1,
                prompt_text="vid",
                file_path=project.data_dir / "videos" / "x.mp4",
                start_frame=project.data_dir / "scenes" / "x.png",
            )
        )
    assert any("WARN generate_videos" in line and "платная генерация" in line for line in warn_log)


def test_paid_skip_reason_does_not_call_inactive_frame_deleted() -> None:
    from types import SimpleNamespace

    from app.services.frame_tombstones import paid_frame_skip_reason

    inactive = SimpleNamespace(number=4, attrs={"активен": False})
    reason = paid_frame_skip_reason(inactive)
    assert reason is not None
    assert "неактивен" in reason
    assert "удалён" not in reason
    marked = SimpleNamespace(number=4, attrs={"удалён": True, "активен": False})
    marked_reason = paid_frame_skip_reason(marked)
    assert marked_reason is not None and "удалён" in marked_reason


@pytest.mark.asyncio
async def test_regen_stops_paid_call_when_db_read_fails(
    monkeypatch: pytest.MonkeyPatch, warn_log
) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    from app.services.montage_board_regen import _regen_paid_blocked

    class _Session:
        async def execute(self, *_a, **_k):
            raise SQLAlchemyError("frames unreadable")

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(
        "app.services.montage_board_apply.session_scope",
        lambda *_a, **_k: _Scope(),
    )
    assert await _regen_paid_blocked(1, 2, where="execute_image_regen") is True
    assert any("ошибка БД" in line and "остановлена" in line for line in warn_log)


@pytest.mark.asyncio
async def test_delete_coverage_child_marks_parent_shot_chain(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_coverage_ops import delete_coverage_child

    session.add(project)
    await session.flush()
    parent_uid = "aa" * 12
    parent = Frame(
        project_id=project.id,
        number=1,
        uuid=parent_uid,
        voiceover_text="ячейка целиком",
        status=FrameStatus.planned,
        attrs={
            "кадры": [
                {"id": "1-K1", "план": "ОБЩИЙ", "закадр": "ячейка"},
                {"id": "1-K2", "план": "ДЕТАЛЬ", "закадр": "кусок"},
            ],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
            },
        },
    )
    child = Frame(
        project_id=project.id,
        number=2,
        uuid="bb" * 12,
        voiceover_text="кусок",
        status=FrameStatus.planned,
        attrs={
            "кадры": [
                {"id": "1-K2", "план": "ДЕТАЛЬ", "закадр": "кусок"},
            ],
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "coverage_kind": "child",
            },
        },
    )
    session.add_all([parent, child])
    await session.flush()
    child_id = int(child.id)
    await delete_coverage_child(session, project, child, [parent, child])
    await session.refresh(parent)
    await session.refresh(child)
    ladder = list((parent.attrs or {}).get("кадры") or [])
    assert [item["id"] for item in ladder] == ["1-K1", "1-K2"]
    assert ladder[0].get("карантин") is not True
    marked = ladder[1]
    assert marked["карантин"] is True
    assert marked["удалён"] is True
    assert marked["активен"] is False
    assert "помечен" in str(marked.get("пометка") or "")
    assert int(child.id) == child_id
    assert (child.attrs or {}).get("удалён") is True
    own = (child.attrs or {})["кадры"][0]
    assert own["id"] == "1-K2"
    assert own["карантин"] is True
    assert child.voiceover_text == "кусок"


@pytest.mark.asyncio
async def test_coverage_delete_stamps_parent_shot(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_coverage_ops import apply_coverage_op

    session.add(project)
    await session.flush()
    parent_uid = "cc" * 12
    parent = Frame(
        project_id=project.id,
        number=1,
        uuid=parent_uid,
        voiceover_text="ячейка",
        status=FrameStatus.planned,
        attrs={
            "кадры": [
                {"id": "1-S1-K1", "закадр": "ячейка"},
                {"id": "1-S1-K2", "закадр": "хвост"},
            ],
            "camera_subdivide": {"role": "vo_parent", "parent_uuid": parent_uid},
        },
    )
    child = Frame(
        project_id=project.id,
        number=2,
        uuid="dd" * 12,
        voiceover_text="хвост",
        status=FrameStatus.planned,
        attrs={
            "кадры": [{"id": "1-S1-K2", "закадр": "хвост"}],
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "coverage_kind": "child",
                "shot_id": "1-S1-K2",
            },
        },
    )
    session.add_all([parent, child])
    await session.flush()
    await apply_coverage_op(
        session, project, {"type": "coverage_delete", "frame_number": 2}
    )
    await session.refresh(parent)
    await session.refresh(child)
    ladder = list((parent.attrs or {}).get("кадры") or [])
    assert [item["id"] for item in ladder] == ["1-S1-K1", "1-S1-K2"]
    assert ladder[1]["карантин"] is True
    assert ladder[1]["удалён"] is True
    assert ladder[0].get("карантин") is not True
    assert (child.attrs or {}).get("удалён") is True
    assert child.voiceover_text == "хвост"


def test_export_skips_marked_frame_column(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from openpyxl import Workbook, load_workbook

    from app.services.db_apply import export_project_xlsx
    from app.services.xlsx_v8_import import ROW_VOICEOVER_V8

    path = tmp_path / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "план"
    ws.cell(row=ROW_VOICEOVER_V8, column=4, value="старый-закадр")
    wb.save(path)
    wb.close()
    marked = SimpleNamespace(
        number=2,
        image_prompt="картинка",
        animation_prompt="видео",
        voiceover_text="секретный закадр",
        duration_seconds=3.0,
        start_ts=0.0,
        end_ts=3.0,
        attrs={"удалён": True, "активен": False, "карантин": True},
    )
    live = SimpleNamespace(
        number=1,
        image_prompt="",
        animation_prompt="",
        voiceover_text="живой",
        duration_seconds=1.0,
        start_ts=None,
        end_ts=None,
        attrs={},
    )
    project = SimpleNamespace(data_dir=tmp_path, meta={})
    export_project_xlsx(project, [live, marked])  # type: ignore[arg-type]
    wb2 = load_workbook(path)
    sheet = wb2["план"]
    assert sheet.cell(ROW_VOICEOVER_V8, 3).value == "живой"
    assert sheet.cell(ROW_VOICEOVER_V8, 4).value is None
    wb2.close()
    assert marked.voiceover_text == "секретный закадр"
    assert marked.attrs["удалён"] is True


def test_assembly_clips_skip_marked_frame(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from app.services.shot2_timeline import build_assembly_clip_specs

    live = SimpleNamespace(number=1, attrs={})
    marked = SimpleNamespace(number=2, attrs={"удалён": True, "активен": False})
    shot_live = tmp_path / "live.mp4"
    shot_marked = tmp_path / "marked.mp4"
    shot_live.write_bytes(b"live-clip")
    shot_marked.write_bytes(b"marked-clip")
    source = [live, marked]
    clips = build_assembly_clip_specs(
        source,
        {1: shot_live, 2: shot_marked},
        {1: None, 2: None},
        {1: 2.0, 2: 2.0},
    )
    assert len(clips) == 1
    assert clips[0].src == shot_live
    assert len(source) == 2
    assert source[1] is marked
    assert shot_marked.is_file()


@pytest.mark.asyncio
async def test_marked_shot_gets_no_voiceover_and_no_active_frame(warn_log, monkeypatch) -> None:
    """Помеченный шот не получает закадр и не порождает активный Frame."""
    from types import SimpleNamespace

    from app.services.frame_tombstones import shot_is_active
    from app.services.scene_shot_grammar import _mark_shot_kept, spread_vo_sentences
    from app.services import vo_shot_expand as vse

    shots = [
        {"id": "k1", "закадр": "Первое предложение."},
        {"id": "k2", "закадр": "", "действие": "лишний шаг"},
        {"id": "k3", "закадр": ""},
    ]
    _mark_shot_kept(shots[1], "сверх лимита — кадр сохранён", where="test")
    assert shot_is_active(shots[1]) is False
    assert shots[1]["активен"] is False
    spread_vo_sentences(shots)
    assert shots[1]["закадр"] == ""
    assert "Первое" in shots[0]["закадр"] or "предложение" in shots[2]["закадр"]
    assert _ids(shots) == ["k1", "k2", "k3"]

    parent = SimpleNamespace(
        id=1,
        uuid="parent",
        voiceover_text="Первое предложение. Второе предложение целиком.",
        attrs={"кадры": [dict(s) for s in shots]},
        sort_key=1.0,
        number=1,
        start_ts=0.0,
        end_ts=4.0,
        duration_seconds=4.0,
    )
    inserted: list[object] = []

    async def _insert(*_a, **_k):
        child = SimpleNamespace(
            id=2,
            uuid="child",
            voiceover_text="",
            attrs={},
            sort_key=2.0,
            number=2,
            start_ts=None,
            end_ts=None,
            duration_seconds=None,
        )
        inserted.append(child)
        return child

    async def _renumber(*_a, **_k):
        return [parent, *inserted]

    class _Session:
        async def flush(self) -> None:
            return None

    monkeypatch.setattr(vse, "already_subdivided", lambda frames: False)
    monkeypatch.setattr(vse, "insert_frame_after", _insert)
    monkeypatch.setattr(vse, "renumber_frames_by_sort_key", _renumber)
    monkeypatch.setattr(vse, "apply_vo_shot_cuts", lambda frames: 0)

    # Один живой шот и один помеченный: новый Frame не создаётся.
    parent.attrs["кадры"] = [
        {"id": "k1", "закадр": "Первое предложение."},
        {"id": "k2", "закадр": "", "карантин": True, "активен": False, "пометка": "лишний"},
    ]
    ordered, _report = await vse._expand_vo_cells_into_shots_default(
        _Session(),
        SimpleNamespace(id=3),
        [parent],
    )
    assert inserted == []
    assert all(getattr(fr, "voiceover_text", None) != "лишний" for fr in ordered)
    marked = next(s for s in parent.attrs["кадры"] if s["id"] == "k2")
    assert marked["закадр"] == ""
    assert shot_is_active(marked) is False
    assert any("помеченных шотов" in line for line in warn_log)


@pytest.mark.asyncio
async def test_delete_then_rebuild_does_not_grow_active_frames(
    session: AsyncSession, project: Project
) -> None:
    """После пометки шота повторная раскладка не создаёт активный Frame."""
    from app.services.frame_tombstones import frame_is_active
    from app.services.montage_board_frames import delete_montage_frame
    from app.services.montage_coverage_ops import (
        apply_coverage_scene_action,
        delete_coverage_child,
    )
    from app.services.vo_shot_expand import _cs

    session.add(project)
    await session.flush()
    parent_uid = "ab" * 12
    parent = Frame(
        project_id=project.id,
        number=1,
        uuid=parent_uid,
        voiceover_text="голова ячейки",
        status=FrameStatus.planned,
        sort_key=10.0,
        attrs={
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
                "shot_id": "1-K1",
            },
            "кадры": [
                {"id": "1-K1", "закадр": "голова ячейки", "действие": "стоит у стола", "план": "СРЕДНИЙ"},
                {"id": "1-K2", "закадр": "кусок шота", "действие": "садится", "план": "КРУПНЫЙ"},
            ],
        },
    )
    child = Frame(
        project_id=project.id,
        number=2,
        uuid="cd" * 12,
        voiceover_text="кусок шота",
        status=FrameStatus.planned,
        sort_key=20.0,
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "shot_id": "1-K2",
                "coverage_parent_id": "1-K1",
            },
            "кадры": [
                {"id": "1-K2", "закадр": "кусок шота", "действие": "садится", "план": "КРУПНЫЙ"}
            ],
        },
    )
    sibling = Frame(
        project_id=project.id,
        number=3,
        uuid="ef" * 12,
        voiceover_text="третий",
        status=FrameStatus.planned,
        sort_key=30.0,
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "shot_id": "1-K3",
            }
        },
    )
    session.add_all([parent, child, sibling])
    await session.flush()

    frames = [parent, child, sibling]
    await delete_coverage_child(session, project, child, frames)
    rows = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    assert [fr.number for fr in rows] == [1, 2, 3]
    active_before = sum(1 for fr in rows if frame_is_active(fr))
    assert active_before == 2
    assert frame_is_active(rows[1]) is False

    kadry = list((rows[0].attrs or {}).get("кадры") or [])
    await apply_coverage_scene_action(
        session,
        project,
        rows[0],
        list(rows),
        "1. комната — стоит у стола",
        grow=True,
        kadry=kadry,
        lock_parent_plan=False,
    )
    rows = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    assert [fr.number for fr in rows] == [1, 2, 3]
    assert sum(1 for fr in rows if frame_is_active(fr)) == active_before
    marked = next(fr for fr in rows if fr.number == 2)
    assert marked.voiceover_text == "кусок шота"
    assert any(
        item.get("id") == "1-K2" and item.get("активен") is False
        for item in (rows[0].attrs or {}).get("кадры") or []
    )

    parent_row = next(fr for fr in rows if fr.number == 1)
    result = await delete_montage_frame(session, project, int(parent_row.id))
    rows = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    assert [fr.id for fr in rows if fr.number == 1] == [parent_row.id]
    assert frame_is_active(rows[0]) is False
    assert result["promoted"] == 3
    head = next(fr for fr in rows if fr.number == 3)
    assert _cs(head).get("role") == "vo_parent"
    assert str((_cs(head).get("vo_cell_full") or head.attrs.get("vo_cell_full") or "")) or head.voiceover_text


@pytest.mark.asyncio
async def test_marked_middle_frame_timeline_matches_audio_length(warn_log) -> None:
    """Помеченный кадр выпадает из списка клипов. Длину файла проверяет соседний тест."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.services.frame_audio import build_assembly_timeline
    from app.services.frame_timeline_sync import timeline_frames_and_cells
    from app.services.whisper import WordTS

    frames = [
        SimpleNamespace(number=1, voiceover_text="один", attrs={}),
        SimpleNamespace(
            number=2,
            voiceover_text="два в середине",
            attrs={"удалён": True, "активен": False},
        ),
        SimpleNamespace(number=3, voiceover_text="три", attrs={}),
    ]
    timeline, cells = timeline_frames_and_cells(SimpleNamespace(), frames)
    assert [fr.number for fr in timeline] == [1, 3]
    assert [n for n, _text in cells] == [1, 3]
    assert frames[1].voiceover_text == "два в середине"
    assert any("не входит в TTS" in line for line in warn_log)

    words = [
        WordTS("один", 0.0, 2.0, 1.0),
        WordTS("два", 2.0, 5.0, 1.0),
        WordTS("три", 5.0, 9.0, 1.0),
    ]
    voice = Path("voice_full.wav")
    with (
        patch(
            "app.services.frame_audio.probe_duration",
            new=AsyncMock(return_value=9.0),
        ),
        patch("app.services.frame_audio.has_all_frame_audio", return_value=False),
    ):
        clips, master, _scale, per_frame, mux_audio = await build_assembly_timeline(
            Path("."),
            voice,
            [1, 2, 3],
            cells=[(1, "один"), (2, "два"), (3, "три")],
            words=words,
            omit_numbers={2},
        )
    assert per_frame is False
    assert mux_audio == voice
    assert [c.frame_number for c in clips] == [1, 3]
    assert abs(sum(c.duration for c in clips) - master) < 0.05
    assert clips[1].start_ts == clips[0].end_ts


@pytest.mark.asyncio
async def test_marked_frame_mux_audio_matches_live_clip_duration(tmp_path: Path, warn_log) -> None:
    """Mux берёт новую дорожку без речи помеченного кадра, не весь voice_full."""
    import subprocess

    from app.services.frame_audio import build_assembly_timeline
    from app.services.media_probe import probe_duration

    def tone(path: Path, seconds: float, freq: int) -> None:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"sine=frequency={freq}:duration={seconds}",
                "-c:a", "libmp3lame", "-q:a", "7",
                str(path),
            ],
            check=True,
            capture_output=True,
        )

    audio = tmp_path / "audio"
    audio.mkdir()
    tone(audio / "frame_001.mp3", 1.0, 440)
    tone(audio / "frame_002.mp3", 2.0, 880)
    tone(audio / "frame_003.mp3", 1.5, 660)
    voice = audio / "voice_full.mp3"
    tone(voice, 4.5, 220)
    original = voice.read_bytes()
    frame_bytes = {
        n: (audio / f"frame_{n:03d}.mp3").read_bytes() for n in (1, 2, 3)
    }

    clips, master, _scale, per_frame, mux_audio = await build_assembly_timeline(
        audio,
        voice,
        [1, 2, 3],
        per_frame_tts=True,
        omit_numbers={2},
    )
    assert per_frame is True
    assert [c.frame_number for c in clips] == [1, 3]
    assert mux_audio != voice
    assert mux_audio.is_file()
    assert voice.read_bytes() == original
    for number, data in frame_bytes.items():
        assert (audio / f"frame_{number:03d}.mp3").read_bytes() == data
    probed = await probe_duration(mux_audio)
    clip_sum = sum(c.duration for c in clips)
    assert abs(probed - clip_sum) < 0.25
    assert abs(master - clip_sum) < 0.25
    assert probed < (await probe_duration(voice)) - 1.0
    assert any("дорожка без помеченных" in line for line in warn_log)


@pytest.mark.asyncio
async def test_drop_frame_rows_marks_and_keeps_related_rows(
    session: AsyncSession, project: Project, warn_log
) -> None:
    from app.services.frame_tombstones import frame_is_active
    from app.services.montage_board_frames import _drop_frame_rows

    session.add(project)
    await session.flush()
    frame = Frame(
        project_id=project.id,
        number=4,
        uuid="d4" * 12,
        voiceover_text="не стирать",
        status=FrameStatus.planned,
        attrs={},
    )
    session.add(frame)
    await session.flush()
    session.add_all(
        [
            PromptVersion(
                project_id=project.id,
                frame_id=frame.id,
                kind="img",
                version=1,
                text="промт",
                is_active=True,
            ),
            FrameText(
                project_id=project.id,
                frame_id=frame.id,
                kind="voiceover",
                text="не стирать",
            ),
            Artifact(
                project_id=project.id,
                frame_id=frame.id,
                kind=ArtifactKind.scene_image,
                uuid="art-drop",
                path="scenes/frame_004.png",
            ),
        ]
    )
    await session.flush()
    frame_id = int(frame.id)
    await _drop_frame_rows(session, project, [frame])
    kept = await session.get(Frame, frame_id)
    assert kept is not None
    assert kept.voiceover_text == "не стирать"
    assert frame_is_active(kept) is False
    assert (kept.attrs or {}).get("удалён") is True
    prompt = (
        await session.execute(select(PromptVersion).where(PromptVersion.frame_id == frame_id))
    ).scalar_one()
    text = (
        await session.execute(select(FrameText).where(FrameText.frame_id == frame_id))
    ).scalar_one()
    art = (
        await session.execute(select(Artifact).where(Artifact.uuid == "art-drop"))
    ).scalar_one()
    assert prompt.text == "промт"
    assert text.text == "не стирать"
    assert art.frame_id == frame_id
    assert any("_drop_frame_rows" in line for line in warn_log)
