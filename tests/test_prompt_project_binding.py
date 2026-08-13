"""Guard «промт ↔ проект»: вариант с чужим серийным тегом → warning в meta.

Инцидент: excel_gpt на проекте PSYCO5459 подхватил legacy-промт
«1-25PSYCO_29.07» вместо собственного «PSYCO5459_29.07_db».
Резолв НЕ блокируется — только loud warning + meta['prompt_binding_warnings'];
явная привязка meta.prompt_slot_variants[node_key] побеждает молча.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.prompt_library import (
    read_resolved_project_prompt,
    validate_prompt_project_binding,
    write_prompt,
)

LEGACY_VARIANT = "1-25PSYCO_29.07"
OWNED_VARIANT = "PSYCO5459_29.07_db"


def _project(**kw) -> SimpleNamespace:
    base: dict = {"topic": "t", "prompt_overrides": {}, "meta": {}}
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture()
def prompts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "prompts"
    monkeypatch.setattr("app.services.prompt_library.PROMPTS_ROOT", root)
    monkeypatch.setattr("app.services.prompt_active_global.PROMPTS_ROOT", root)
    write_prompt("excel_gpt", "default", "DEFAULT")
    write_prompt("excel_gpt", LEGACY_VARIANT, "LEGACY BODY")
    write_prompt("excel_gpt", OWNED_VARIANT, "OWNED BODY")
    return root


# ── validate_prompt_project_binding (чистая функция) ───────────


def test_validate_mismatch_returns_warning() -> None:
    project = _project(title="PSYCO5459", slug="psyco5459")
    warning = validate_prompt_project_binding(
        project, LEGACY_VARIANT, node_key="n_excel_gpt_2"
    )
    assert warning is not None
    assert "25PSYCO" in warning
    assert "PSYCO5459" in warning
    assert "n_excel_gpt_2" in warning


def test_validate_matching_variant_no_warning() -> None:
    project = _project(title="PSYCO5459", slug="psyco5459")
    assert validate_prompt_project_binding(project, OWNED_VARIANT) is None


def test_validate_project_without_token_no_opinion() -> None:
    project = _project(title="nicshe", slug="nicshe")
    assert validate_prompt_project_binding(project, LEGACY_VARIANT) is None


def test_validate_token_from_slug_only() -> None:
    # slugifier всегда в lowercase — тег обязан находиться и там.
    project = _project(slug="psyco5459")
    assert validate_prompt_project_binding(project, LEGACY_VARIANT) is not None


def test_validate_token_from_meta() -> None:
    project = _project(meta={"series": "PSYCO5459"})
    assert validate_prompt_project_binding(project, LEGACY_VARIANT) is not None


def test_validate_same_digits_different_base_warns() -> None:
    project = _project(title="PSYCO5459")
    assert validate_prompt_project_binding(project, "TREVOR5459_final") is not None


def test_validate_unrelated_token_no_warning() -> None:
    # Другая база И другие цифры — не наш тег, мнения нет.
    project = _project(title="PSYCO5459")
    assert validate_prompt_project_binding(project, "GPT5_prompt") is None


def test_validate_lowercase_variant_ignored() -> None:
    # Строчные буквы с цифрами — не серийный тег (agent_54_59, psyco1).
    project = _project(title="PSYCO5459", slug="psyco5459")
    assert validate_prompt_project_binding(project, "agent_54_59_07.07") is None
    assert validate_prompt_project_binding(project, "psyco1_legacy") is None


def test_validate_default_variant_no_warning() -> None:
    project = _project(title="PSYCO5459", slug="psyco5459")
    assert validate_prompt_project_binding(project, "default") is None


# ── Проводка через read_resolved_project_prompt ────────────────


def test_resolve_records_warning_for_legacy_variant(prompts_root: Path) -> None:
    project = _project(
        title="PSYCO5459",
        slug="psyco5459",
        prompt_overrides={"excel_gpt": LEGACY_VARIANT},
    )
    name, _path, text, source = read_resolved_project_prompt(
        project, "excel_gpt", node_key="n_excel_gpt_2"
    )
    # Резолв НЕ заблокирован — legacy-вариант всё ещё возвращается.
    assert name == LEGACY_VARIANT
    assert source == "override"
    assert "LEGACY BODY" in text
    warnings = project.meta.get("prompt_binding_warnings")
    assert isinstance(warnings, list) and len(warnings) == 1
    entry = warnings[0]
    assert entry["kind"] == "prompt_binding_mismatch"
    assert entry["variant"] == LEGACY_VARIANT
    assert entry["project_token"] == "PSYCO5459"
    assert entry["node_key"] == "n_excel_gpt_2"
    assert entry["step"] == "excel_gpt"
    assert entry["source"] == "override"


def test_resolve_matching_variant_records_no_warning(prompts_root: Path) -> None:
    project = _project(
        title="PSYCO5459",
        slug="psyco5459",
        prompt_overrides={"excel_gpt": OWNED_VARIANT},
    )
    name, _path, text, _source = read_resolved_project_prompt(
        project, "excel_gpt", node_key="n_excel_gpt_2"
    )
    assert name == OWNED_VARIANT
    assert "OWNED BODY" in text
    assert "prompt_binding_warnings" not in project.meta


def test_explicit_slot_binding_wins_silently(prompts_root: Path) -> None:
    """Явная привязка ноды в prompt_slot_variants — выбор оператора, молчим."""
    project = _project(
        title="PSYCO5459",
        slug="psyco5459",
        meta={"prompt_slot_variants": {"n_excel_gpt_1": {"main": LEGACY_VARIANT}}},
    )
    name, _path, _text, source = read_resolved_project_prompt(
        project, "excel_gpt", node_key="n_excel_gpt_1", slot_id="main"
    )
    assert name == LEGACY_VARIANT
    assert source == "slot"
    assert "prompt_binding_warnings" not in project.meta


def test_project_without_token_records_no_warning(prompts_root: Path) -> None:
    project = _project(
        title="nicshe",
        slug="nicshe",
        prompt_overrides={"excel_gpt": LEGACY_VARIANT},
    )
    name, _path, _text, _source = read_resolved_project_prompt(project, "excel_gpt")
    assert name == LEGACY_VARIANT
    assert "prompt_binding_warnings" not in project.meta


def test_warning_dedupes_same_variant(prompts_root: Path) -> None:
    """Повторные резолвы того же шага не раздувают список предупреждений."""
    project = _project(
        title="PSYCO5459",
        slug="psyco5459",
        prompt_overrides={"excel_gpt": LEGACY_VARIANT},
    )
    for _ in range(3):
        read_resolved_project_prompt(project, "excel_gpt", node_key="n_excel_gpt_2")
    warnings = project.meta.get("prompt_binding_warnings")
    assert isinstance(warnings, list) and len(warnings) == 1
