"""Orchestrator code edits: allowlist paths, apply patches, run pytest."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from loguru import logger

# Relative to repo root, posix style.
ALLOWED_PREFIXES: tuple[str, ...] = (
    "app/",
    "tests/",
    "prompts/",
)

DENIED_PREFIXES: tuple[str, ...] = (
    "data/",
    ".git/",
    ".venv/",
    "web/out/",
    "scripts/legacy/",
    "node_modules/",
)

DENIED_NAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "credentials.json",
        "secrets.json",
    }
)

MAX_FILE_BYTES = 400_000
MAX_EDITS_PER_CALL = 20


class CodeAutofixError(RuntimeError):
    """Ошибка правки кода оркестратором."""


def repo_root() -> Path:
    # app/services/code_autofix.py → parents[2] = repo root
    return Path(__file__).resolve().parents[2]


def normalize_rel(path: str) -> str:
    raw = (path or "").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if raw.startswith("/"):
        raise CodeAutofixError(f"абсолютный путь запрещён: {path!r}")
    if ".." in raw.split("/"):
        raise CodeAutofixError(f"path traversal запрещён: {path!r}")
    return raw


def assert_path_allowed(path: str) -> str:
    rel = normalize_rel(path)
    name = Path(rel).name
    if name in DENIED_NAMES or rel in DENIED_NAMES:
        raise CodeAutofixError(f"файл запрещён: {rel}")
    for d in DENIED_PREFIXES:
        if rel == d.rstrip("/") or rel.startswith(d):
            raise CodeAutofixError(f"путь запрещён: {rel}")
    if not any(rel.startswith(p) for p in ALLOWED_PREFIXES):
        raise CodeAutofixError(
            f"путь вне allowlist: {rel}; разрешены: {list(ALLOWED_PREFIXES)}"
        )
    return rel


def assert_paths_allowed(paths: list[str]) -> list[str]:
    return [assert_path_allowed(p) for p in paths]


def abs_path(rel: str) -> Path:
    rel = assert_path_allowed(rel)
    root = repo_root()
    full = (root / rel).resolve()
    if not str(full).startswith(str(root.resolve())):
        raise CodeAutofixError(f"path escape: {rel}")
    return full


def read_file(
    path: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    """Read allowlisted source for orchestrator context (1-based line range)."""
    rel = assert_path_allowed(path)
    full = abs_path(rel)
    if not full.is_file():
        raise CodeAutofixError(f"read_file: файл не найден: {rel}")
    text = full.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)
    s = 1 if start_line is None else max(1, int(start_line))
    e = total if end_line is None else min(total, int(end_line))
    if e < s:
        raise CodeAutofixError(f"read_file: пустой диапазон {s}-{e}")
    chunk = "\n".join(f"{i}|{lines[i - 1]}" for i in range(s, e + 1))
    truncated = False
    if len(chunk) > max_chars:
        chunk = chunk[:max_chars] + "\n…(обрезано)"
        truncated = True
    return {
        "path": rel,
        "start_line": s,
        "end_line": e,
        "total_lines": total,
        "truncated": truncated,
        "content": chunk,
    }


def apply_edits(edits: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply search-replace or full-content writes under allowlist.

    Each edit:
      {"path": "app/...", "old_string": "...", "new_string": "..."}
      {"path": "tests/...", "content": "..."}  # create/overwrite small file
    """
    if not isinstance(edits, list) or not edits:
        raise CodeAutofixError("edit_files: нужен непустой список правок")
    if len(edits) > MAX_EDITS_PER_CALL:
        raise CodeAutofixError(f"edit_files: максимум {MAX_EDITS_PER_CALL} правок")

    changed: list[str] = []
    for i, raw in enumerate(edits):
        if not isinstance(raw, dict):
            raise CodeAutofixError(f"edit_files[{i}]: ожидается объект")
        rel = assert_path_allowed(str(raw.get("path") or ""))
        full = abs_path(rel)
        old_s = raw.get("old_string")
        new_s = raw.get("new_string")
        content = raw.get("content")

        if old_s is not None or new_s is not None:
            if not isinstance(old_s, str) or not isinstance(new_s, str):
                raise CodeAutofixError(
                    f"edit_files[{i}]: old_string/new_string должны быть строками"
                )
            if not old_s:
                raise CodeAutofixError(f"edit_files[{i}]: пустой old_string")
            if not full.is_file():
                raise CodeAutofixError(f"edit_files[{i}]: файл не найден: {rel}")
            text = full.read_text(encoding="utf-8")
            count = text.count(old_s)
            if count == 0:
                raise CodeAutofixError(
                    f"edit_files[{i}]: old_string не найден в {rel}"
                )
            if count > 1:
                raise CodeAutofixError(
                    f"edit_files[{i}]: old_string встречается {count} раз в {rel} — уточни"
                )
            new_text = text.replace(old_s, new_s, 1)
            if len(new_text.encode("utf-8")) > MAX_FILE_BYTES:
                raise CodeAutofixError(f"edit_files[{i}]: файл слишком большой после правки")
            full.write_text(new_text, encoding="utf-8", newline="\n")
            changed.append(rel)
            logger.info("code_autofix: patched {}", rel)
            continue

        if content is not None:
            if not isinstance(content, str):
                raise CodeAutofixError(f"edit_files[{i}]: content должен быть строкой")
            if len(content.encode("utf-8")) > MAX_FILE_BYTES:
                raise CodeAutofixError(f"edit_files[{i}]: content слишком большой")
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8", newline="\n")
            changed.append(rel)
            logger.info("code_autofix: wrote {}", rel)
            continue

        raise CodeAutofixError(
            f"edit_files[{i}]: нужен old_string/new_string или content"
        )

    return {"changed": changed, "count": len(changed)}


def run_tests(test_paths: list[str] | None = None, *, timeout: float = 300.0) -> dict[str, Any]:
    """Run pytest on allowlisted test paths (default: smoke autofix tests)."""
    paths = test_paths or ["tests/test_code_autofix_allowlist.py"]
    rels = assert_paths_allowed([str(p) for p in paths])
    for rel in rels:
        if not rel.startswith("tests/"):
            raise CodeAutofixError(f"run_tests: только tests/*, не {rel}")
        if not abs_path(rel).is_file() and not abs_path(rel).is_dir():
            raise CodeAutofixError(f"run_tests: нет файла {rel}")

    cmd = [sys.executable, "-m", "pytest", "-q", *rels]
    logger.info("code_autofix: {}", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=str(repo_root()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "returncode": proc.returncode,
        "tests": rels,
        "output": out[-2000:],
    }
