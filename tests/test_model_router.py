"""Тесты на app/ai_agent/model_router.py."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.ai_agent.config import get_config
from app.ai_agent.model_router import (
    ModelChoice,
    pick_model,
    strip_override_prefix,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg():
    return get_config(repo_root=REPO_ROOT)


# ──────────────────────────── strip_override_prefix ─────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("!pro analyze project", ("pro", "analyze project")),
        ("!claude refactor", ("claude", "refactor")),
        ("!mini hello", ("mini", "hello")),
        ("!PRO upper case prefix", ("pro", "upper case prefix")),
        ("normal query", (None, "normal query")),
        ("", (None, "")),
        ("  !pro with leading space", ("pro", "with leading space")),
    ],
)
def test_strip_override_prefix(raw, expected) -> None:
    assert strip_override_prefix(raw) == expected


def test_strip_override_no_match_returns_original() -> None:
    """Если префикса нет — query возвращается как был."""
    result = strip_override_prefix("just regular text")
    assert result == (None, "just regular text")


# ──────────────────────────── pick_model: override ──────────────────────────


def test_pick_pro_override(cfg) -> None:
    choice = pick_model("!pro design new feature", cfg)
    assert isinstance(choice, ModelChoice)
    assert choice.model == cfg.pro_model
    assert choice.cleaned_query == "design new feature"
    assert "override" in choice.reason


def test_pick_claude_override(cfg) -> None:
    choice = pick_model("!claude refactor X", cfg)
    assert choice.model == cfg.code_model
    assert choice.cleaned_query == "refactor X"


def test_pick_mini_override(cfg) -> None:
    choice = pick_model("!mini hello", cfg)
    assert choice.model == cfg.default_model
    assert choice.cleaned_query == "hello"


# ──────────────────────────── pick_model: code keywords ─────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "отрефактори app/services/hitl.py",
        "найди баг в outsee.py",
        "почему падает воркер",
        "почему бот зациклился?",
        "бот висит на шаге картинок",
        "не запускается, что не так",
        "крашится при retry",
        "refactor this function",
        "fix bug in pipeline",
        "debug session loop",
    ],
)
def test_pick_code_for_code_keywords(query, cfg) -> None:
    choice = pick_model(query, cfg)
    assert choice.model == cfg.code_model, f"expected code_model for {query!r}"


# ──────────────────────────── pick_model: pro keywords ──────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "проанализируй весь pipeline",
        "сделай план миграции",
        "что лучше — Redis или sqlite?",
        "сравни подходы A и B",
        "design new feature",
        "deep dive into module",
    ],
)
def test_pick_pro_for_pro_keywords(query, cfg) -> None:
    choice = pick_model(query, cfg)
    assert choice.model == cfg.pro_model, f"expected pro_model for {query!r}"


# ──────────────────────────── pick_model: default ───────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "привет",
        "сколько проектов в БД?",
        "запусти тесты",
        "статус",
        "как дела",
    ],
)
def test_pick_default_for_simple(query, cfg) -> None:
    choice = pick_model(query, cfg)
    assert choice.model == cfg.default_model, f"expected default_model for {query!r}"


def test_pick_pro_for_long_query(cfg) -> None:
    """Запрос > 500 байт → gpt-4o (больше контекст-окно)."""
    long_q = "Расскажи подробно про каждый шаг пайплайна. " * 30
    choice = pick_model(long_q, cfg)
    assert choice.model == cfg.pro_model
    assert "long" in choice.reason


# ──────────────────────────── ModelChoice frozen ────────────────────────────


def test_model_choice_immutable(cfg) -> None:
    choice = pick_model("привет", cfg)
    with pytest.raises(dataclasses.FrozenInstanceError):
        choice.model = "x"  # frozen dataclass
