"""Каталог шаблонов T/X для ноды shots."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.prompt_library import resolve_excel_gpt_prompt_path
from app.services.shot_templates import (
    clear_shot_templates_cache,
    format_shot_templates_catalog,
    load_shot_templates,
    neighbor_place_hints,
)


def test_load_shot_templates_has_t1_and_select() -> None:
    clear_shot_templates_cache()
    data = load_shot_templates()
    ids = {t["id"] for t in data["templates"]}
    assert "T0" in ids and "T1" in ids and "T2" in ids and "X1" in ids
    assert data["select"][0]["then_template"] == "T1"
    shots = [s for s in data["shots"] if s["template_id"] == "T2"]
    assert any(s.get("required") == 1 for s in shots)


def test_catalog_mentions_drop_order_and_keep_sides() -> None:
    clear_shot_templates_cache()
    text = format_shot_templates_catalog()
    assert "ШАБЛОНЫ СЦЕНА→КАДРЫ" in text
    assert "drop_order" in text or "drop=" in text
    assert "keep_sides" in text
    assert "T1|" in text


def test_neighbor_place_hints_prev() -> None:
    frames = [
        SimpleNamespace(
            uuid="aa" * 8,
            number=1,
            attrs={"главное_действие": "1. двор — мальчик бегает\n(текст)"},
        ),
        SimpleNamespace(
            uuid="bb" * 8,
            number=2,
            attrs={"главное_действие": "1. двор — раскладывает вещи\n(текст)"},
        ),
        SimpleNamespace(
            uuid="cc" * 8,
            number=3,
            attrs={"главное_действие": "1. зал суда — приговор\n(текст)"},
        ),
    ]
    text = neighbor_place_hints(frames)
    assert "prev_place≈двор" in text
    assert "number=3" in text


def test_scenes_to_frames_resolves_v8_template() -> None:
    from app.services.prompt_library import (
        SCRIPT_FRAMES_QC_PROMPT_NAMES,
        list_excel_gpt_prompts,
        list_group_owned_prompts,
    )

    path = resolve_excel_gpt_prompt_path("scenes_to_frames_ru")
    assert "node_groups/script_frames_qc" in str(path).replace("\\", "/")
    text = path.read_text(encoding="utf-8")
    assert "v8 — шаблоны" in text or "шаблоны T/X" in text
    assert "drop_order" in text
    listed = list_excel_gpt_prompts()
    for name in SCRIPT_FRAMES_QC_PROMPT_NAMES:
        assert name not in listed
    grouped = list_group_owned_prompts("script_frames_qc")
    assert grouped is not None
    assert "scenes_to_frames_ru" in grouped
    assert list_group_owned_prompts("scene_design_fanout") is None


def test_write_group_prompt_stays_out_of_excel_gpt(
    tmp_path, monkeypatch
) -> None:
    from app.services import prompt_library as pl

    group_dir = tmp_path / "templates" / "node_groups" / "script_frames_qc"
    group_dir.mkdir(parents=True)
    excel_dir = tmp_path / "prompts" / "05_excel_gpt"
    excel_dir.mkdir(parents=True)
    monkeypatch.setattr("app.project_root.find_project_root", lambda: tmp_path)
    monkeypatch.setattr(pl, "PROMPTS_ROOT", tmp_path / "prompts")

    pl.write_prompt("excel_gpt", "scenes_to_frames_ru", "GROUP BODY\n")
    group = group_dir / "scenes_to_frames_ru.md"
    common = excel_dir / "scenes_to_frames_ru.md"
    assert group.read_text(encoding="utf-8") == "GROUP BODY\n"
    assert not common.exists()
    assert "scenes_to_frames_ru" not in pl.list_excel_gpt_prompts()
    assert pl.resolve_excel_gpt_prompt_path("scenes_to_frames_ru") == group
