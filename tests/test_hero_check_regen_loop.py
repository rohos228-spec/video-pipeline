"""Цикл checkMode (после hero) → regen плохих PNG → снова check."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import settings as app_settings
from app.models import Project, ProjectStatus
from app.services.check_analysis import (
    append_hero_regen_hint,
    extract_hero_regen_ids,
    normalize_hero_excel_id,
)
from app.services import hero_check_regen as hcr
from app.services.excel_gpt_node import upload_dir


def _sess():
    class _Sess:
        async def flush(self):
            return None

    return _Sess()


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


def test_normalize_and_extract_regen_line() -> None:
    assert normalize_hero_excel_id("c1") == "c01"
    assert normalize_hero_excel_id("C03.png") == "c03"
    text = """
# ОТЧЁТ ПРОВЕРКИ
verdict: fail
mode: report_only

## summary
Брак у двух героев.

## findings
- [error] c01 лицо каша
- [ok] c02 норм

## actions
переген

regen: c01, c3
"""
    assert extract_hero_regen_ids(text) == ["c01", "c03"]


def test_extract_from_findings_heuristic() -> None:
    text = """
# ОТЧЁТ ПРОВЕРКИ
verdict: fail
mode: report_only

## summary
плохо

## findings
- [error] персонаж c02.png лишние руки
- [warn] c04 стиль не бьётся
- [ok] c01 ок

## actions
нужен переген плохих
"""
    ids = extract_hero_regen_ids(text)
    assert "c02" in ids
    assert "c04" in ids
    assert "c01" not in ids


def test_append_hero_regen_hint_idempotent() -> None:
    base = "проверь картинки"
    once = append_hero_regen_hint(base)
    assert "regen:" in once.lower()
    twice = append_hero_regen_hint(once)
    assert twice.count("regen:") == once.count("regen:")


def test_start_regen_loop_sets_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _project(tmp_path, monkeypatch, "t")
    check_key = "n_check"
    hero_key = "n_hero"
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": hero_key, "type": "hero"},
                {
                    "id": check_key,
                    "type": "excel_gpt",
                    "data": {"slotIndex": 1, "checkMode": True, "checkFix": False},
                },
            ],
            "edges": [
                {"source": hero_key, "target": check_key, "data": {"kind": "after"}}
            ],
        },
        "gpt_operator_results": {
            check_key: {"gateStatus": "fail", "replyPreview": "regen: c01, c02\n"},
        },
        "excel_gpt_nodes": {
            check_key: {"checkMode": True, "checkFix": False, "slotIndex": 1},
        },
    }
    out = upload_dir(p, check_key)
    out.mkdir(parents=True)
    (out / "check_report.txt").write_text(
        "# ОТЧЁТ ПРОВЕРКИ\nverdict: fail\n\nregen: c01, c02\n",
        encoding="utf-8",
    )

    async def _noop_prepare(*_a, **_k):
        return True

    monkeypatch.setattr(
        "app.services.run_sync.prepare_node_for_step_start",
        _noop_prepare,
    )

    started = asyncio.run(
        hcr.maybe_start_hero_check_regen_after_check(_sess(), p, check_key)
    )
    assert started is True
    assert p.status is ProjectStatus.generating_hero
    assert p.meta[hcr.META_IDS] == ["c01", "c02"]
    assert p.meta[hcr.META_RETURN] == check_key
    assert int(p.meta[hcr.META_ROUND]) == 1


def test_start_regen_skips_non_hero_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _project(tmp_path, monkeypatch, "t2")
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_script", "type": "script"},
                {
                    "id": "n_check",
                    "type": "excel_gpt",
                    "data": {"slotIndex": 1, "checkMode": True},
                },
            ],
            "edges": [
                {
                    "source": "n_script",
                    "target": "n_check",
                    "data": {"kind": "after"},
                }
            ],
        },
        "gpt_operator_results": {"n_check": {"gateStatus": "fail"}},
        "excel_gpt_nodes": {"n_check": {"checkMode": True, "slotIndex": 1}},
    }
    started = asyncio.run(
        hcr.maybe_start_hero_check_regen_after_check(_sess(), p, "n_check")
    )
    assert started is False


def test_max_rounds_gives_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    check_key = "n_check"
    p = _project(tmp_path, monkeypatch, "t3")
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_hero", "type": "hero"},
                {
                    "id": check_key,
                    "type": "excel_gpt",
                    "data": {"slotIndex": 2, "checkMode": True},
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
        "gpt_operator_results": {check_key: {"gateStatus": "fail"}},
        "excel_gpt_nodes": {check_key: {"checkMode": True, "slotIndex": 2}},
        hcr.META_ROUND: hcr.MAX_HERO_CHECK_REGEN_ROUNDS,
        hcr.META_RETURN: check_key,
    }
    out = upload_dir(p, check_key)
    out.mkdir(parents=True)
    (out / "check_report.txt").write_text(
        "regen: c01\nverdict: fail\n", encoding="utf-8"
    )

    async def _noop_prepare(*_a, **_k):
        return True

    monkeypatch.setattr(
        "app.services.run_sync.prepare_node_for_step_start",
        _noop_prepare,
    )

    started = asyncio.run(
        hcr.maybe_start_hero_check_regen_after_check(_sess(), p, check_key)
    )
    assert started is False
    assert hcr.META_RETURN not in (p.meta or {})


def test_return_to_check_after_hero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    check_key = "n_check"
    p = _project(tmp_path, monkeypatch, "t4")
    p.status = ProjectStatus.hero_ready
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_hero", "type": "hero"},
                {
                    "id": check_key,
                    "type": "excel_gpt",
                    "data": {"slotIndex": 2, "checkMode": True},
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
        "excel_gpt_nodes": {check_key: {"checkMode": True, "slotIndex": 2}},
        "excel_gpt_completed_keys": [check_key],
        "enrich_completed_slots": [2],
        hcr.META_RETURN: check_key,
        hcr.META_IDS: ["c01"],
        hcr.META_ROUND: 1,
    }

    async def _noop_prepare(*_a, **_k):
        return True

    monkeypatch.setattr(
        "app.services.run_sync.prepare_node_for_step_start",
        _noop_prepare,
    )

    nxt = asyncio.run(
        hcr.maybe_return_to_check_after_hero(_sess(), p)
    )
    assert nxt is ProjectStatus.enriching_2
    assert check_key not in (p.meta.get("excel_gpt_completed_keys") or [])
    assert p.meta.get(hcr.META_RETURN) == check_key
    assert p.meta.get("active_excel_gpt_node_key") == check_key


def test_pass_clears_loop_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _project(tmp_path, monkeypatch, "t5")
    p.meta = {
        hcr.META_IDS: ["c01"],
        hcr.META_ROUND: 2,
        hcr.META_RETURN: "n_check",
    }
    hcr.clear_hero_check_regen_meta(p)
    assert hcr.META_RETURN not in (p.meta or {})
    assert hcr.get_hero_check_regen_ids(p) == []
