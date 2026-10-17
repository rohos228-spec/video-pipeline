"""resolve_project_prompt_name учитывает meta.prompt_slot_variants (Node Studio)."""

from __future__ import annotations

from unittest.mock import patch

from app.services.prompt_library import resolve_project_prompt_name


def _fake_prompt_path(_step: str, _name: str):
    class _P:
        def exists(self) -> bool:
            return True

        def is_file(self) -> bool:
            return True

    return _P()


def test_resolve_prefers_prompt_overrides_over_stale_meta_slot() -> None:
    """Активный override проекта важнее чужой ноды в meta."""
    meta = {
        "prompt_slot_variants": {
            "n_old": {"main": "default"},
            "n_enrich_1": {"main": "От клода"},
        }
    }
    overrides = {"enrich_1": "От клода"}
    with patch(
        "app.services.prompt_library.prompt_path",
        side_effect=_fake_prompt_path,
    ), patch(
        "app.services.prompt_library.excel_gpt_prompt_exists",
        return_value=True,
    ):
        name = resolve_project_prompt_name(overrides, "enrich_1", meta=meta)
    assert name == "От клода"


def test_resolve_uses_meta_when_no_override() -> None:
    meta = {"prompt_slot_variants": {"n_enrich_1": {"main": "custom_slot"}}}
    with patch(
        "app.services.prompt_library.prompt_path",
        side_effect=_fake_prompt_path,
    ), patch(
        "app.services.prompt_library.excel_gpt_prompt_exists",
        return_value=True,
    ):
        name = resolve_project_prompt_name({}, "enrich_1", meta=meta)
    assert name == "custom_slot"


def test_excel_gpt_registry_agent_is_not_stolen_as_hero() -> None:
    """Копия агента реестра в 04_hero + слот excel_gpt не должна ломать Hero."""
    from app.services.hero_prompt_contract import hero_master_looks_like_registry_agent
    from app.services.prompt_library import (
        resolve_project_prompt_with_source,
        prompt_path,
    )

    registry_name = "агент по созданию персонажей 02.08.txt"
    hero_copy = prompt_path("hero", registry_name)
    assert hero_copy.is_file(), "регресс: копия агента в 04_hero пропала — тест не ловит баг"
    assert hero_master_looks_like_registry_agent(
        hero_copy.read_text(encoding="utf-8")
    )
    meta = {
        "prompt_slot_variants": {
            "n_excel_gpt_1788694559747": {"main": registry_name},
            "n_excel_gpt_fw_frames": {"main": "frame_prompts_continuity_ru"},
        }
    }
    name, source = resolve_project_prompt_with_source({}, "hero", meta=meta)
    assert name != registry_name
    sheet = prompt_path("hero", name)
    assert sheet.is_file()
    text = sheet.read_text(encoding="utf-8")
    assert not hero_master_looks_like_registry_agent(text)
    from app.services.hero_prompt_contract import hero_master_looks_like_sheet

    assert hero_master_looks_like_sheet(text)
    assert source in {"default", "global", "slot", "override"}


def test_img_pr_trash_polka_is_not_used_as_hero_style() -> None:
    """Глобальный шаблон кадров не должен становиться стилем листа персонажа."""
    from app.services.hero_prompt_contract import (
        hero_style_looks_like_character_lock,
        hero_style_looks_like_img_pr_template,
    )
    from app.services.prompt_library import (
        prompt_path,
        resolve_project_prompt_with_source,
    )

    frame_template = "треш полька акварель_20260802_200516.txt"
    char_lock = "треш полька акварель_ДЛЯ ПЕРСОНАЖА02.08.txt"
    frame_path = prompt_path("hero_style", frame_template)
    lock_path = prompt_path("hero_style", char_lock)
    assert frame_path.is_file()
    assert lock_path.is_file()
    assert hero_style_looks_like_img_pr_template(
        frame_path.read_text(encoding="utf-8")
    )
    assert hero_style_looks_like_character_lock(
        lock_path.read_text(encoding="utf-8")
    )
    name, source = resolve_project_prompt_with_source(
        {"hero_style": frame_template},
        "hero_style",
        meta={"prompt_slot_variants": {}},
    )
    assert name != frame_template
    assert "персонаж" in name.lower()
    text = prompt_path("hero_style", name).read_text(encoding="utf-8")
    assert hero_style_looks_like_character_lock(text)
    assert source in {"default", "global", "slot", "override"}


def test_resolve_falls_back_to_prompt_overrides() -> None:
    meta: dict = {}
    overrides = {"enrich_1": "custom_slot"}
    with patch(
        "app.services.prompt_library.prompt_path",
        side_effect=_fake_prompt_path,
    ), patch(
        "app.services.prompt_library.excel_gpt_prompt_exists",
        return_value=True,
    ):
        name = resolve_project_prompt_name(overrides, "enrich_1", meta=meta)
    assert name == "custom_slot"
