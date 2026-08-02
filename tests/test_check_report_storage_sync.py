"""checkMode excel_gpt → storage: отчёт с диска даже после wipe meta."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import Project, ProjectStatus
from app.services.gpt_operator import files_from_source_node
from app.services.storage_node import sync_downstream_storage_from_node


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(
        slug="check-stor",
        topic="t",
        status=ProjectStatus.new,
        hero_mode="no_hero",
        meta={},
    )
    p.data_dir.mkdir(parents=True)
    return p


def test_files_from_source_check_report_when_meta_wiped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """canvas PATCH затёр gpt_operator_results — берём check_report с диска."""
    p = _project(tmp_path, monkeypatch)
    gpt, store = "n_excel_gpt_6", "n_storage_1"
    up = p.data_dir / "excel_gpt_uploads" / gpt
    up.mkdir(parents=True)
    report = up / "check_report.txt"
    report.write_text(
        "ОТЧЁТ ПРОВЕРКИ\nverdict: pass\nВсё ок, персонажи на месте.\n",
        encoding="utf-8",
    )
    (up / "gpt_reply.txt").write_text("ok reply", encoding="utf-8")
    (up / "project_fixed.xlsx").write_bytes(b"PK" + b"0" * 200)
    (p.data_dir / "project.xlsx").write_bytes(b"PK" + b"1" * 200)

    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": gpt, "type": "excel_gpt", "position": {"x": 0, "y": 0}},
                {"id": store, "type": "storage", "position": {"x": 200, "y": 0}},
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": gpt,
                    "target": store,
                    "data": {"kind": "after"},
                }
            ],
        },
        "excel_gpt_nodes": {
            gpt: {
                "role": "review",
                "checkMode": True,
                "emitKinds": ["result", "reply_txt"],
                "transport": "api",
                "outputMode": "text",
            }
        },
        # как после canvas PATCH — ключ есть, значение null
        "gpt_operator_results": {gpt: None},
    }

    paths = files_from_source_node(p, gpt)
    names = {x.name for x in paths}
    assert "check_report.txt" in names
    assert "project.xlsx" not in names  # не подменять отчёт голым xlsx
    assert "project_fixed.xlsx" in names or "gpt_reply.txt" in names

    synced = sync_downstream_storage_from_node(p, gpt)
    assert synced
    copied = {Path(n).name for info in synced for n in (info.get("copied") or [])}
    # slotN_timestamp_check_report.txt
    assert any("check_report" in n for n in copied)


def test_files_from_source_check_report_plain_txt_no_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _project(tmp_path, monkeypatch)
    gpt = "n_excel_gpt_1"
    up = p.data_dir / "excel_gpt_uploads" / gpt
    up.mkdir(parents=True)
    (up / "check_report.txt").write_text("просто текстовый отчёт проверки\n", encoding="utf-8")
    p.meta = {
        "canvas_graph": {
            "nodes": [{"id": gpt, "type": "excel_gpt", "position": {"x": 0, "y": 0}}],
            "edges": [],
        },
        "excel_gpt_nodes": {
            gpt: {
                "role": "review",
                "checkMode": True,
                "emitKinds": ["reply_txt"],
            }
        },
        "gpt_operator_results": {},
    }
    names = {x.name for x in files_from_source_node(p, gpt)}
    assert "check_report.txt" in names
