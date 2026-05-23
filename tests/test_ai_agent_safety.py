"""Тесты безопасности AI-агента: проверки путей и secret-scan.

Запуск:  pytest -q tests/test_ai_agent_safety.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai_agent.safety import (
    SafetyError,
    check_path,
    redact_secrets,
    scan_for_secrets,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ──────────────────────────── check_path ────────────────────────────────────


@pytest.mark.parametrize(
    "rel_path,op",
    [
        ("README.md", "read"),
        ("README.md", "write"),
        ("app/telegram/bot.py", "read"),
        ("app/telegram/bot.py", "write"),
        ("app/ai_agent/__init__.py", "write"),
        (".env.example", "read"),
        (".env.example", "write"),
        ("data/videos/anything.mp4", "read"),  # read ok
        ("legacy/old.py", "read"),  # read ok
        ("tests/snapshots/x.html", "read"),
    ],
)
def test_check_path_allowed(rel_path: str, op: str) -> None:
    """Разрешённые операции не должны бросать."""
    result = check_path(rel_path, op, repo_root=REPO_ROOT)
    assert result.is_absolute()
    assert str(result).startswith(str(REPO_ROOT))


@pytest.mark.parametrize(
    "rel_path,op",
    [
        # Полный запрет: .env*
        (".env", "read"),
        (".env", "write"),
        (".env.local", "read"),
        (".env.production", "write"),
        # SQLite БД
        ("data/state.db", "read"),
        ("data/state.db-wal", "write"),
        # Git internals
        (".git/config", "read"),
        (".git/HEAD", "write"),
        # venv
        (".venv/bin/python", "read"),
        # __pycache__
        ("app/__pycache__/foo.cpython-312.pyc", "write"),
        # креды
        ("credentials.json", "read"),
        ("secrets.yaml", "read"),
        ("app.pem", "read"),
        ("app.key", "write"),
    ],
)
def test_check_path_forbidden_completely(rel_path: str, op: str) -> None:
    """Запрещённые пути должны бросать SafetyError для любой операции."""
    with pytest.raises(SafetyError):
        check_path(rel_path, op, repo_root=REPO_ROOT)


@pytest.mark.parametrize(
    "rel_path",
    [
        "data/videos/some.mp4",
        "data/videos/proj/scenes/img.png",
        "data/test_prompts/x/iter_001/image.jpg",
        "tests/snapshots/outsee_dom.html",
        "assets/visual_lab/reference_examples/ref_1_cat_eating.png",
        "legacy/old_module.py",
        "browser_profile/cache/x",
    ],
)
def test_check_path_write_forbidden_but_read_ok(rel_path: str) -> None:
    """Артефактные пути: read разрешён, write — запрещён."""
    # read — ок
    check_path(rel_path, "read", repo_root=REPO_ROOT)
    # write — нет
    with pytest.raises(SafetyError):
        check_path(rel_path, "write", repo_root=REPO_ROOT)


@pytest.mark.parametrize(
    "outside_path",
    [
        "../etc/passwd",
        "/etc/passwd",
        "/tmp/anything",
        "../../home/ubuntu/.ssh/id_rsa",
    ],
)
def test_check_path_outside_repo(outside_path: str) -> None:
    """Путь за пределами repo_root должен быть отвергнут."""
    with pytest.raises(SafetyError, match="path outside repo"):
        check_path(outside_path, "read", repo_root=REPO_ROOT)


def test_check_path_returns_absolute() -> None:
    """check_path возвращает абсолютный resolve()'нутый Path."""
    p = check_path("README.md", "read", repo_root=REPO_ROOT)
    assert p == (REPO_ROOT / "README.md").resolve()


# ──────────────────────────── scan_for_secrets ──────────────────────────────


@pytest.mark.parametrize(
    "text,expect_name",
    [
        ("api: sk-aitunnel-cNTc7vWTMaSAAC0B8KCdcEaJ7L1QTcJb", "aitunnel_key"),
        # OpenAI ключи (длина 30+ после префикса)
        ("token=sk-proj-AbCdEf0123456789012345678901234567890123", "openai_key"),
        ("OPENAI_API_KEY=sk-AbCdEf0123456789012345678901234567890123", "openai_key"),
        ("Anthropic: sk-ant-AbCdEf0123456789012345678901234567890123", "anthropic_key"),
        (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789ABCD",
            "bearer_token",
        ),
        (
            "proxy=socks5://user123:pass456@45.130.61.143:8000",
            "socks5_creds",
        ),
        ("github_token=ghp_AbCdEf0123456789012345678901234567890", "github_pat"),
        ("BOT_TOKEN=1234567890:ABC-defGhIJKlMnOpQrStUvWxYz0123456789", "telegram_bot_token"),
    ],
)
def test_scan_for_secrets_finds_known_patterns(
    text: str, expect_name: str
) -> None:
    found = scan_for_secrets(text)
    assert found, f"должен найти {expect_name} в {text!r}"
    names = [name for name, _ in found]
    assert expect_name in names, f"ожидали {expect_name}, нашли {names}"


@pytest.mark.parametrize(
    "clean_text",
    [
        "",
        "обычный код без секретов",
        "url=https://api.openai.com/v1/chat/completions",
        "model=gpt-4o-mini",
        # короткие случайные строки — не должны матчить
        "abc-12345",
        "key=short",
    ],
)
def test_scan_for_secrets_no_false_positives(clean_text: str) -> None:
    """На чистом тексте не должно быть находок."""
    assert scan_for_secrets(clean_text) == []


def test_scan_for_secrets_limit() -> None:
    """Если секретов > 5, возвращаем максимум 5 (не вешаем агента)."""
    text = "\n".join([f"sk-aitunnel-cNTc7vWTMaSAAC0B8KCdcEaJ7L1QTcJ{i:02d}" for i in range(10)])
    found = scan_for_secrets(text)
    assert len(found) <= 5


# ──────────────────────────── redact_secrets ────────────────────────────────


def test_redact_secrets_replaces() -> None:
    text = "key1: sk-aitunnel-cNTc7vWTMaSAAC0B8KCdcEaJ7L1QTcJb and more"
    out = redact_secrets(text)
    assert "sk-aitunnel-cNTc7vWTMaSAAC0B8KCdcEaJ7L1QTcJb" not in out
    assert "REDACTED" in out
    assert "and more" in out  # остальной текст сохранён


def test_redact_secrets_preserves_clean_text() -> None:
    text = "обычный код print('hello')"
    assert redact_secrets(text) == text


def test_redact_secrets_handles_multiple() -> None:
    text = (
        "a=sk-aitunnel-cNTc7vWTMaSAAC0B8KCdcEaJ7L1QTcJb "
        "b=ghp_AbCdEf0123456789012345678901234567890"
    )
    out = redact_secrets(text)
    assert "sk-aitunnel-cNTc" not in out
    assert "ghp_AbCdEf" not in out
    assert out.count("REDACTED") >= 2


def test_redact_secrets_handles_empty() -> None:
    assert redact_secrets("") == ""
    assert redact_secrets(None) is None  # type: ignore[arg-type]
