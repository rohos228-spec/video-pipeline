"""Stand-only option ``--lenient-verdict`` of ``scripts/e2e_full_chain_live.py``.

Offline, nothing paid. Product templates in ``prompts/`` must stay strict; the soft copies are written into the run's own work dir.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "scripts" / "e2e_full_chain_live.py"


@pytest.fixture(scope="module")
def live():
    spec = importlib.util.spec_from_file_location("e2e_full_chain_live_lenient_under_test", LIVE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def work(tmp_path):
    shutil.copytree(ROOT / "prompts" / "check_plan", tmp_path / "prompts" / "check_plan")
    shutil.copytree(ROOT / "prompts" / "check_script", tmp_path / "prompts" / "check_script")
    return tmp_path


def test_option_is_off_by_default(live, monkeypatch):
    src = LIVE.read_text(encoding="utf-8")
    assert '"--lenient-verdict", action="store_true"' in src
    assert "if args.lenient_verdict:" in src


def test_soft_copies_written_and_selected(live, work):
    chosen = live.write_lenient_verdicts(work)
    assert chosen == {"plan": "stand_lenient", "script": "stand_lenient"}
    for folder in ("check_plan", "check_script"):
        assert (work / "prompts" / folder / "stand_lenient.md").is_file()


def test_strict_phrases_removed_only_in_copy(live, work):
    live.write_lenient_verdicts(work)
    for folder in ("check_plan", "check_script"):
        strict = (work / "prompts" / folder / "default.md").read_text(encoding="utf-8")
        soft = (work / "prompts" / folder / "stand_lenient.md").read_text(encoding="utf-8")
        assert "Не одобряй автоматически" in strict and "confidence < 0.7" in strict
        assert "Не одобряй автоматически" not in soft
        assert "confidence < 0.7" not in soft and "confidence < 0.5" in soft
        assert "weak" in soft


def test_json_format_and_quote_check_kept(live, work):
    live.write_lenient_verdicts(work)
    plan = (work / "prompts" / "check_plan" / "stand_lenient.md").read_text(encoding="utf-8")
    assert "evidence_quote" in plan and '"decision"' in plan


def test_repo_templates_untouched(live, work):
    live.write_lenient_verdicts(work)
    for folder in ("check_plan", "check_script"):
        assert not (ROOT / "prompts" / folder / "stand_lenient.md").exists()
        assert "Не одобряй автоматически" in (ROOT / "prompts" / folder / "default.md").read_text(encoding="utf-8")


def test_selected_name_resolves_to_soft_file(live, work, monkeypatch):
    from app.services import gpt_verdict_review as g

    live.write_lenient_verdicts(work)
    monkeypatch.setattr(g, "PROMPTS_ROOT", work / "prompts")
    assert g.verdict_template_path("plan", "stand_lenient") == work / "prompts" / "check_plan" / "stand_lenient.md"
    text = g.load_verdict_check_prompt("script", template="stand_lenient")
    assert "Не одобряй автоматически" not in text


def test_script_style_criterion_softened(live, work):
    live.write_lenient_verdicts(work)
    strict = (work / "prompts" / "check_script" / "default.md").read_text(encoding="utf-8")
    soft = (work / "prompts" / "check_script" / "stand_lenient.md").read_text(encoding="utf-8")
    assert "НЕТ постоянных противопоставлений" in strict
    assert "НЕТ постоянных противопоставлений" not in soft and "три и более раза" in soft


def test_script_style_criterion_never_blocks(live, work):
    live.write_lenient_verdicts(work)
    soft = (work / "prompts" / "check_script" / "stand_lenient.md").read_text(encoding="utf-8")
    assert "`fail` не ставь" in soft
    plan = (work / "prompts" / "check_plan" / "stand_lenient.md").read_text(encoding="utf-8")
    assert "`fail` не ставь" not in plan
