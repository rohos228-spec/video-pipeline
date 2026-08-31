"""excel_gpt list — общий каталог main; группа — только по group_id."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services import prompt_library as pl
from app.web.api import create_app


@pytest.fixture()
def prompts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "prompts"
    for folder in (
        "05_excel_gpt",
        "05a_enrich_1",
        "05b_enrich_2",
        "05c_enrich_3",
        "05d_enrich_4",
        "05e_enrich_5",
    ):
        (root / folder).mkdir(parents=True)
    (root / "05_excel_gpt" / "default.md").write_text("unified default", encoding="utf-8")
    (root / "05c_enrich_3" / "legacy_only.md").write_text("from enrich_3", encoding="utf-8")
    monkeypatch.setattr(pl, "PROMPTS_ROOT", root)
    return root


def test_get_excel_gpt_list_hides_legacy_enrich_folder(prompts_root: Path) -> None:
    client = TestClient(create_app())
    listed = client.get("/api/prompt-files/excel_gpt")
    assert listed.status_code == 200
    names = {row["name"] for row in listed.json()}
    assert "legacy_only" not in names
    assert "default" in names

    r = client.get("/api/prompt-files/excel_gpt/legacy_only/content")
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "from enrich_3"


def test_stale_local_script_writer_uses_git_template(
    prompts_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (prompts_root / "05_excel_gpt" / "script_writer_ru.md").write_text(
        "# Агент: сценарист закадра RU (v1)\nпишет script_text\n",
        encoding="utf-8",
    )
    tmpl = tmp_path / "tmpl"
    tmpl.mkdir()
    (tmpl / "script_writer_ru.md").write_text("# биты v3\n", encoding="utf-8")
    monkeypatch.setattr(pl, "excel_gpt_template_dir", lambda: tmpl)
    path = pl.resolve_excel_gpt_prompt_path("script_writer_ru")
    assert path == tmpl / "script_writer_ru.md"


def test_list_script_frames_qc_group_prompts_isolated(
    prompts_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    group = tmp_path / "templates" / "node_groups" / "script_frames_qc"
    group.mkdir(parents=True)
    (group / "scenes_to_frames_ru.md").write_text("GROUP V8\n", encoding="utf-8")
    (prompts_root / "05_excel_gpt" / "common_one.md").write_text(
        "COMMON\n", encoding="utf-8"
    )
    monkeypatch.setattr("app.project_root.find_project_root", lambda: tmp_path)

    client = TestClient(create_app())
    common = client.get("/api/prompt-files/excel_gpt")
    assert common.status_code == 200
    common_names = {row["name"] for row in common.json()}
    assert "common_one" in common_names
    assert "scenes_to_frames_ru" not in common_names

    grouped = client.get(
        "/api/prompt-files/excel_gpt?group_id=script_frames_qc"
    )
    assert grouped.status_code == 200
    group_names = {row["name"] for row in grouped.json()}
    assert group_names == {"scenes_to_frames_ru"}
    assert "common_one" not in group_names

    body = client.get(
        "/api/prompt-files/excel_gpt/scenes_to_frames_ru/content"
        "?group_id=script_frames_qc"
    )
    assert body.status_code == 200
    assert body.json()["content"] == "GROUP V8\n"
