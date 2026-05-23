"""Safety-слой AI-агента: проверка путей, маскирование секретов.

Любая операция с файлами проходит через `check_path()`. Любой вывод в чат
проходит через `redact_secrets()`. Любой input (edit_file.new,
write_file.content) проходит через `scan_for_secrets()`.

См. правила в .cursor/rules/50-ai-agent.mdc.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Literal

# Запрет read И write: никогда не трогать.
_DENY_READ_AND_WRITE = (
    ".env",
    ".env.local",
    ".env.production",
    ".env.staging",
    "data/state.db",
    "data/state.db-shm",
    "data/state.db-wal",
    ".git/**",
    ".venv/**",
    "venv/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "**/*.pem",
    "**/*.key",
    "**/credentials*",
    "**/secrets*",
)

# Запрет только write (read ok) — это артефакты и legacy.
_DENY_WRITE_ALLOW_READ = (
    "data/videos/**",
    "data/test_prompts/**",
    "tests/snapshots/**",
    "assets/visual_lab/reference_examples/**",
    "browser_profile/**",
    "legacy/**",
)

# Паттерны секретов в коде (для scan/redact).
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aitunnel_key", re.compile(r"sk-aitunnel-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{30,}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{30,}")),
    ("bearer_token", re.compile(r"Bearer [A-Za-z0-9+/=._\-]{30,}")),
    (
        "socks5_creds",
        re.compile(r"socks5://[A-Za-z0-9_\-]+:[A-Za-z0-9_\-]+@[A-Za-z0-9.\-]+:\d+"),
    ),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    (
        "telegram_bot_token",
        re.compile(r"\b\d{8,12}:[A-Za-z0-9_\-]{30,40}\b"),
    ),
)


class SafetyError(RuntimeError):
    """Поднимается при попытке нарушить safety-правила."""


def _match_any(rel_path: str, patterns: tuple[str, ...]) -> bool:
    """Возвращает True, если путь матчит ХОТЯ БЫ один glob-паттерн.

    Поддерживает 3 вида паттернов:
        - `foo/**`  → путь начинается с `foo/` (recursive directory)
        - `**/foo*` → basename матчится по `foo*` (any subdir)
        - `foo`     → точное совпадение или PurePath.match
    """
    rel = PurePosixPath(rel_path)
    basename = rel.name
    rel_str = rel.as_posix()

    parts = set(rel.parts)
    for pat in patterns:
        # 0. `**/foo/**` → любая директория с именем `foo` где-то в пути
        if pat.startswith("**/") and pat.endswith("/**"):
            segment = pat[3:-3]
            if segment and segment in parts:
                return True
            continue

        # 1. `foo/**` → prefix match
        if pat.endswith("/**"):
            prefix = pat[:-3]  # без `/**`
            if rel_str == prefix or rel_str.startswith(prefix + "/"):
                return True
            continue

        # 2. `**/glob` → glob по basename
        if pat.startswith("**/"):
            bare = pat[3:]
            try:
                if PurePosixPath(basename).match(bare):
                    return True
            except (ValueError, NotImplementedError):
                pass
            continue

        # 3. обычный glob — стандартный PurePath.match
        try:
            if rel.match(pat):
                return True
        except (ValueError, NotImplementedError):
            pass

        # 4. exact match
        if rel_str == pat:
            return True

    return False


def check_path(
    path: str | Path,
    op: Literal["read", "write"],
    *,
    repo_root: Path,
) -> Path:
    """Проверить, что путь разрешён для операции `op`.

    Поднимает SafetyError при нарушении. Возвращает абсолютный Path.

    Логика:
        1. Путь должен резолвиться внутри repo_root.
        2. Не должен матчить _DENY_READ_AND_WRITE.
        3. Если op="write" — не должен матчить _DENY_WRITE_ALLOW_READ.
    """
    repo_root = repo_root.resolve()
    p = Path(path)
    if not p.is_absolute():
        p = (repo_root / p).resolve()
    else:
        p = p.resolve()

    # 1. Внутри репо
    try:
        rel = p.relative_to(repo_root)
    except ValueError as e:
        raise SafetyError(
            f"path outside repo: {p} (repo_root={repo_root})"
        ) from e

    rel_str = rel.as_posix()

    # 2. Полный запрет (read + write)
    if _match_any(rel_str, _DENY_READ_AND_WRITE):
        raise SafetyError(
            f"path forbidden (no read/write allowed): {rel_str}"
        )

    # Спец-кейс: .env.example РАЗРЕШЁН (он шаблон без секретов).
    if rel_str == ".env.example":
        return p

    # 3. Write-only запрет
    if op == "write" and _match_any(rel_str, _DENY_WRITE_ALLOW_READ):
        raise SafetyError(f"write forbidden: {rel_str}")

    return p


def scan_for_secrets(text: str) -> list[tuple[str, str]]:
    """Найти секреты в строке. Возвращает [(pattern_name, sample), ...].

    Используется перед write_file/edit_file.new — если что-то нашли,
    отказываем LLM.
    """
    findings: list[tuple[str, str]] = []
    for name, pat in _SECRET_PATTERNS:
        for m in pat.finditer(text or ""):
            # сэмпл: первые 12 символов + ...
            sample = m.group(0)
            if len(sample) > 16:
                sample = sample[:8] + "...REDACTED..." + sample[-4:]
            findings.append((name, sample))
            if len(findings) >= 5:
                return findings
    return findings


def redact_secrets(text: str) -> str:
    """Заменить найденные секреты на ***REDACTED-<name>***."""
    if not text:
        return text
    out = text
    for name, pat in _SECRET_PATTERNS:
        out = pat.sub(f"***REDACTED-{name}***", out)
    return out
