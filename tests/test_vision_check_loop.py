"""DB-aware vision check: parsers + loop meta (hero/scenes)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import settings as app_settings
from app.models import Project, ProjectStatus
from app.services.check_analysis import (
    append_vision_check_hint,
    extract_db_patch,
    extract_frame_regen_targets,
    extract_hero_regen_ids,
)
from app.services import vision_check_loop as vcl
from app.services.excel_gpt_node import upload_dir


def test_extract_frame_regen_line() -> None:
    text = """
# ОТЧЁТ ПРОВЕРКИ
verdict: fail

## summary
плохо

frames: 3, 7s2, 12
"""
    got = extract_frame_regen_targets(text)
    assert got == [
        {"number": 3, "shot": 1},
        {"number": 7, "shot": 2},
        {"number": 12, "shot": 1},
    ]


def test_extract_frame_from_findings() -> None:
    text = """
# ОТЧЁТ ПРОВЕРКИ
verdict: fail

## findings
- [error] frame_003_abcd.png не тот персонаж
- [ok] frame_001_x.png ок

## actions
нужен переген
"""
    ids = extract_frame_regen_targets(text)
    assert {"number": 3, "shot": 1} in ids
    assert all(t["number"] != 1 for t in ids)


def test_extract_db_patch_fenced() -> None:
    text = """
verdict: fail
regen: c02

## db_patch
```json
{
  "characters": [{"id": "c02", "внешность": "длинные волосы"}],
  "ops": [{"frame_uuid": "u-1", "fields": {"промт_картинки": "new"}}]
}
```
"""
    patch = extract_db_patch(text)
    assert patch is not None
    assert patch["characters"][0]["id"] == "c02"
    assert patch["ops"][0]["target"] == "frame"
    assert patch["ops"][0]["frame_uuid"] == "u-1"


def test_append_vision_hint_idempotent() -> None:
    once = append_vision_check_hint("проверь")
    assert "db_patch" in once
    assert "ТЕКСТ НА КАРТИНКЕ" in once
    assert "РАКУРСЫ ПЕРСОНАЖА" in once
    twice = append_vision_check_hint(once)
    assert twice.count("vision_check") == once.count("vision_check")


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slug: str) -> Project:
    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(
        slug=slug,
        topic="t",
        status=ProjectStatus.enrich_1_ready,
        hero_mode="no_hero",
        meta={},
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    return p


def test_start_hero_loop_via_vision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _project(tmp_path, monkeypatch, "vh")
    check_key = "n_check"
    p.meta = {
        "excel_gpt_nodes": {
            check_key: {"checkMode": True, "checkFix": False, "slotIndex": 1},
        },
        "gpt_operator_results": {check_key: {"gateStatus": "fail"}},
        "canvas_graph": {
            "nodes": [
                {"id": "n_hero", "type": "hero"},
                {
                    "id": check_key,
                    "type": "excel_gpt",
                    "data": {"slotIndex": 1, "checkMode": True},
                },
            ],
            "edges": [
                {
                    "source": "n_hero",
                    "target": check_key,
                    "data": {"kind": "after"},
                }
            ],
        },
    }
    out = upload_dir(p, check_key)
    out.mkdir(parents=True, exist_ok=True)
    (out / "check_report.txt").write_text(
        "# ОТЧЁТ ПРОВЕРКИ\nverdict: fail\n\nregen: c01, c02\n",
        encoding="utf-8",
    )

    async def _fake_prepare(*_a, **_k):
        return True

    monkeypatch.setattr(
        "app.services.run_sync.prepare_node_for_step_start", _fake_prepare
    )

    class _Sess:
        async def flush(self):
            return None

    started = asyncio.run(
        vcl.maybe_start_vision_check_loop_after_check(_Sess(), p, check_key)
    )
    assert started is True
    assert p.status is ProjectStatus.generating_hero
    assert p.meta["hero_check_regen_ids"] == ["c01", "c02"]
    assert p.meta["vision_check_kind"] == "hero"
    assert p.meta["vision_check_return_node"] == check_key


def test_start_scenes_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _project(tmp_path, monkeypatch, "vs")
    check_key = "n_check_img"
    p.meta = {
        "excel_gpt_nodes": {
            check_key: {"checkMode": True, "checkFix": False, "slotIndex": 2},
        },
        "gpt_operator_results": {check_key: {"gateStatus": "fail"}},
        "canvas_graph": {
            "nodes": [
                {"id": "n_img", "type": "images"},
                {
                    "id": check_key,
                    "type": "excel_gpt",
                    "data": {"slotIndex": 2, "checkMode": True},
                },
            ],
            "edges": [
                {
                    "source": "n_img",
                    "target": check_key,
                    "data": {"kind": "after"},
                }
            ],
        },
    }
    scenes = p.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    png = scenes / "frame_003_abcd1234.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 80)

    out = upload_dir(p, check_key)
    out.mkdir(parents=True, exist_ok=True)
    (out / "check_report.txt").write_text(
        "# ОТЧЁТ ПРОВЕРКИ\nverdict: fail\n\nframes: 3, 5s2\n",
        encoding="utf-8",
    )

    async def _fake_prepare(*_a, **_k):
        return True

    monkeypatch.setattr(
        "app.services.run_sync.prepare_node_for_step_start", _fake_prepare
    )

    class _Sess:
        async def flush(self):
            return None

        async def execute(self, *_a, **_k):
            class _R:
                def scalars(self):
                    return SimpleNamespace(all=lambda: [])

            return _R()

        async def delete(self, *_a, **_k):
            return None

    started = asyncio.run(
        vcl.maybe_start_vision_check_loop_after_check(_Sess(), p, check_key)
    )
    assert started is True
    assert p.status is ProjectStatus.generating_images
    assert vcl.get_scene_check_regen(p) == [
        {"number": 3, "shot": 1},
        {"number": 5, "shot": 2},
    ]
    assert not png.exists()


def test_scene_regen_allows() -> None:
    p = Project(slug="x", topic="t", status=ProjectStatus.generating_images, meta={})
    assert vcl.scene_regen_allows(p, 1, 1) is None
    p.meta = {"scene_check_regen": [{"number": 3, "shot": 1}]}
    assert vcl.scene_regen_allows(p, 3, 1) is True
    assert vcl.scene_regen_allows(p, 1, 1) is False


def test_hero_compat_extract_still_works() -> None:
    assert extract_hero_regen_ids("regen: c01, c3") == ["c01", "c03"]
