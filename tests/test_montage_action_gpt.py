"""Генерация сцен одной VO-ячейки с доски: merge кусков + apply-ops."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services.montage_action_gpt import (
    build_action_generate_prompt,
    extract_main_action_from_reply,
    generate_cell_scene_action,
    merge_scene_chain,
    parse_replace_ns,
)
from app.services.shot_templates import format_scene_chain, parse_scene_chain
from app.services.vo_shot_expand import main_action_text


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'action-gpt.db'}")
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
    return Project(id=63, slug="tkach-action", topic="t", hero_mode="auto")


BASE_CHAIN = [
    {
        "n": 1,
        "place": "кабинет",
        "action": "вошёл → сел к столу",
        "vo": "Он вошёл в кабинет и сел.",
    },
    {
        "n": 2,
        "place": "двор",
        "action": "вышел на крыльцо",
        "vo": "Потом вышел во двор.",
    },
]


def test_parse_replace_ns_unique_positive() -> None:
    assert parse_replace_ns("2, 1, 2") == [2, 1]
    assert parse_replace_ns([3, "4", 0, -1, "x"]) == [3, 4]
    assert parse_replace_ns(None) == []


def test_merge_replace_ns_keeps_other_scenes() -> None:
    patch = [
        {
            "n": 2,
            "place": "казарма",
            "action": "вошёл в строй",
            "vo": "Зашёл в казарму.",
        }
    ]
    merged = merge_scene_chain(BASE_CHAIN, patch, [2])
    assert [row["n"] for row in merged] == [1, 2]
    assert merged[0]["place"] == "кабинет"
    assert merged[1]["place"] == "казарма"
    assert merged[1]["action"] == "вошёл в строй"
    assert "Зашёл" in merged[1]["vo"]


def test_merge_patch_without_n_zips_onto_replace_ns() -> None:
    patch = [{"n": 1, "place": "казарма", "action": "вошёл в строй", "vo": ""}]
    merged = merge_scene_chain(BASE_CHAIN, patch, [2])
    assert merged[0]["place"] == "кабинет"
    assert merged[1]["place"] == "казарма"
    assert merged[1]["vo"] == "Потом вышел во двор."


def test_merge_empty_replace_replaces_whole_chain() -> None:
    patch = [
        {"n": 9, "place": "архив", "action": "снял папку", "vo": "Снял папку."},
        {"n": 9, "place": "коридор", "action": "пошёл дальше", "vo": "Пошёл."},
    ]
    merged = merge_scene_chain(BASE_CHAIN, patch, [])
    assert [row["n"] for row in merged] == [1, 2]
    assert merged[0]["place"] == "архив"
    assert merged[1]["place"] == "коридор"


def test_extract_main_action_from_apply_ops() -> None:
    uid = "aa" * 12
    reply = json.dumps(
        {
            "ops": [
                {
                    "frame_uuid": uid,
                    "fields": {
                        "главное_действие": (
                            "1. архив — вошёл → снял папку\n"
                            "(Следователь вошёл в архив и снял папку.)"
                        )
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    text = extract_main_action_from_reply(reply, uid)
    chain = parse_scene_chain(text)
    assert chain[0]["place"] == "архив"
    assert "снял папку" in chain[0]["action"]


def test_extract_main_action_from_bare_chain() -> None:
    raw = "1. двор — вышел на крыльцо\n(Потом вышел во двор.)"
    text = extract_main_action_from_reply(raw, "xx")
    assert text.startswith("1. двор")


def test_build_prompt_is_one_cell_and_uses_group_action() -> None:
    parent = Frame(
        project_id=1,
        number=5,
        uuid="cc" * 12,
        voiceover_text="Он вошёл в архив.",
        status="planned",
        attrs={
            "vo_cell_full": "Он вошёл в архив и снял папку.",
            "главное_действие": format_scene_chain(BASE_CHAIN),
            "биты": [{"порядок": 1, "якорь": "вошёл", "изменение": "снаружи → внутри"}],
            "camera_subdivide": {"место": "архив", "role": "vo_parent"},
        },
    )
    text = build_action_generate_prompt(
        parent=parent,
        members=[parent],
        operator_prompt="убери лишние шаги",
        replace_ns=[2],
        passport={"place": "архив", "light": "ночной"},
    )
    assert "задача оператора" in text
    assert "replace_ns" in text
    assert "ТОЛЬКО сцены с номерами 2" in text
    assert "main_action_from_bits_ru" in text or "главное_действие" in text
    assert '"паспорт"' not in text
    assert "полный кадр" in text
    assert "кто слева" in text
    assert "убери лишние шаги" in text
    assert "Он вошёл в архив" in text


def test_build_prompt_improve_asks_for_intro_and_cutaway() -> None:
    parent = Frame(
        project_id=1,
        number=5,
        uuid="cc" * 12,
        voiceover_text="Он вошёл в архив и снял папку.",
        status="planned",
        attrs={"camera_subdivide": {"место": "архив", "role": "vo_parent"}},
    )
    text = build_action_generate_prompt(
        parent=parent,
        members=[parent],
        operator_prompt="только стол и газета",
        replace_ns=[],
        passport={"place": "архив"},
        mode="improve",
    )
    assert "только стол и газета" in text
    assert "Главный заказ" in text
    assert text.index("Главный заказ") < text.index("главное_действие")
    assert "вступление в место" not in text
    assert "минимум три" not in text
    assert "Если закадр длиннее 40" not in text
    assert "ТОЛЬКО сцены с номерами" not in text


@pytest.mark.asyncio
async def test_generate_cell_applies_chain_without_insert(
    session: AsyncSession, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent_uid = "aa" * 12
    child_uid = "bb" * 12
    parent = Frame(
        project_id=project.id,
        number=5,
        uuid=parent_uid,
        voiceover_text="Следователь вошёл в архив и снял папку с полки.",
        status="planned",
        attrs={
            "place": "архив",
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
                "место": "архив",
            },
        },
    )
    child = Frame(
        project_id=project.id,
        number=6,
        uuid=child_uid,
        voiceover_text="снял папку с полки.",
        status="planned",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
            },
        },
    )
    session.add_all([project, parent, child])
    await session.flush()

    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        assert "voiceover_text" not in text
        if "Поставь сцену" in text:
            assert "разложи вход и полку" in text
            assert "персонажи_реестра" in text
        return json.dumps(
            {
                "ops": [
                    {
                        "frame_uuid": parent_uid,
                        "fields": {
                            "главное_действие": (
                                "1. архив — вошёл в проём → снял папку с полки\n"
                                "(Следователь вошёл в архив и снял папку с полки.)"
                            )
                        },
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", fake_ask)

    result = await generate_cell_scene_action(
        session,
        project,
        int(parent.id),
        operator_prompt="разложи вход и полку",
        passport={"place": "архив", "light": "ночной"},
    )
    assert result["ok"] is True
    assert result["replace_ns"] == []
    assert result["passport_applied"] == []
    chain = parse_scene_chain(result["chain"])
    assert chain[0]["place"] == "архив"
    assert "вошёл в проём" in (chain[0].get("action") or "")
    assert "разложи вход и полку" not in (chain[0].get("action") or "")
    assert int(result["report"]["inserted_frames"]) == 0
    assert "image_ops" not in result
    assert result["frame_numbers"] == [5, 6]
    await session.refresh(parent)
    assert "вошёл в проём" in main_action_text(parent)


@pytest.mark.asyncio
async def test_generate_cell_regenerates_piece_only(
    session: AsyncSession, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent_uid = "dd" * 12
    parent = Frame(
        project_id=project.id,
        number=8,
        uuid=parent_uid,
        voiceover_text="Он вошёл в кабинет и сел. Потом вышел во двор.",
        status="planned",
        attrs={
            "главное_действие": format_scene_chain(BASE_CHAIN),
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
                "место": "кабинет",
            },
        },
    )
    session.add_all([project, parent])
    await session.flush()

    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        assert "ТОЛЬКО сцены с номерами 2" in text
        return json.dumps(
            {
                "ops": [
                    {
                        "frame_uuid": parent_uid,
                        "fields": {
                            "главное_действие": (
                                "2. казарма — вошёл в строй\n(Зашёл в казарму.)"
                            )
                        },
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", fake_ask)
    result = await generate_cell_scene_action(
        session,
        project,
        int(parent.id),
        operator_prompt="двор замени на казарму",
        replace_ns=[2],
    )
    merged = parse_scene_chain(result["chain"])
    actions = " ".join(row.get("action") or "" for row in merged)
    assert "сел к столу" in actions or "вошёл → сел" in result["chain"]
    assert "вошёл в строй" in result["chain"]


def test_build_scene_image_ops_skips_leftover_and_maps_beats() -> None:
    from app.services.montage_action_gpt import (
        build_scene_image_ops,
        scene_image_instruction,
    )

    parent = Frame(
        id=10,
        number=1,
        uuid="aa" * 12,
        sort_key=1.0,
        attrs={"действие": "душит", "camera_subdivide": {"role": "vo_parent", "leftover": False}},
    )
    child = Frame(
        id=11,
        number=2,
        uuid="bb" * 12,
        sort_key=2.0,
        attrs={"действие": "уходит", "camera_subdivide": {"role": "shot", "leftover": False}},
    )
    extra = Frame(
        id=12,
        number=3,
        uuid="cc" * 12,
        sort_key=3.0,
        attrs={"camera_subdivide": {"role": "shot", "leftover": True}},
    )
    ops = build_scene_image_ops(
        [parent, child, extra],
        passport={"place": "ЛЕСОПОЛОСА", "light": "ночной"},
        chain="душит → уходит",
        frame_ids=[10, 11],
    )
    assert [op["frame_number"] for op in ops] == [1, 2]
    assert all(op["type"] == "image_ai_change" for op in ops)
    assert "ЛЕСОПОЛОСА" not in ops[0]["instruction"]
    assert "душит" in ops[0]["instruction"]
    assert "уходит" in ops[1]["instruction"]
    note = scene_image_instruction(beat="жмёт руку", passport={"place": "архив"})
    assert "архив" not in note
    assert "жмёт руку" in note
    master_note = scene_image_instruction(
        beat="душит",
        passport={"place": "архив", "characters": "Ткач, жертва"},
        master=True,
    )
    assert "душит" in master_note
    assert "лицом к камере" not in master_note
    assert "Ткач" not in master_note


@pytest.mark.asyncio
async def test_generate_with_images_falls_back_when_gpt_empty(
    session: AsyncSession, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.montage_action_gpt import generate_cell_scene_with_images

    parent_uid = "ee" * 12
    parent = Frame(
        project_id=project.id,
        number=4,
        uuid=parent_uid,
        voiceover_text="Он вышел в лес.",
        status="planned",
        sort_key=4.0,
        attrs={"camera_subdivide": {"role": "vo_parent", "parent_uuid": parent_uid}},
    )
    session.add_all([project, parent])
    await session.flush()

    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        return "нет сцен"

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", fake_ask)
    result = await generate_cell_scene_with_images(
        session,
        project,
        int(parent.id),
        operator_prompt="выходит в лес → скрывается в тени",
        passport={"place": "ЛЕСОПОЛОСА"},
        frame_ids=[int(parent.id)],
    )
    assert result["ok"] is True
    assert "выходит в лес" in (result.get("chain") or "")
    assert result["images"] == 0
    assert result["image_ops"] == []


@pytest.mark.asyncio
async def test_improve_fallback_grows_and_images_new_shots(
    session: AsyncSession, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.montage_action_gpt import generate_cell_scene_with_images

    parent_uid = "ff" * 12
    parent = Frame(
        project_id=project.id,
        number=4,
        uuid=parent_uid,
        voiceover_text="Он вышел в лес и скрылся в тени.",
        status="planned",
        sort_key=4.0,
        attrs={"camera_subdivide": {"role": "vo_parent", "parent_uuid": parent_uid}},
    )
    neighbor = Frame(
        project_id=project.id,
        number=5,
        uuid="11" * 12,
        voiceover_text="Потом вернулся домой.",
        status="planned",
        sort_key=5.0,
        attrs={"shot01_action": "дома"},
    )
    session.add_all([project, parent, neighbor])
    await session.flush()

    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        assert "выходит в лес" in text
        assert "Он вышел в лес и скрылся в тени." not in text
        assert "voiceover_text" not in text
        return "нет сцен"

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", fake_ask)
    result = await generate_cell_scene_with_images(
        session,
        project,
        int(parent.id),
        operator_prompt="выходит в лес → скрывается в тени",
        passport={"place": "ЛЕСОПОЛОСА"},
        frame_ids=[int(parent.id)],
        mode="improve",
    )
    assert result["ok"] is True
    assert result.get("mode") == "improve"
    assert int(result.get("inserted_frames") or 0) == 1
    assert result["images"] == 0
    assert result["image_ops"] == []
    nodes = [n["node"] for n in result["improve_report"]["nodes"]]
    assert nodes == ["fw_action", "fw_shots", "fw_qc", "characters"]
    acts = [s["действие"] for s in result["improve_report"]["shots"]]
    assert acts == ["выходит в лес", "скрывается в тени"]
    await session.refresh(neighbor)
    assert int(neighbor.number) == 5
    assert neighbor.voiceover_text.startswith("Потом")


def test_generate_prompt_uses_saved_vo_span() -> None:
    parent = Frame(
        project_id=1,
        number=1,
        uuid="ee" * 12,
        voiceover_text="Он вошёл в архив и снял папку с полки.",
        status="planned",
        attrs={
            "vo_cell_full": "Он вошёл в архив и снял папку с полки.",
            "закадр_выделение": {
                "start": 0,
                "end": 17,
                "text": "Он вошёл в архив",
            },
            "camera_subdivide": {"role": "vo_parent", "место": "архив"},
        },
    )
    text = build_action_generate_prompt(
        parent=parent,
        members=[parent],
        operator_prompt="",
        replace_ns=[],
        passport={"place": "архив"},
    )
    assert "Он вошёл в архив" in text
    assert "снял папку с полки" not in text
    assert "закадр_выделение" in text


@pytest.mark.asyncio
async def test_generate_cell_grows_stubs_without_images(
    session: AsyncSession, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent_uid = "ff" * 12
    parent = Frame(
        project_id=project.id,
        number=1,
        uuid=parent_uid,
        voiceover_text="Он вошёл в архив, снял папку и прочитал штамп.",
        status="planned",
        attrs={
            "place": "архив",
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
                "место": "архив",
            },
        },
    )
    session.add_all([project, parent])
    await session.flush()

    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        return json.dumps(
            {
                "ops": [
                    {
                        "frame_uuid": parent_uid,
                        "fields": {
                            "главное_действие": (
                                "1. архив — вошёл в помещение → снял папку → читает штамп\n"
                                "(Он вошёл в архив, снял папку и прочитал штамп.)"
                            )
                        },
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", fake_ask)
    result = await generate_cell_scene_action(
        session,
        project,
        int(parent.id),
        operator_prompt="разложи вход, полку и штамп",
    )
    assert result["ok"] is True
    assert int(result["inserted_frames"]) == 2
    assert int(result["skipped_shots"]) == 0
    assert "image_ops" not in result
    assert len(result["frame_numbers"]) == 3
    assert 1 in result["frame_numbers"]

