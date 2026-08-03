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
    extract_critical_frame_regen_targets,
    extract_critical_hero_regen_ids,
    extract_db_patch,
    extract_frame_regen_targets,
    extract_hero_regen_ids,
    parse_check_analysis,
    resolve_vision_check_gate,
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
    assert "## scores" in once
    assert "critical" in once
    assert "0.70" in once
    twice = append_vision_check_hint(once)
    assert twice.count("vision_check") == once.count("vision_check")


def test_vision_gate_pass_on_scores_despite_model_fail() -> None:
    text = """
# ОТЧЁТ ПРОВЕРКИ
verdict: fail

## summary
мелочи

## scores
style: 0.85
character: 0.85
format: 0.90
pose: 0.85
clones: 1.0
text: 0.80
angles: 0.75
quality: 0.85
hands: 0.80
overall: 0.82

## issues
- [warning] c01: лёгкий шум
- [minor] c02: чуть плотный кроп
"""
    assert resolve_vision_check_gate(text) == "pass"
    assert parse_check_analysis(text).verdict == "pass"
    assert extract_critical_hero_regen_ids(text) == []


def test_vision_gate_fail_on_critical_even_high_overall() -> None:
    text = """
# ОТЧЁТ ПРОВЕРКИ
verdict: pass

## summary
брак

## scores
style: 0.90
character: 0.90
format: 0.90
pose: 0.90
clones: 0.90
text: 0.90
angles: 0.90
quality: 0.90
hands: 0.90
overall: 0.90

## issues
- [critical] c01: нет вида со спины
- [warning] c02: шум

regen: c01, c02
"""
    assert resolve_vision_check_gate(text) == "fail"
    assert parse_check_analysis(text).verdict == "fail"
    assert extract_critical_hero_regen_ids(text) == ["c01"]


def test_vision_gate_fail_low_overall_no_regen() -> None:
    text = """
# ОТЧЁТ ПРОВЕРКИ
verdict: fail

## scores
style: 0.50
character: 0.50
format: 0.60
pose: 0.55
clones: 1.0
text: 0.55
angles: 0.50
quality: 0.50
hands: 0.50
overall: 0.54

## issues
- [warning] frame_003_x.png: слабая композиция

frames: 3
"""
    assert resolve_vision_check_gate(text) == "fail"
    assert extract_critical_frame_regen_targets(text) == []


def test_extract_extended_score_axes() -> None:
    from app.services.check_analysis import extract_vision_scores

    scores = extract_vision_scores(
        "## scores\nstyle: 0.7\nhands: 0.4\noverall: 0.6\n"
    )
    assert scores["style"] == 0.7
    assert scores["hands"] == 0.4
    assert scores["overall"] == 0.6


def test_error_findings_are_critical_not_legacy_all_frames() -> None:
    """Модель часто пишет [error] в findings без ## scores — не тащить frames: 1..N."""
    from app.services.check_analysis import (
        has_critical_vision_issues,
        looks_like_scored_vision_report,
    )

    text = """
# ОТЧЁТ ПРОВЕРКИ
verdict: fail
mode: fix

## findings
- [error] `frame_005_5d5b52a3.png`: нет c02
- [error] `frame_006_69026b39.png`: лишние двойники
- [error] вход: отсутствует TSV-экспорт листа «Общий план»
- [ok] `frame_001_ec15f244.png`: ок

## regen_frames
frames: 5, 6, 7, 3, 4, 8, 2, 1, 9, 11, 12, 10
"""
    assert looks_like_scored_vision_report(text)
    assert has_critical_vision_issues(text)
    assert resolve_vision_check_gate(text) == "fail"
    got = extract_critical_frame_regen_targets(text)
    nums = {t["number"] for t in got}
    assert nums == {5, 6}
    assert 1 not in nums
    assert 12 not in nums


def test_vision_assemble_forces_report_only_for_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.gpt_operator import assemble_check_master_prompt_with_hero_hint

    p = _project(tmp_path, monkeypatch, "vfix")
    check_key = "n_check"
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_img", "type": "images"},
                {"id": check_key, "type": "excel_gpt", "data": {"checkMode": True}},
            ],
            "edges": [{"source": "n_img", "target": check_key}],
        }
    }
    text = assemble_check_master_prompt_with_hero_hint(
        p,
        check_key,
        [{"ok": True, "nodeKey": "n_img", "text": "agent"}],
        check_fix=True,
    )
    assert "mode: report_only" in text
    assert "ОБЯЗАТЕЛЕН блок --- XLSX_WRITEBACK" not in text
    assert "db_patch" in text
    assert "VISION OVERRIDE" in text


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


def test_extract_ok_vision_tokens() -> None:
    from app.services.check_analysis import extract_ok_vision_tokens

    text = """
## findings
- [ok] `frame_001_ec15f244.png`: один персонаж
- [critical] frame_005_x.png: нет c02
- [ok] c01.png: принято
- [ok] замечаний нет
"""
    toks = extract_ok_vision_tokens(text)
    assert "f1" in toks
    assert "c01" in toks
    assert "f5" not in toks


def test_mark_ok_keeps_passed_across_rounds() -> None:
    p = Project(
        slug="x",
        topic="t",
        status=ProjectStatus.enrich_1_ready,
        meta={"vision_check_passed": ["f2"]},
    )
    vcl.mark_ok_tokens_from_reply(
        p,
        "## findings\n- [ok] frame_001_aaa.png: ок\n- [critical] frame_003_b.png: брак\n",
    )
    assert "f1" in vcl.get_vision_passed(p)
    assert "f2" in vcl.get_vision_passed(p)
    assert "f3" not in vcl.get_vision_passed(p)


def test_recheck_filters_only_regen_targets(tmp_path: Path) -> None:
    p = Project(
        slug="x",
        topic="t",
        status=ProjectStatus.enrich_1_ready,
        meta={
            "vision_check_return_node": "n_check",
            "vision_check_kind": "scenes",
            "scene_check_regen": [{"number": 3, "shot": 1}, {"number": 7, "shot": 2}],
        },
    )
    paths = [
        tmp_path / "frame_001_aaa.png",
        tmp_path / "frame_003_bbb.png",
        tmp_path / "frame_007_s2_ccc.png",
        tmp_path / "frame_009_ddd.png",
    ]
    for path in paths:
        path.write_bytes(b"x")
    got = vcl.filter_image_paths_for_recheck(p, paths)
    names = {x.name for x in got}
    assert names == {"frame_003_bbb.png", "frame_007_s2_ccc.png"}


def test_first_check_keeps_all_images(tmp_path: Path) -> None:
    p = Project(slug="x", topic="t", status=ProjectStatus.enrich_1_ready, meta={})
    paths = [tmp_path / "frame_001_a.png", tmp_path / "frame_002_b.png"]
    for path in paths:
        path.write_bytes(b"x")
    assert vcl.filter_image_paths_for_recheck(p, paths) == paths


def test_hero_compat_extract_still_works() -> None:
    assert extract_hero_regen_ids("regen: c01, c3") == ["c01", "c03"]


def test_scored_warn_only_does_not_start_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _project(tmp_path, monkeypatch, "vw")
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
        """# ОТЧЁТ ПРОВЕРКИ
verdict: fail

## scores
character: 0.55
format: 0.60
text: 0.50
angles: 0.55
overall: 0.55

## issues
- [warning] c01: мелочь

regen: c01
""",
        encoding="utf-8",
    )

    class _Sess:
        async def flush(self):
            return None

    started = asyncio.run(
        vcl.maybe_start_vision_check_loop_after_check(_Sess(), p, check_key)
    )
    assert started is False
    assert "vision_check_return_node" not in (p.meta or {})
