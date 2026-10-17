#!/usr/bin/env python3
"""Генерация web/src/lib/orchestratorTypes.ts из app/orchestrator/contracts.py.

Источник — ``export_contract_schemas()`` (WS3). Файл контракта в эту ветку
не копируется: если его нет в дереве, скрипт читает

    git show origin/cursor/ws3-agents-core-r1-3c7d:app/orchestrator/contracts.py

во временный файл и импортирует оттуда.

Запуск из корня репо:
  python web/scripts/gen_orch_types.py
  python web/scripts/gen_orch_types.py --stdout
  python web/scripts/gen_orch_types.py --self-test
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LOCAL_CONTRACTS = ROOT / "app" / "orchestrator" / "contracts.py"
OUT = ROOT / "web" / "src" / "lib" / "orchestratorTypes.ts"
CONTRACT_REF = "origin/cursor/ws3-agents-core-r1-3c7d"
CONTRACT_GIT_PATH = "app/orchestrator/contracts.py"

# Имена констант, которые уже импортирует UI.
CONST_NAMES = {
    "EventType": "ORCH_EVENT_TYPES",
    "TaskStatus": "TASK_STATUSES",
    "MessageMode": "MESSAGE_MODES",
    "AgentRole": "AGENT_ROLES",
    "NodeSource": "NODE_SOURCES",
}

SQL_TS = {
    "INTEGER": "number",
    "REAL": "number",
    "TEXT": "string",
}

# Финальное решение куратора: к модели NodeDef из contracts.py
# добавляются только эти три поля. overridden_in_db и active_version не эмитим.
NODEDEF_EXTRA: tuple[tuple[str, str, str], ...] = (
    (
        "db_version",
        "number | null",
        "Финальное решение куратора. В contracts.NodeDef нет.",
    ),
    (
        "git_sha256",
        "string",
        "Финальное решение куратора. В contracts.NodeDef нет.",
    ),
    (
        "stale_base",
        "boolean",
        "Финальное решение куратора. В contracts.NodeDef нет.",
    ),
)

UI_TAIL = """
/**
 * Полезные нагрузки событий и REST, которых нет отдельными моделями в contracts.py.
 * Шина кладёт свободный `payload`. Approve/kill в роутере WS3 (PR #346) нет —
 * это временный контракт мока, пока ручки не появятся у WS2.
 */

export interface AckPayload {
  /** WS3 queue.accept: текст принятой реплики. */
  text?: string;
  accepted?: boolean;
  /** Старый мок. Если нет, UI берёт echo из text при accepted. */
  echo?: string;
  message_id?: string;
}

export interface TokenPayload {
  text: string;
}

export interface AgentStatusPayload {
  agent: AgentRole;
  status: string;
  detail?: string;
  task_status?: TaskStatus;
  title?: string;
}

export interface StepPayload {
  step_id: string;
  title: string;
  action?: string;
  paid?: boolean;
  ok?: boolean;
  note?: string;
  node?: NodeDef;
}

export interface ApprovalPayload {
  approval_id: string;
  title: string;
  reason: string;
  risk?: string;
  action: string;
  cost_estimate?: number;
}

export interface CheckpointPayload {
  checkpoint_id: string;
  step_id?: string;
  summary: string;
  spend?: number;
}

export interface ValidatorResultPayload {
  name: string;
  pass: boolean;
  details: string;
  blocking?: boolean;
}

export interface TaskDonePayload {
  summary?: string;
}

export interface TaskFailedPayload {
  error?: string;
  summary?: string;
}

export interface ClassificationPayload {
  mode: MessageMode;
  tier?: string;
  deterministic?: boolean;
  confidence?: number;
  detail?: string;
}

/** POST /api/orchestrator/chat/{session}/message — MessageIn в WS3. */
export interface OrchOutboundMessage {
  text: string;
}

/** Ответ post_message: ack уходит в шину, HTTP его не ждёт. */
export interface OrchMessageAckHttp {
  ok: boolean;
  session: string;
  ack_seq: number;
}

export interface OrchApprovalDecision {
  task_id?: string | null;
  approval_id: string;
  reason?: string;
}

export interface OrchKillRequest {
  session_id: string;
  reason?: string;
}

export interface OrchKillResult {
  ok: boolean;
  status?: TaskStatus | string;
  /** Честная семантика: платный шаг останавливается на границе, не mid-flight. */
  detail: string;
}
"""


def _const_name(enum_name: str) -> str:
    if enum_name in CONST_NAMES:
        return CONST_NAMES[enum_name]
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", enum_name)
    split = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", split)
    return split.upper()


def _pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part)


def _dedupe(parts: list[str]) -> str:
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return " | ".join(seen) if seen else "unknown"


def schema_to_ts(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "anyOf" in schema:
        return _dedupe([schema_to_ts(item) for item in schema["anyOf"]])
    if "enum" in schema:
        return _dedupe([json.dumps(item, ensure_ascii=False) for item in schema["enum"]])
    kind = schema.get("type")
    if kind == "string":
        return "string"
    if kind in {"integer", "number"}:
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    if kind == "array":
        return f"{schema_to_ts(schema.get('items') or {})}[]"
    if kind == "object":
        return "Record<string, unknown>"
    return "unknown"


def _comment(text: str) -> list[str]:
    clean = " ".join(text.split())
    if not clean:
        return []
    return ["/**", f" * {clean}", " */"]


def _render_enums(enums: dict[str, list[str]]) -> list[str]:
    lines: list[str] = []
    for name, values in enums.items():
        const = _const_name(name)
        joined = ", ".join(json.dumps(value, ensure_ascii=False) for value in values)
        lines.append(f"export const {const} = [{joined}] as const;")
        lines.append(f"export type {name} = (typeof {const})[number];")
        if name == "EventType":
            lines.append("export type OrchEventType = EventType;")
        lines.append("")
    return lines


def _render_models(models: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for name, schema in models.items():
        desc = str(schema.get("description") or "").strip()
        lines.extend(_comment(desc))
        lines.append(f"export interface {name} {{")
        required = set(schema.get("required") or [])
        props: dict[str, Any] = schema.get("properties") or {}
        for prop, prop_schema in props.items():
            optional = "" if prop in required else "?"
            lines.append(f"  {prop}{optional}: {schema_to_ts(prop_schema)};")
        if name == "NodeDef":
            for field, ts_type, note in NODEDEF_EXTRA:
                lines.append(f"  /** {note} */")
                lines.append(f"  {field}?: {ts_type};")
        lines.append("}")
        lines.append("")
    return lines


def _render_tables(tables: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = ["/** Строки таблиц из TABLE_SPECS. */", ""]
    for table in tables:
        notes = str(table.get("notes") or "").strip().split("\n")[0]
        lines.extend(_comment(notes))
        lines.append(f"export interface {_pascal(table['name'])}Row {{")
        for column in table.get("columns") or []:
            base = SQL_TS.get(str(column.get("sql_type")), "unknown")
            if column.get("nullable", True):
                base = f"{base} | null"
            note = str(column.get("note") or column.get("check") or "").strip()
            if note:
                lines.append(f"  /** {note} */")
            lines.append(f"  {column['name']}: {base};")
        lines.append("}")
        lines.append("")
    return lines


def _render_fsm(fsm: dict[str, list[str]]) -> list[str]:
    lines = ["export const FSM_TRANSITIONS: Record<TaskStatus, readonly TaskStatus[]> = {"]
    for src, dsts in fsm.items():
        rendered = ", ".join(json.dumps(item, ensure_ascii=False) for item in dsts)
        lines.append(f"  {json.dumps(src, ensure_ascii=False)}: [{rendered}],")
    lines.append("};")
    lines.append("")
    return lines


def render_schema(schema: dict[str, Any], source_label: str) -> str:
    version = str(schema.get("version") or "")
    lines = [
        "/**",
        " * @generated by web/scripts/gen_orch_types.py",
        " * Не править руками.",
        f" * source: {source_label}",
        f" * contract: {version}",
        " *",
        " * Расхождение NodeDef с contracts.py (WS3 r1, PR #346):",
        " * куратор добавил db_version, git_sha256, stale_base.",
        " * overridden_in_db и active_version в тип ноды не входят.",
        " */",
        "",
        f"export const ORCH_CONTRACT_VERSION = {json.dumps(version, ensure_ascii=False)};",
        "",
    ]
    lines.extend(_render_enums(schema.get("enums") or {}))
    lines.extend(_render_models(schema.get("models") or {}))
    lines.extend(_render_tables(schema.get("tables") or []))
    lines.extend(_render_fsm(schema.get("fsm") or {}))
    lines.append(UI_TAIL.strip())
    lines.append("")
    return "\n".join(lines)


def _load_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("_orch_contracts_gen", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"не удалось загрузить {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_schema() -> tuple[dict[str, Any], str]:
    """Вернуть (schema, подпись источника). Файл в репозиторий не пишется."""
    if LOCAL_CONTRACTS.is_file():
        module = _load_module(LOCAL_CONTRACTS)
        label = "app/orchestrator/contracts.py"
        return module.export_contract_schemas(), label

    raw = subprocess.check_output(
        ["git", "show", f"{CONTRACT_REF}:{CONTRACT_GIT_PATH}"],
        cwd=ROOT,
        text=True,
    )
    with tempfile.NamedTemporaryFile("w", suffix="_contracts.py", delete=False, encoding="utf-8") as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    try:
        module = _load_module(tmp_path)
        schema = module.export_contract_schemas()
    finally:
        tmp_path.unlink(missing_ok=True)
    label = f"git show {CONTRACT_REF}:{CONTRACT_GIT_PATH}"
    return schema, label


def _self_test() -> int:
    sample = {
        "version": "test",
        "enums": {"TaskStatus": ["queued", "done"], "EventType": ["ack", "token"]},
        "models": {
            "NodeDef": {
                "properties": {
                    "node_key": {"type": "string"},
                    "source": {"$ref": "#/$defs/NodeSource"},
                    "version": {"type": "integer"},
                },
                "required": ["node_key"],
                "type": "object",
            },
            "OrchEvent": {
                "properties": {
                    "type": {"type": "string"},
                    "task_id": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    "payload": {"type": "object"},
                    "seq": {"type": "integer"},
                    "session_id": {"type": "string"},
                },
                "required": ["type", "seq"],
                "type": "object",
            },
        },
        "tables": [
            {
                "name": "node_defs",
                "notes": "spec",
                "columns": [
                    {"name": "node_key", "sql_type": "TEXT", "nullable": False},
                    {"name": "active_version", "sql_type": "INTEGER", "nullable": True},
                ],
            }
        ],
        "fsm": {"queued": ["done"]},
    }
    text = render_schema(sample, "self-test")
    required = [
        "export const TASK_STATUSES",
        "export type TaskStatus",
        "export const ORCH_EVENT_TYPES",
        "export interface NodeDef",
        "db_version?: number | null;",
        "git_sha256?: string;",
        "stale_base?: boolean;",
        "task_id?: number | null;",
        "session_id?: string;",
        "export interface NodeDefsRow",
        "active_version: number | null;",
        "export interface OrchOutboundMessage",
        "ack_seq: number;",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        print("self-test failed, missing:", ", ".join(missing), file=sys.stderr)
        return 1
    if render_schema(sample, "self-test") != text:
        print("self-test failed: unstable render", file=sys.stderr)
        return 1

    schema, label = load_schema()
    live = render_schema(schema, label)
    live_need = [
        '"message"',
        "export interface NodeDef",
        "prompt_template?: string;",
        "source?: NodeSource;",
        "db_version?: number | null;",
        "git_sha256?: string;",
        "stale_base?: boolean;",
        "export interface OrchEvent",
        "session_id?: string;",
        "export const ORCH_CONTRACT_VERSION = \"ws3-r1\"",
        "git show origin/cursor/ws3-agents-core-r1-3c7d:app/orchestrator/contracts.py",
        "export const LLM_PROVIDER",
    ]
    if LOCAL_CONTRACTS.is_file():
        live_need[-1] = "app/orchestrator/contracts.py"
    missing_live = [item for item in live_need if item not in live]
    if missing_live:
        print("self-test failed on live contracts, missing:", ", ".join(missing_live), file=sys.stderr)
        return 1
    forbidden = [item for item in ("overridden_in_db?:", "active_version?:") if item in live]
    if forbidden:
        print("self-test failed, NodeDef must not include:", ", ".join(forbidden), file=sys.stderr)
        return 1
    if LOCAL_CONTRACTS.is_file():
        print("self-test note: local contracts.py exists, git show path was not required")
    print("gen_orch_types self-test ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Сгенерировать TS-типы оркестратора из contracts.py")
    parser.add_argument("--stdout", action="store_true", help="печатать TS, не записывая файл")
    parser.add_argument("--self-test", action="store_true", help="проверить рендер и живой contracts.py")
    args = parser.parse_args()
    if args.self_test:
        return _self_test()
    schema, label = load_schema()
    rendered = render_schema(schema, label)
    if args.stdout:
        sys.stdout.write(rendered)
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} from {label}")
    if LOCAL_CONTRACTS.exists():
        print("local contracts.py was used")
    else:
        print("contracts.py was not copied into the branch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
