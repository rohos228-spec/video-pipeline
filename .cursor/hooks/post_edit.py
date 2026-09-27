"""postToolUse(Write): мгновенная обратная связь после правки файла.

- .py: ruff E9/F63/F7/F82 (синтаксис, неопределённые имена) + py_compile;
- web/src/**: одноразовое напоминание про scripts/bump_studio_version.py.
Ошибки возвращаются агенту через additional_context; действие не блокируется.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vp_common import lint_errors, log, project_python, project_root, read_input, respond, state_dir, toplevel  # noqa: E402

from file_guard import paths_from  # noqa: E402


def check_python(path: Path) -> str:
    root = toplevel(path) or project_root()
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    return "\n".join(sorted(lint_errors(project_python(root), root, [rel])))


def web_reminder(data: dict) -> str:
    marker = state_dir() / "web_reminded.json"
    conv = str(data.get("conversation_id") or "default")
    try:
        seen = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        seen = []
    if conv in seen:
        return ""
    marker.write_text(json.dumps((seen + [conv])[-50:]), encoding="utf-8")
    return (
        "Ты правишь web/src. Перед завершением задачи: `python scripts/bump_studio_version.py` "
        "(пересоберёт web/out и поднимет версию студии) и закоммить web/out вместе с правкой."
    )


def main() -> None:
    data = read_input()
    notes = []
    for raw in paths_from(data):
        path = Path(raw)
        if not path.is_absolute():
            path = Path(data.get("cwd") or project_root()) / path
        norm = path.as_posix()
        try:
            if path.suffix == ".py" and path.exists():
                problems = check_python(path)
                if problems:
                    notes.append(f"vp-post-edit: в {path.name} ошибки, исправь сразу:\n{problems[:2000]}")
            elif "/web/src/" in norm:
                msg = web_reminder(data)
                if msg:
                    notes.append(msg)
        except Exception as exc:
            log("post_edit", f"error on {raw}: {exc}")
    respond({"additional_context": "\n\n".join(notes)} if notes else {})


if __name__ == "__main__":
    main()
