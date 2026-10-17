#!/usr/bin/env python3
"""Генерация web/src/lib/orchestratorTypes.ts из app/orchestrator/contracts.py.

До freeze contracts (Д+2) файла может не быть. В этом случае скрипт ничего
не перезаписывает и выходит с кодом 0 — в репозитории остаются ручные типы
с пометкой «до freeze contracts Д+2».

Когда contracts.py появится, скрипт читает Enum (str-значения) и dataclass'ы
и перезаписывает orchestratorTypes.ts. Ожидаемые имена: OrchEventType,
TaskStatus, MessageMode, AgentRole, OrchEvent.

Запуск из корня репо:
  python web/scripts/gen_orch_types.py
  python web/scripts/gen_orch_types.py --self-test
  python web/scripts/gen_orch_types.py --stdout
"""

from __future__ import annotations

import argparse
import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "app" / "orchestrator" / "contracts.py"
OUT = ROOT / "web" / "src" / "lib" / "orchestratorTypes.ts"

_PY_SCALARS = {
    "str": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "dict": "Record<string, unknown>",
    "Any": "unknown",
    "object": "Record<string, unknown>",
}


def _ann_to_ts(node: ast.AST | None) -> str:
    if node is None:
        return "unknown"
    if isinstance(node, ast.Name):
        return _PY_SCALARS.get(node.id, node.id)
    if isinstance(node, ast.Constant):
        if node.value is None:
            return "null"
        if isinstance(node.value, str):
            return f'"{node.value}"'
        return "unknown"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _ann_to_ts(node.left)
        right = _ann_to_ts(node.right)
        if left == "null":
            return f"{right} | null"
        if right == "null":
            return f"{left} | null"
        return f"{left} | {right}"
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        base = node.value.id
        if base in {"list", "List", "Sequence"}:
            return f"{_ann_to_ts(node.slice)}[]"
        if base in {"dict", "Dict", "Mapping"}:
            return "Record<string, unknown>"
        if base == "Optional":
            return f"{_ann_to_ts(node.slice)} | null"
        if base == "Literal":
            elts: list[ast.AST]
            if isinstance(node.slice, ast.Tuple):
                elts = list(node.slice.elts)
            else:
                elts = [node.slice]
            parts = [_ann_to_ts(e) for e in elts]
            return " | ".join(parts) if parts else "unknown"
        return f"{base}<{_ann_to_ts(node.slice)}>"
    if isinstance(node, ast.Attribute):
        return node.attr
    return "unknown"


def _enum_strings(node: ast.ClassDef) -> list[str] | None:
    base_names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            base_names.append(base.id)
        elif isinstance(base, ast.Attribute):
            base_names.append(base.attr)
    if "Enum" not in base_names:
        return None
    values: list[str] = []
    for stmt in node.body:
        target = None
        value = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            target = stmt.target
            value = stmt.value
        if not isinstance(target, ast.Name) or value is None:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            values.append(value.value)
    return values


def _is_dataclass(node: ast.ClassDef) -> bool:
    for dec in node.decorator_list:
        name = ""
        if isinstance(dec, ast.Name):
            name = dec.id
        elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
            name = dec.func.id
        elif isinstance(dec, ast.Attribute):
            name = dec.attr
        if name == "dataclass":
            return True
    return False


def _dataclass_fields(node: ast.ClassDef) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            fields.append((stmt.target.id, _ann_to_ts(stmt.annotation)))
    return fields


def render_ts(source: str) -> str:
    tree = ast.parse(source)
    enums: list[tuple[str, list[str]]] = []
    classes: list[tuple[str, list[tuple[str, str]]]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        values = _enum_strings(node)
        if values is not None:
            enums.append((node.name, values))
            continue
        if _is_dataclass(node):
            classes.append((node.name, _dataclass_fields(node)))

    lines: list[str] = [
        "/**",
        " * @generated from app/orchestrator/contracts.py",
        " * Не править руками — python web/scripts/gen_orch_types.py",
        " */",
        "",
    ]
    for name, values in enums:
        if not values:
            continue
        union = " | ".join(f'"{v}"' for v in values)
        const = ", ".join(f'"{v}"' for v in values)
        lines.append(f"export const {name.upper()}_VALUES = [{const}] as const;")
        lines.append(f"export type {name} = {union};")
        lines.append("")
    for name, fields in classes:
        lines.append(f"export interface {name} {{")
        for fname, ftype in fields:
            lines.append(f"  {fname}: {ftype};")
        lines.append("}")
        lines.append("")
    if not any(name == "OrchEvent" for name, _ in classes):
        lines.extend(
            [
                "export interface OrchEvent<TPayload = Record<string, unknown>> {",
                "  type: string;",
                "  task_id: string | null;",
                "  payload: TPayload;",
                "  seq: number;",
                "}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _self_test() -> int:
    sample = '''
from enum import Enum
from dataclasses import dataclass

class OrchEventType(str, Enum):
    ACK = "ack"
    TOKEN = "token"
    CLASSIFICATION = "classification"

class TaskStatus(str, Enum):
    QUEUED = "queued"
    DONE = "done"

class MessageMode(str, Enum):
    INTERRUPT = "interrupt"
    STEER = "steer"
    APPEND = "append"

@dataclass
class OrchEvent:
    type: str
    task_id: str | None
    payload: dict
    seq: int
'''
    text = render_ts(sample)
    required = [
        '"ack"',
        '"token"',
        '"classification"',
        '"queued"',
        '"interrupt"',
        '"steer"',
        '"append"',
        "export interface OrchEvent",
        "task_id: string | null;",
        "seq: number;",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        print("self-test failed, missing:", ", ".join(missing), file=sys.stderr)
        print(text, file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "contracts.py"
        path.write_text(sample, encoding="utf-8")
        again = render_ts(path.read_text(encoding="utf-8"))
    if again != text:
        print("self-test failed: unstable render", file=sys.stderr)
        return 1
    print("gen_orch_types self-test ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Сгенерировать TS-типы оркестратора из contracts.py")
    parser.add_argument("--stdout", action="store_true", help="печатать TS, не записывая файл")
    parser.add_argument("--self-test", action="store_true", help="прогнать парсер на встроенном образце")
    args = parser.parse_args()
    if args.self_test:
        return _self_test()
    if not CONTRACTS.is_file():
        print(
            f"contracts.py ещё нет ({CONTRACTS}). "
            "Типы остаются ручными: до freeze contracts Д+2. Файл не перезаписан."
        )
        return 0
    source = CONTRACTS.read_text(encoding="utf-8")
    rendered = render_ts(source)
    if args.stdout:
        sys.stdout.write(rendered)
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
