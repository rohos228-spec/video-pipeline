"""File-system tools для AI-агента.

Read-only (без HITL):
- read_file(path, line_offset=, line_limit=)
- list_dir(path, recursive=, max_entries=)
- search_code(pattern, glob=, max_matches=, case_insensitive=)

Edit (с HITL — реализуются в Phase I.4):
- edit_file(path, old_string, new_string)
- write_file(path, content)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.ai_agent.safety import check_path, redact_secrets

# Avoid circular import
from app.ai_agent.tools._spec import ToolContext, ToolSpec

# Лимиты на размер вывода (чтобы не сжечь токены).
_MAX_FILE_BYTES = 60_000  # ~15-20k токенов
_MAX_LIST_ENTRIES = 200
_MAX_SEARCH_MATCHES = 100
_MAX_LINE_LENGTH = 500  # обрезаем сверхдлинные строки


# ──────────────────────────── read_file ─────────────────────────────────────


async def _run_read_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    path = str(args.get("path", "")).strip()
    if not path:
        return {"ok": False, "error": "path is required"}

    try:
        p = check_path(path, "read", repo_root=ctx.repo_root)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"safety: {e}"}

    if not p.exists():
        return {"ok": False, "error": f"file not found: {path}"}
    if not p.is_file():
        return {"ok": False, "error": f"not a file: {path}"}

    line_offset = int(args.get("line_offset", 0) or 0)
    line_limit = int(args.get("line_limit", 0) or 0)

    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"read error: {e}"}

    lines = text.split("\n")
    total_lines = len(lines)

    if line_offset or line_limit:
        end = (line_offset + line_limit) if line_limit else total_lines
        snippet = lines[line_offset:end]
        content = "\n".join(snippet)
        snippet_start = line_offset + 1
        snippet_end = line_offset + len(snippet)
    else:
        content = text
        snippet_start = 1
        snippet_end = total_lines

    # Hard byte cap.
    truncated = False
    if len(content) > _MAX_FILE_BYTES:
        content = content[:_MAX_FILE_BYTES]
        truncated = True

    # Redact secrets.
    content = redact_secrets(content)

    return {
        "ok": True,
        "path": path,
        "total_lines": total_lines,
        "lines_shown": [snippet_start, snippet_end],
        "truncated_bytes": truncated,
        "size_bytes": p.stat().st_size,
        "content": content,
    }


TOOL_READ_FILE = ToolSpec(
    name="read_file",
    spec={
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Прочитать файл из репозитория. Для больших файлов используй "
                "line_offset+line_limit чтобы читать частями (по 200-500 строк)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Относительный путь от корня репо (например, 'app/telegram/bot.py').",
                    },
                    "line_offset": {
                        "type": "integer",
                        "description": "Начать с этой строки (0-based). Опционально.",
                    },
                    "line_limit": {
                        "type": "integer",
                        "description": "Сколько строк прочитать. 0 = до конца. Опционально.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    run=_run_read_file,
    is_hitl=False,
    description_short="Прочитать файл (с lines slice)",
)


# ──────────────────────────── list_dir ──────────────────────────────────────


async def _run_list_dir(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    path = str(args.get("path", ".")).strip() or "."
    recursive = bool(args.get("recursive", False))
    max_entries = min(int(args.get("max_entries", 50) or 50), _MAX_LIST_ENTRIES)

    try:
        p = check_path(path, "read", repo_root=ctx.repo_root)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"safety: {e}"}

    if not p.exists():
        return {"ok": False, "error": f"not found: {path}"}
    if not p.is_dir():
        return {"ok": False, "error": f"not a directory: {path}"}

    entries: list[dict[str, Any]] = []
    try:
        paths = sorted(p.rglob("*")) if recursive else sorted(p.iterdir())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"list[Any] error: {e}"}

    truncated = False
    for child in paths:
        if len(entries) >= max_entries:
            truncated = True
            break
        # safety: skip if child is forbidden
        try:
            check_path(child.relative_to(ctx.repo_root), "read", repo_root=ctx.repo_root)
        except Exception:  # noqa: BLE001
            continue
        entries.append(
            {
                "name": child.name,
                "path": child.relative_to(ctx.repo_root).as_posix(),
                "is_dir": child.is_dir(),
                "size": child.stat().st_size if child.is_file() else None,
            }
        )

    return {
        "ok": True,
        "path": p.relative_to(ctx.repo_root).as_posix() or ".",
        "entries": entries,
        "truncated": truncated,
        "total": len(entries),
    }


TOOL_LIST_DIR = ToolSpec(
    name="list_dir",
    spec={
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "Список файлов и папок в директории.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Относительный путь (по умолчанию — корень репо).",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Рекурсивно (по умолчанию false).",
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "Лимит записей (по умолчанию 50, макс 200).",
                    },
                },
                "required": [],
            },
        },
    },
    run=_run_list_dir,
    is_hitl=False,
    description_short="Список файлов в папке",
)


# ──────────────────────────── search_code (rg) ──────────────────────────────


async def _run_search_code(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        return {"ok": False, "error": "pattern is required"}
    glob = str(args.get("glob", "")).strip()
    max_matches = min(
        int(args.get("max_matches", 30) or 30), _MAX_SEARCH_MATCHES
    )
    case_insensitive = bool(args.get("case_insensitive", False))

    cmd = [
        "rg",
        "--line-number",
        "--no-heading",
        "--color=never",
        "--max-count=10",
    ]
    if case_insensitive:
        cmd.append("-i")
    if glob:
        cmd.extend(["--glob", glob])
    cmd.extend(["--", pattern, str(ctx.repo_root)])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ctx.repo_root),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=ctx.tool_timeout_sec
            )
        except TimeoutError:
            proc.kill()
            return {"ok": False, "error": "rg timeout"}
    except FileNotFoundError:
        # rg не установлен — fallback на python grep
        return await _python_grep(pattern, glob, max_matches, ctx)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"rg error: {e}"}

    raw = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")

    matches: list[dict[str, Any]] = []
    repo_str = str(ctx.repo_root) + "/"
    truncated = False
    for line in raw.splitlines():
        if len(matches) >= max_matches:
            truncated = True
            break
        # формат: <path>:<lineno>:<content>
        try:
            path_part, lineno_str, content = line.split(":", 2)
        except ValueError:
            continue
        rel = path_part.replace(repo_str, "")
        if len(content) > _MAX_LINE_LENGTH:
            content = content[:_MAX_LINE_LENGTH] + "...[truncated]"
        matches.append({
            "path": rel,
            "line": int(lineno_str),
            "content": redact_secrets(content),
        })

    return {
        "ok": True,
        "pattern": pattern,
        "glob": glob or None,
        "matches": matches,
        "total": len(matches),
        "truncated": truncated,
        "stderr": err[:300] if err else None,
    }


async def _python_grep(
    pattern: str, glob: str, max_matches: int, ctx: ToolContext
) -> dict[str, Any]:
    """Fallback если rg не установлен — медленнее, но работает."""
    import re

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return {"ok": False, "error": f"invalid regex: {e}"}

    files: list[Path] = []
    if glob:
        files = list[Any](ctx.repo_root.glob(glob))
    else:
        for ext in ("*.py", "*.md", "*.yml", "*.yaml", "*.toml"):
            files.extend(ctx.repo_root.rglob(ext))

    matches: list[dict[str, Any]] = []
    truncated = False
    for f in files:
        if len(matches) >= max_matches:
            truncated = True
            break
        if not f.is_file():
            continue
        try:
            check_path(f.relative_to(ctx.repo_root), "read", repo_root=ctx.repo_root)
        except Exception:  # noqa: BLE001
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for i, ln in enumerate(content.split("\n"), 1):
            if regex.search(ln):
                matches.append({
                    "path": f.relative_to(ctx.repo_root).as_posix(),
                    "line": i,
                    "content": redact_secrets(ln[:_MAX_LINE_LENGTH]),
                })
                if len(matches) >= max_matches:
                    truncated = True
                    break

    return {
        "ok": True,
        "pattern": pattern,
        "glob": glob or None,
        "matches": matches,
        "total": len(matches),
        "truncated": truncated,
        "engine": "python-fallback",
    }


TOOL_SEARCH_CODE = ToolSpec(
    name="search_code",
    spec={
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Поиск по регулярному выражению в коде (через ripgrep). "
                "Возвращает path:line:content. Удобно для поиска символов, callback_data, селекторов."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Регулярное выражение (rg синтаксис).",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Glob фильтр (например, '*.py', 'app/telegram/**').",
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": "Максимум совпадений (по умолчанию 30, макс 100).",
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "Без регистра (по умолчанию false).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    run=_run_search_code,
    is_hitl=False,
    description_short="Поиск по коду (rg)",
)


# ──────────────────────────── edit_file (HITL) ───────────────────────────────


async def _run_edit_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """StrReplace-style правка. ВЫПОЛНЯЕТСЯ ТОЛЬКО ПОСЛЕ HITL-АПРУВА.

    Loop.py гарантирует что эта функция не вызовется без owner ✅.
    """
    from app.ai_agent.safety import scan_for_secrets

    path = str(args.get("path", "")).strip()
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")

    if not path:
        return {"ok": False, "error": "path is required"}
    if not isinstance(old_string, str) or not isinstance(new_string, str):
        return {"ok": False, "error": "old_string and new_string must be strings"}
    if not old_string:
        return {"ok": False, "error": "old_string не может быть пустым"}

    # Secret-scan на new_string — не даём LLM записать ключ хардкодом
    secrets_in_new = scan_for_secrets(new_string)
    if secrets_in_new:
        return {
            "ok": False,
            "error": (
                f"secret-scan: в new_string найдены секреты ({len(secrets_in_new)} "
                f"паттернов: {[n for n, _ in secrets_in_new[:3]]}). "
                "Не пиши ключи в код, используй env-переменные."
            ),
        }

    try:
        p = check_path(path, "write", repo_root=ctx.repo_root)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"safety: {e}"}

    if not p.exists():
        return {"ok": False, "error": f"file not found: {path}"}

    try:
        text = p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"read error: {e}"}

    count = text.count(old_string)
    if count == 0:
        return {
            "ok": False,
            "error": f"old_string not found in {path}",
            "hint": "Прочитай файл сначала read_file и скопируй точную подстроку.",
        }
    if count > 1:
        return {
            "ok": False,
            "error": (
                f"old_string найден {count} раз в {path}. Должен быть уникальным. "
                "Расширь old_string контекстом (3-5 строк до и после)."
            ),
        }

    new_text = text.replace(old_string, new_string, 1)
    try:
        p.write_text(new_text, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"write error: {e}"}

    return {
        "ok": True,
        "path": path,
        "bytes_before": len(text),
        "bytes_after": len(new_text),
        "lines_before": text.count("\n") + 1,
        "lines_after": new_text.count("\n") + 1,
    }


TOOL_EDIT_FILE = ToolSpec(
    name="edit_file",
    spec={
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Заменить уникальную подстроку в файле (StrReplace). "
                "old_string должен быть уникальным в файле — расширяй контекстом "
                "(3-5 строк до и после) для уникальности. "
                "ВНИМАНИЕ: эта правка требует подтверждения owner'а через HITL. "
                "Бот покажет diff пользователю с кнопками ✅/🔁/✏️/❌."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Относительный путь к файлу.",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Точная подстрока для замены (уникальная в файле).",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "На что заменить. Может быть пустой строкой.",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    run=_run_edit_file,
    is_hitl=True,
    description_short="StrReplace в файле (HITL)",
)


# ──────────────────────────── write_file (HITL) ──────────────────────────────


async def _run_write_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Создать новый файл или полностью переписать существующий.

    ВЫПОЛНЯЕТСЯ ТОЛЬКО ПОСЛЕ HITL.
    """
    from app.ai_agent.safety import scan_for_secrets

    path = str(args.get("path", "")).strip()
    content = args.get("content", "")
    if not path:
        return {"ok": False, "error": "path is required"}
    if not isinstance(content, str):
        return {"ok": False, "error": "content must be string"}

    secrets = scan_for_secrets(content)
    if secrets:
        return {
            "ok": False,
            "error": (
                f"secret-scan: в content найдены секреты ({[n for n, _ in secrets[:3]]}). "
                "Не пиши ключи в код."
            ),
        }

    try:
        p = check_path(path, "write", repo_root=ctx.repo_root)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"safety: {e}"}

    # Создаём родительские директории если нужно
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"write error: {e}"}

    return {
        "ok": True,
        "path": path,
        "existed_before": existed,
        "bytes_written": len(content.encode("utf-8")),
        "lines": content.count("\n") + 1,
    }


TOOL_WRITE_FILE = ToolSpec(
    name="write_file",
    spec={
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Создать новый файл или ПОЛНОСТЬЮ переписать существующий. "
                "Для частичных правок используй edit_file. "
                "ВНИМАНИЕ: требует HITL-апрува owner'а."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Относительный путь к новому/перезаписываемому файлу.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Полное содержимое файла.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    run=_run_write_file,
    is_hitl=True,
    description_short="Создать/переписать файл (HITL)",
)
