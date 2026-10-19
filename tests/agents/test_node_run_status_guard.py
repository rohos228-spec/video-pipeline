"""Сырой SQL: контур A не кладёт в node_runs.status чужой TaskStatus.

Чтение через ORM на неизвестном значении падает LookupError, поэтому
статусы снимаются ``SELECT DISTINCT``. Список допустимых значений —
текущий ``NodeRunStatus``, не зашитый набор.
"""

from __future__ import annotations

import ast
import enum
import sqlite3
import sys
from pathlib import Path

import pytest

from app.models import NodeRunStatus
from app.orchestrator.contracts import TaskStatus
from app.settings import settings
from tests.agents import test_contour_a_http as contour_http

_ROOT = Path(__file__).resolve().parents[2]
_CORE = (
    _ROOT / "app" / "orchestrator" / "agents",
    _ROOT / "app" / "orchestrator" / "queue.py",
    _ROOT / "app" / "orchestrator" / "checkpoints.py",
    _ROOT / "app" / "orchestrator" / "events.py",
    _ROOT / "app" / "web" / "routers" / "orchestrator_chat.py",
)


def _allowed() -> set[str]:
    return {item.value for item in NodeRunStatus}


def _statuses_fit(statuses: list[str]) -> bool:
    """Сырые статусы входят в текущий enum. Список нигде не зашит."""
    return set(statuses) <= _allowed()


def _foreign_task_statuses() -> set[str]:
    return {item.value for item in TaskStatus} - _allowed()


def _distinct_statuses() -> tuple[list[str], bool]:
    """Строки ``node_runs`` тем же sqlite, что у контура. Нет таблицы — пусто."""
    conn = sqlite3.connect(settings.sqlite_path)
    try:
        try:
            rows = conn.execute("SELECT DISTINCT status FROM node_runs").fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            return [], True
    finally:
        conn.close()
    return [str(row[0]) for row in rows if row[0] is not None], False


def _mentions_task_status(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id == "TaskStatus":
            return True
        if isinstance(child, ast.Attribute) and child.attr == "TaskStatus":
            return True
    return False


def _receiver_is_node_run(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        lowered = node.id.lower()
        return lowered in {"nr", "noderun"} or "node_run" in lowered
    if isinstance(node, ast.Attribute):
        return node.attr in {"node_run", "node_runs"} or _receiver_is_node_run(node.value)
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        return name == "NodeRun"
    if isinstance(node, ast.Subscript):
        return _receiver_is_node_run(node.value)
    return False


def _status_assignment_hits(node: ast.AST, value: ast.AST | None, label: str) -> list[str]:
    if value is None or not _mentions_task_status(value):
        return []
    hits: list[str] = []

    def visit(target: ast.AST) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                visit(elt)
            return
        if isinstance(target, ast.Starred):
            visit(target.value)
            return
        if isinstance(target, ast.Attribute) and target.attr == "status" and _receiver_is_node_run(target.value):
            hits.append(f"{label}:{getattr(target, 'lineno', 0)}")

    if isinstance(node, ast.Assign):
        for target in node.targets:
            visit(target)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        visit(node.target)
    return hits


def _scan_tree(tree: ast.AST, label: str, source: str) -> list[str]:
    foreign = _foreign_task_statuses()
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            value = node.value
            hits.extend(_status_assignment_hits(node, value, label))
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "NodeRun":
                for keyword in node.keywords:
                    if keyword.arg == "status" and _mentions_task_status(keyword.value):
                        hits.append(f"{label}:{node.lineno}")
            if name == "setattr" and len(node.args) >= 3:
                status_name = node.args[1]
                if (
                    isinstance(status_name, ast.Constant)
                    and status_name.value == "status"
                    and _receiver_is_node_run(node.args[0])
                    and _mentions_task_status(node.args[2])
                ):
                    hits.append(f"{label}:{node.lineno}")
            strings = [
                child.value
                for child in ast.walk(node)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            ]
            if any("node_runs" in item.lower() for item in strings) and any(
                _mentions_task_status(arg) for arg in (*node.args, *(kw.value for kw in node.keywords))
            ):
                hits.append(f"{label}:{node.lineno}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            if "node_runs" in low and any(value in low for value in foreign):
                hits.append(f"{label}:{node.lineno}")
    if "NodeRun" in source or "node_runs" in source:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                continue
            value = node.value
            if value is None or not _mentions_task_status(value):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "status":
                    hits.append(f"{label}:{target.lineno}")
    return hits


def _core_hits() -> list[str]:
    files: list[Path] = []
    for entry in _CORE:
        if entry.is_dir():
            files.extend(sorted(entry.rglob("*.py")))
        elif entry.is_file():
            files.append(entry)
    hits: list[str] = []
    for path in files:
        source = path.read_text(encoding="utf-8")
        hits.extend(_scan_tree(ast.parse(source), str(path.relative_to(_ROOT)), source))
    return hits


def _walker_sees_node_run_assignment() -> None:
    """Сам обход отличает NodeRun.status от статуса задачи."""
    bad = _scan_tree(
        ast.parse("node_run.status = TaskStatus.waiting_approval\n"),
        "<synthetic>",
        "node_run.status = TaskStatus.waiting_approval\n",
    )
    assert bad
    good = _scan_tree(
        ast.parse("project.status = TaskStatus.done\n"),
        "<synthetic>",
        "project.status = TaskStatus.done\n",
    )
    assert good == []
    sql_bad = "conn.execute(\"UPDATE node_runs SET status = ?\", (TaskStatus.waiting_approval.value,))\n"
    assert _scan_tree(ast.parse(sql_bad), "<synthetic>", sql_bad)
    sql_ok = "conn.execute(\"UPDATE orch_tasks SET status = ?\", (TaskStatus.waiting_approval.value,))\n"
    assert _scan_tree(ast.parse(sql_ok), "<synthetic>", sql_ok) == []


@pytest.mark.asyncio
async def test_contour_a_node_run_statuses_belong_to_node_run_status(token: str) -> None:
    """После approve контура A статусы node_runs — только NodeRunStatus.

    Контур оркестратора таблицу не создаёт: тогда проверка статическая,
    присваиваний TaskStatus в NodeRun.status в ядре нет.
    """
    await contour_http.test_contour_a_http_reaches_done_and_keeps_inactive_shot(token)
    statuses, table_missing = _distinct_statuses()
    assert _allowed()
    if table_missing or not statuses:
        _walker_sees_node_run_assignment()
        assert _core_hits() == []
        return
    assert _statuses_fit(statuses)


def test_guard_accepts_status_added_to_node_run_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Лишний член enum проходит тот же предикат, что и контур A.

    R0b добавит ``waiting_approval`` в ``NodeRunStatus``. Гард читает enum
    в момент проверки, поэтому новое значение не нужно дописывать в список.
    """
    original = {item.value for item in NodeRunStatus}
    extra = "future_status_r0b"
    assert extra not in original
    members = [(item.name, item.value) for item in NodeRunStatus]
    members.append((extra, extra))
    patched = enum.Enum("NodeRunStatus", members, type=str)
    monkeypatch.setattr(sys.modules[__name__], "NodeRunStatus", patched)
    assert extra in _allowed()
    assert _statuses_fit([*sorted(original), extra])
    assert _statuses_fit(["not-a-node-status"]) is False
