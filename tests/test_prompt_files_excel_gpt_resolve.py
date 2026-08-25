"""excel_gpt list — только 05_excel_gpt; legacy enrich_* в списке не светится."""

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
