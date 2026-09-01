"""Character registry + hero: Entity / db_frames SoT (не Excel)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.orchestrator.steps.enrich_xlsx import (
    _is_character_registry_prompt,
    _is_scene_grammar_prompt,
    _is_script_writer_node,
    _is_script_writer_prompt,
)
from app.services.excel_characters import (
    ExcelCharacter,
    characters_from_entities,
    entity_cards_for_gpt,
)


def test_detect_character_registry_prompt() -> None:
    assert _is_character_registry_prompt(
        "агент по созданию персонажей 02.08.txt.md", None
    )
    assert _is_character_registry_prompt(
        None, "Агент: character_registry_database_agent_v3_web_verified\n"
    )
    assert not _is_character_registry_prompt("scene_grammar_unified_agent_v1", None)
    assert _is_scene_grammar_prompt("scene_grammar_unified_agent_v1", None)


def test_detect_script_writer_prompt() -> None:
    assert _is_script_writer_prompt("script_writer_ru.md", None)
    assert _is_script_writer_prompt(None, "Ты — сценарист закадра.\n")
    assert not _is_script_writer_prompt("sd_skeleton.md", "shot fill")
    assert _is_script_writer_node("script_writer_ru.md", None, "n_excel_gpt_fw_script")
    assert _is_script_writer_node(None, None, "n_excel_gpt_fw_script")
    assert not _is_script_writer_node("frame_prompts_continuity_ru.md", None, "n_excel_gpt_fw_frames")
    assert not _is_script_writer_node(
        "main_action_from_bits_ru.md", None, "n_excel_gpt_fw_action"
    )
    assert not _is_script_writer_node(
        "scenes_to_frames_ru.md", None, "n_excel_gpt_fw_shots"
    )


def test_script_frames_qc_group_batches_of_30() -> None:
    from app.orchestrator.steps.enrich_xlsx import (
        _is_script_frames_qc_group_node,
        _script_frames_qc_footer_kind,
    )
    from app.services.apply_ops_batches import SCRIPT_FRAMES_QC_PARALLEL_BATCHES

    assert SCRIPT_FRAMES_QC_PARALLEL_BATCHES == 6
    for key, kind in (
        ("n_excel_gpt_fw_script", "bits"),
        ("n_excel_gpt_fw_action", "action_chain"),
        ("n_excel_gpt_fw_shots", "shots_coverage"),
        ("n_excel_gpt_fw_frames", "prompts"),
        ("n_excel_gpt_fw_qc", "prompts"),
        ("n_excel_gpt_fw_report", "report"),
    ):
        assert _is_script_frames_qc_group_node(None, None, key)
        assert _script_frames_qc_footer_kind(None, None, key) == kind
    assert not _is_script_frames_qc_group_node(
        "sd_skeleton.md", None, "n_excel_gpt_1"
    )
    # Шапка промта картинок цитирует «сцены → кадры» — ключ ноды важнее.
    frames_master = (
        "Нода: excel_gpt · после «сцены → кадры».\n"
        "Пишет промт_картинки для родителя.\n"
    )
    from app.orchestrator.steps.enrich_xlsx import _is_scenes_to_frames_node

    assert not _is_scenes_to_frames_node(
        "frame_prompts_continuity_ru.md", frames_master, "n_excel_gpt_fw_frames"
    )
    assert (
        _script_frames_qc_footer_kind(
            "frame_prompts_continuity_ru.md",
            frames_master,
            "n_excel_gpt_fw_frames",
        )
        == "prompts"
    )
    assert _is_scenes_to_frames_node(
        "scenes_to_frames_ru.md", "агент: сцены → кадры", "n_excel_gpt_fw_shots"
    )


def test_detect_frame_and_qc_prompt() -> None:
    from app.orchestrator.steps.enrich_xlsx import (
        _all_frame_prompts_ready,
        _is_frame_prompts_prompt,
        _is_qc_prompts_prompt,
    )

    assert _is_frame_prompts_prompt("frame_prompts_continuity_ru.md", None)
    assert _is_qc_prompts_prompt("prompts_qc_continuity_ru.md", None)
    assert not _is_frame_prompts_prompt("script_writer_ru.md", None)
    assert not _is_frame_prompts_prompt("scenes_to_frames_ru.md", None)
    assert not _is_qc_prompts_prompt("scenes_to_frames_ru.md", None)
    qc_body = (
        "Нода: excel_gpt. ПОСЛЕ агента-конвертера\n"
        "(frame_prompts_continuity) и ДО генерации картинок.\n"
    )
    assert _is_qc_prompts_prompt("prompts_qc_continuity_ru", qc_body)
    assert not _is_frame_prompts_prompt("prompts_qc_continuity_ru", qc_body)
    ready = [
        SimpleNamespace(
            image_prompt="img",
            animation_prompt="mov",
            attrs={"shot01_action": "шаг"},
        ),
        SimpleNamespace(
            image_prompt="img2",
            animation_prompt="mov2",
            attrs={"shot01_action": "ещё шаг"},
        ),
    ]
    assert _all_frame_prompts_ready(ready)
    assert not _all_frame_prompts_ready(ready[:1])
    missing = [
        SimpleNamespace(
            image_prompt="img",
            animation_prompt="mov",
            attrs={"shot01_action": "шаг"},
        ),
        SimpleNamespace(image_prompt="img2", animation_prompt=""),
    ]
    assert not _all_frame_prompts_ready(missing)
    no_action = [
        SimpleNamespace(image_prompt="img", animation_prompt="mov", attrs={}),
        SimpleNamespace(image_prompt="img2", animation_prompt="mov2", attrs={}),
    ]
    assert not _all_frame_prompts_ready(no_action)


def test_project_topic_bits_uses_title_not_name() -> None:
    """Project.title, не .name — иначе ▶ сценария падает AttributeError."""
    from app.orchestrator.steps.enrich_xlsx import _project_topic_bits

    class Proj:
        title = "Ткач"
        general_plan = "двор и армия"
        meta = {}

    bits = _project_topic_bits(Proj())
    assert bits[0] == "Название проекта: Ткач"
    assert "двор и армия" in bits[1]


def test_characters_from_entities_and_gpt_cards() -> None:
    ents = [
        SimpleNamespace(
            type="character",
            code="c01",
            name="Асаahara",
            sort_key=10.0,
            attrs={
                "внешность": "мужчина в очках",
                "одежда": "белый халат",
                "характер": "спокойный",
                "правила": "",
            },
        ),
        SimpleNamespace(
            type="character",
            code="c02",
            name="оставь формат неизменный",
            sort_key=20.0,
            attrs={
                "look": "моложе",
                "clothes": "толстовка",
                "char": "резче",
                "rules": "c01",
            },
        ),
        SimpleNamespace(type="background", code="f01", name="метро", attrs={}),
    ]
    chars = characters_from_entities(ents)
    assert [c.id for c in chars] == ["c01", "c02"]
    assert chars[1].ref_ids == ["c01"]
    cards = entity_cards_for_gpt(ents)
    assert cards[0]["имя"] == "Асаahara"
    assert cards[0]["внешность"] == "мужчина в очках"
    assert cards[1]["правила"] == "c01"


@pytest.mark.asyncio
async def test_hero_loads_from_entity_not_xlsx(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from app.models import Project, ProjectStatus
    from app.orchestrator.steps import generate_hero
    from app.settings import settings

    slug = "aum-chars"
    videos = tmp_path / "videos" / slug
    videos.mkdir(parents=True)
    # xlsx отсутствует — Entity должен хватить
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    fake = [ExcelCharacter(id="c01", name="Герой", look="лицо")]

    async def _ents(_session, _project):
        return fake

    monkeypatch.setattr(generate_hero, "_load_entity_characters", _ents)
    monkeypatch.setattr(
        "app.services.excel_characters.parse_persons_sheet",
        lambda _p: (_ for _ in ()).throw(AssertionError("xlsx must not be used")),
    )

    p = Project(topic="t", slug=slug, hero_mode="auto")
    p.status = ProjectStatus.generating_hero
    p.meta = {}
    session = AsyncMock()
    session.flush = AsyncMock()

    cfg = await generate_hero._load_excel_hero_from_xlsx(session, p)
    assert cfg is not None
    assert cfg["source"] == "entity"
    assert cfg["characters"][0]["id"] == "c01"


@pytest.mark.asyncio
async def test_hero_falls_back_to_xlsx_when_entity_empty(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from app.models import Project, ProjectStatus
    from app.orchestrator.steps import generate_hero
    from app.settings import settings

    slug = "aum-chars-xlsx"
    videos = tmp_path / "videos" / slug
    videos.mkdir(parents=True)
    (videos / "project.xlsx").write_bytes(b"fake")
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    async def _ents(_session, _project):
        return []

    monkeypatch.setattr(generate_hero, "_load_entity_characters", _ents)
    fake = ExcelCharacter(id="c01", name="ИзExcel", look="лицо")
    monkeypatch.setattr(
        "app.services.excel_characters.parse_persons_sheet",
        lambda _p: [fake],
    )

    p = Project(topic="t", slug=slug, hero_mode="auto")
    p.status = ProjectStatus.generating_hero
    p.meta = {}
    session = AsyncMock()
    session.flush = AsyncMock()

    cfg = await generate_hero._load_excel_hero_from_xlsx(session, p)
    assert cfg is not None
    assert cfg["source"] == "xlsx"
    assert cfg["characters"][0]["name"] == "ИзExcel"
