"""Единая папка промтов excel_gpt для всех нод «Работа с GPT»."""

from __future__ import annotations

import pytest

from app.services.prompt_library import (
    EXCEL_GPT_UNIFIED_STEP,
    list_excel_gpt_prompts,
    list_prompts,
    read_prompt,
    resolve_project_prompt_name,
    write_prompt,
)
from tests.conftest import patch_prompt_roots


@pytest.fixture
def prompt_dirs(tmp_path, monkeypatch):
    return patch_prompt_roots(
        monkeypatch,
        tmp_path,
        folders=("05_excel_gpt", "05a_enrich_1", "05b_enrich_2"),
    )


def test_list_merges_excel_gpt_and_legacy_enrich(prompt_dirs):
    write_prompt("enrich_1", "from_slot1", "a")
    write_prompt("excel_gpt", "from_unified", "b")
    write_prompt("enrich_2", "legacy_only", "c")
    names = list_excel_gpt_prompts()
    assert "from_slot1" in names
    assert "from_unified" in names
    assert "legacy_only" in names
    assert list_prompts("enrich_3") == names


def test_read_legacy_enrich_file_via_excel_gpt_step(prompt_dirs):
    write_prompt("enrich_1", "old_prompt", "legacy text")
    assert read_prompt(EXCEL_GPT_UNIFIED_STEP, "old_prompt") == "legacy text"


def test_write_always_goes_to_unified_folder(prompt_dirs):
    _bundled, user = prompt_dirs
    write_prompt("enrich_2", "new_one", "unified")
    assert (user / "05_excel_gpt" / "new_one.md").is_file()
    assert not (user / "05b_enrich_2" / "new_one.md").exists()


def test_resolve_across_enrich_overrides(prompt_dirs):
    write_prompt("excel_gpt", "shared", "x")
    name = resolve_project_prompt_name({"enrich_1": "shared"}, "enrich_2", meta={})
    assert name == "shared"
