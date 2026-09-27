"""beforeReadFile + preToolUse(Write|Delete): защита секретов и артефактов.

- чтение/запись .env*, ключей, browser_profile — deny;
- запись в web/out/** — deny (собирается scripts/bump_studio_version.py);
- запись в .git/ — deny;
- удаление внутри data/ — deny (там state.db и медиа проекта).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vp_common import is_secret_path, log, project_root, read_input, respond  # noqa: E402

PATH_KEYS = ("file_path", "path", "target_file", "filePath", "target", "file")


def rel_parts(path: str) -> tuple[str, ...]:
    p = Path(path.replace("\\", "/"))
    root = project_root()
    try:
        p = p.resolve().relative_to(root)
    except (ValueError, OSError):
        pass
    parts = p.parts
    if ".worktrees" in parts:
        i = parts.index(".worktrees")
        parts = parts[i + 2 :]
    return parts


def paths_from(data: dict) -> list[str]:
    tool_input = data.get("tool_input") or {}
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except json.JSONDecodeError:
            tool_input = {}
    found = []
    for src in (data, tool_input):
        if isinstance(src, dict):
            found += [str(src[k]) for k in PATH_KEYS if isinstance(src.get(k), str) and src.get(k)]
    return found


def decide(data: dict) -> tuple[str, str]:
    tool = str(data.get("tool_name") or "")
    is_read = "tool_name" not in data or tool in ("Read", "Grep")
    for path in paths_from(data):
        if is_secret_path(path):
            return "deny", f"доступ к секретному файлу запрещён: {Path(path).name}"
        if is_read:
            continue
        parts = rel_parts(path)
        if parts[:1] == (".git",) or ".git" in parts[:-1]:
            return "deny", "прямая запись в .git запрещена"
        if parts[:2] == ("web", "out"):
            return "deny", "web/out/ не править руками: меняй web/src и запусти python scripts/bump_studio_version.py"
        if tool == "Delete" and parts[:1] == ("data",):
            return "deny", "удаление в data/ запрещено (state.db, медиа проектов)"
    return "allow", ""


def main() -> None:
    data = read_input()
    try:
        permission, reason = decide(data)
    except Exception as exc:
        permission, reason = "deny", f"file_guard error: {exc}"
    out: dict = {"permission": permission}
    if reason:
        log("file_guard", f"{permission}: {reason}")
        out["user_message"] = f"vp-guard: {reason}"
        if "tool_name" in data:
            out["agent_message"] = f"vp-guard: {reason}. Политика: .cursor/rules/00-autonomy.mdc"
    respond(out)


if __name__ == "__main__":
    main()
