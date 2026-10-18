"""Каждый тип, который ядро публикует, есть в EVENT_TYPES."""

from __future__ import annotations

import ast
from pathlib import Path

from app.orchestrator.events import EVENT_TYPES

_ROOT = Path(__file__).resolve().parents[2] / "app" / "orchestrator"


def _if_arms(node: ast.AST) -> set[str]:
    if isinstance(node, ast.IfExp):
        return _if_arms(node.body) | _if_arms(node.orelse)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    return set()


def _string_constants(node: ast.AST) -> set[str]:
    found: set[str] = set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        found.add(node.value)
    for child in ast.iter_child_nodes(node):
        found |= _string_constants(child)
    return found


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return None


def _published_types() -> set[str]:
    """Строковые типы из publish, включая if-выражения и коды problem."""
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    calls: list[tuple[ast.Call, ast.FunctionDef | ast.AsyncFunctionDef | None]] = []
    for path in _ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[node.name] = node
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) != "publish":
                continue
            if len(node.args) < 2:
                continue
            parent = None
            for fn in functions.values():
                if node in ast.walk(fn):
                    parent = fn
                    break
            calls.append((node, parent))
    found: set[str] = set()
    for call, parent in calls:
        arg = call.args[1]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.add(arg.value)
            continue
        if isinstance(arg, ast.IfExp):
            found |= _if_arms(arg)
            continue
        if isinstance(arg, ast.Name) and parent is not None:
            found |= _name_types(arg.id, parent, functions)
    return found


def _name_types(
    name: str,
    parent: ast.FunctionDef | ast.AsyncFunctionDef,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(parent):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value, str):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            found.add(node.value.value)
        called = _call_name(node.value)
        if called and called in functions:
            found |= {
                item
                for item in _string_constants(functions[called])
                if item.isidentifier() or "_" in item
            }
        if isinstance(node.value, ast.IfExp):
            found |= _string_constants(node.value)
    return {item for item in found if item.replace("_", "").isalnum()}


def test_published_event_types_belong_to_event_types() -> None:
    published = _published_types()
    assert "partial_write" in published
    missing = sorted(published - set(EVENT_TYPES))
    assert missing == []
