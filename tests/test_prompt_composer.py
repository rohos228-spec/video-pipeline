"""Тесты для app/services/prompt_composer.py: сборка блоков v2, веса, свой
текст, обратная совместимость, и структура шаблонов шагов (5-7 блоков,
обязательный технический блок первым)."""

from __future__ import annotations

import re

import pytest

from app.services import prompt_composer as pc

HEADER_RE = re.compile(r"^## (\d+)\.\s+(.+)$", re.MULTILINE)


# ── resolve_block_value: обратная совместимость + новый формат ──────────


def test_resolve_block_value_legacy_string() -> None:
    text, weight = pc.resolve_block_value("world", "cats_anthropomorphic")
    assert weight == 1.0
    assert "антропоморф" in text.lower() or len(text) > 0


def test_resolve_block_value_none_uses_default() -> None:
    text, weight = pc.resolve_block_value("world", None)
    assert weight == 1.0
    assert text and not text.startswith("<!--")


def test_resolve_block_value_missing_file_is_safe() -> None:
    text, weight = pc.resolve_block_value("world", "does_not_exist_xyz")
    assert weight == 1.0
    assert text.startswith("<!-- block file missing:")


def test_resolve_block_value_missing_category_is_safe() -> None:
    text, weight = pc.resolve_block_value("no_such_category", None)
    assert text.startswith("<!-- missing block:")


def test_resolve_block_value_dict_with_weight() -> None:
    text, weight = pc.resolve_block_value(
        "world", {"name": "cats_anthropomorphic", "weight": 0.5}
    )
    assert weight == 0.5
    assert not text.startswith("<!--")


def test_resolve_block_value_dict_with_custom_text() -> None:
    text, weight = pc.resolve_block_value(
        "lighting", {"text": "мягкий боковой свет", "weight": 0.9}
    )
    assert text == "мягкий боковой свет"
    assert weight == 0.9


def test_resolve_block_value_dict_custom_text_wins_over_name() -> None:
    text, _ = pc.resolve_block_value(
        "world", {"name": "cats_anthropomorphic", "text": "свой мир"}
    )
    assert text == "свой мир"


def test_resolve_block_value_invalid_type_is_safe() -> None:
    text, weight = pc.resolve_block_value("world", 12345)  # type: ignore[arg-type]
    assert text.startswith("<!-- invalid block config:")
    assert weight == 1.0


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.5, 0.5),
        (-1, 0.0),
        (2, 1.0),
        ("0.3", 0.3),
        ("not-a-number", 1.0),
        (None, 1.0),
        (float("nan"), 1.0),
    ],
)
def test_clamp_weight(raw: object, expected: float) -> None:
    assert pc.clamp_weight(raw, default=1.0) == expected


# ── compose_step: вес влияет на текст, дефолт не помечается ─────────────


def test_compose_step_default_weight_has_no_prefix() -> None:
    blocks = dict(pc.DEFAULT_BLOCKS)
    vars_ = dict(pc.DEFAULT_VARS)
    vars_["PROJECT_TOPIC"] = "тест"
    text = pc.compose_step("01_plan", blocks, vars_)
    assert "[ВАЖНО" not in text
    assert "[учитывай" not in text
    assert "[фоновый" not in text


def test_compose_step_low_weight_adds_background_label() -> None:
    blocks: dict[str, pc.BlockValue] = dict(pc.DEFAULT_BLOCKS)
    blocks["voice_tone"] = {"name": "documentary_calm", "weight": 0.2}
    vars_ = dict(pc.DEFAULT_VARS)
    vars_["PROJECT_TOPIC"] = "тест"
    text = pc.compose_step("02_script", blocks, vars_)
    assert "[фоновый акцент, второстепенно]" in text


def test_compose_step_custom_text_block_supports_var_substitution() -> None:
    blocks: dict[str, pc.BlockValue] = dict(pc.DEFAULT_BLOCKS)
    blocks["voice_tone"] = {"text": "тон для видео {{VAR:ASPECT_RATIO_VIDEO}}"}
    vars_ = dict(pc.DEFAULT_VARS)
    vars_["PROJECT_TOPIC"] = "тест"
    text = pc.compose_step("02_script", blocks, vars_)
    assert "тон для видео 9:16" in text


def test_compose_step_no_dangling_placeholders_with_defaults() -> None:
    """Со стандартными blocks/vars ни один шаблон не должен оставлять
    неразрешённый {{BLOCK:}}/{{VAR:}} — иначе в промт уйдёт мусор."""
    vars_ = dict(pc.DEFAULT_VARS)
    vars_["PROJECT_TOPIC"] = "тест"
    vars_["HERO_DESCRIPTION"] = "герой-кот"
    vars_["ITEM_STYLE_NOTE"] = "нейтральный фон"
    for i in range(1, 6):
        vars_[f"ENRICH_{i}_TASK"] = "тестовая задача"
    for step_id in pc.list_step_templates():
        text = pc.compose_step(step_id, dict(pc.DEFAULT_BLOCKS), vars_)
        assert "<!-- missing var" not in text, f"{step_id}: missing var"
        assert "<!-- missing block" not in text, f"{step_id}: missing block"
        assert "<!-- block file missing" not in text, f"{step_id}: missing block file"


# ── project_uses_blocks_v2 / merge_project_prompt_config ────────────────


def test_merge_project_prompt_config_preserves_dict_block_value() -> None:
    overrides = {"blocks": {"world": {"name": "cats_anthropomorphic", "weight": 0.4}}}
    blocks, _ = pc.merge_project_prompt_config(overrides)
    assert blocks["world"] == {"name": "cats_anthropomorphic", "weight": 0.4}


def test_merge_project_prompt_config_legacy_string_values_still_work() -> None:
    overrides = {"blocks": {"world": "cats_anthropomorphic"}}
    blocks, _ = pc.merge_project_prompt_config(overrides)
    assert blocks["world"] == "cats_anthropomorphic"


def test_project_uses_blocks_v2() -> None:
    assert pc.project_uses_blocks_v2({"use_blocks_v2": True}) is True
    assert pc.project_uses_blocks_v2({"blocks": {"world": "x"}}) is True
    assert pc.project_uses_blocks_v2({}) is False
    assert pc.project_uses_blocks_v2(None) is False


# ── Структура шаблонов: 5-7 блоков, технический блок первым ─────────────


ALL_STEP_TEMPLATES = pc.list_step_templates()


def test_all_expected_steps_have_templates() -> None:
    """Каждый шаг из NODE_TYPE_TO_STEP должен иметь steps/<id>/template.md —
    это и есть покрытие blocks v2 (см. docs/PROMPTS_BLOCKS.md §8)."""
    expected = set(pc.NODE_TYPE_TO_STEP.values())
    assert expected.issubset(set(ALL_STEP_TEMPLATES))


@pytest.mark.parametrize("step_id", ALL_STEP_TEMPLATES)
def test_template_has_5_to_7_top_level_blocks(step_id: str) -> None:
    text = pc._read_template(step_id)  # noqa: SLF001
    headers = HEADER_RE.findall(text)
    numbers = [int(n) for n, _ in headers]
    assert 5 <= len(headers) <= 7, f"{step_id}: expected 5-7 blocks, got {len(headers)}"
    assert numbers == list(range(1, len(headers) + 1)), f"{step_id}: blocks must be numbered 1..N in order"


@pytest.mark.parametrize("step_id", ALL_STEP_TEMPLATES)
def test_template_first_block_is_technical(step_id: str) -> None:
    text = pc._read_template(step_id)  # noqa: SLF001
    headers = HEADER_RE.findall(text)
    assert headers, f"{step_id}: no blocks found"
    first_title = headers[0][1].upper()
    assert "ТЕХНИЧЕСКАЯ ЧАСТЬ" in first_title, f"{step_id}: first block must be technical, got {first_title!r}"


@pytest.mark.parametrize("step_id", ALL_STEP_TEMPLATES)
def test_technical_block_documents_read_write_attention(step_id: str) -> None:
    text = pc._read_template(step_id)  # noqa: SLF001
    headers = list(HEADER_RE.finditer(text))
    assert headers
    start = headers[0].end()
    end = headers[1].start() if len(headers) > 1 else len(text)
    technical_body = text[start:end].lower()
    assert "откуда чита" in technical_body, f"{step_id}: technical block missing 'откуда читаю'"
    assert "куда пиш" in technical_body, f"{step_id}: technical block missing 'куда пишу'"
    assert "внимание" in technical_body, f"{step_id}: technical block missing attention notes"


# ── step_block_categories ────────────────────────────────────────────────


def test_step_block_categories_matches_placeholders_in_template() -> None:
    text = pc._read_template("01_plan")  # noqa: SLF001
    expected = set(pc.BLOCK_RE.findall(text))
    assert set(pc.step_block_categories("01_plan")) == expected


def test_step_block_categories_empty_for_enrich_steps() -> None:
    # enrich-шаблоны намеренно не используют категории {{BLOCK:}} — их
    # содержимое полностью произвольное (см. docs/PROMPTS_BLOCKS.md §2).
    for step_id in ("05a_enrich_1", "05b_enrich_2", "05c_enrich_3", "05d_enrich_4", "05e_enrich_5"):
        assert pc.step_block_categories(step_id) == []


def test_step_block_categories_unknown_step_returns_empty() -> None:
    assert pc.step_block_categories("no_such_step") == []
