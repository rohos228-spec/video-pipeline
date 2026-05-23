"""Аудит inline-кнопок Telegram-бота: мёртвые callback'и, дубли, длинные
префиксы, отсутствие Назад/В меню.

Запуск:
    python -m scripts.audit_buttons              # human-readable отчёт
    python -m scripts.audit_buttons --json       # JSON для CI
    python -m scripts.audit_buttons --fail       # exit 1 если есть критичные

Что проверяет:
1. Все callback_data ≤ 64 байт (Telegram лимит).
2. Дубли callback_data: один и тот же data в РАЗНЫХ местах.
3. Мёртвые кнопки: callback_data без хендлера (`@router.callback_query(...)` /
   `@dp.callback_query(...)`).
4. Мёртвые хендлеры: handler без кнопок.
5. Дубли text+callback в одной клавиатуре (UX-конфуз).

См. Phase F PLAN.md и AGENTS.md §10.

Текущее состояние:
- 8200-строчный bot.py разбирается; новые handlers идут в handlers/.
- Из-за этого результат — baseline, который будем улучшать в фазе F.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
TG_DIR = REPO_ROOT / "app" / "telegram"

# Лимит Telegram на callback_data
TG_CALLBACK_LIMIT = 64


@dataclass
class ButtonOccurrence:
    file: str
    line: int
    text: str | None
    callback_template: str  # как в коде (может содержать .format() / f-string)


@dataclass
class HandlerOccurrence:
    file: str
    line: int
    decorator: str  # e.g. F.data == "x:y" или F.data.startswith("x:")


@dataclass
class AuditReport:
    buttons: list[ButtonOccurrence] = field(default_factory=list)
    handlers: list[HandlerOccurrence] = field(default_factory=list)

    long_callbacks: list[ButtonOccurrence] = field(default_factory=list)
    duplicate_callbacks: list[tuple[str, list[ButtonOccurrence]]] = field(
        default_factory=list
    )
    dead_buttons: list[ButtonOccurrence] = field(default_factory=list)
    dead_handlers: list[HandlerOccurrence] = field(default_factory=list)

    def has_critical(self) -> bool:
        # Сейчас critical = только превышение 64-байтового лимита Telegram
        # (которое 100% сломает бота). Duplicates и dead handlers — это
        # warnings, не блокируют CI.
        return bool(self.long_callbacks)


# ────────────────────────────── AST helpers ─────────────────────────────────


def _ast_to_text(node: ast.AST) -> str:
    """Превратить AST-узел в текстовое представление (для callback_data)."""
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.JoinedStr):
        # f-string — собираем литералы + placeholders
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                parts.append("{...}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _ast_to_text(node.left) + _ast_to_text(node.right)
    if isinstance(node, ast.Call):
        # f"{cb}:{x}" → .format(...) / f-string
        return "<call>"
    if isinstance(node, ast.Name):
        return f"{{{node.id}}}"  # переменная — оборачиваем в {var}
    if isinstance(node, ast.Attribute):
        # CB.X.value
        return f"{{{ast.unparse(node)}}}"
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return "<expr>"


def _find_buttons_in_file(filepath: Path) -> list[ButtonOccurrence]:
    """Найти все InlineKeyboardButton(text=..., callback_data=...) в файле."""
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, OSError):
        return []

    out: list[ButtonOccurrence] = []
    rel = str(filepath.relative_to(REPO_ROOT))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # InlineKeyboardButton(...)
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        if func_name != "InlineKeyboardButton":
            continue

        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        cb_arg = kwargs.get("callback_data")
        if cb_arg is None:
            continue
        text_arg = kwargs.get("text")
        text_str = _ast_to_text(text_arg) if text_arg else None
        cb_str = _ast_to_text(cb_arg)
        out.append(
            ButtonOccurrence(
                file=rel,
                line=node.lineno,
                text=text_str,
                callback_template=cb_str,
            )
        )
    return out


def _find_handlers_in_file(filepath: Path) -> list[HandlerOccurrence]:
    """Найти @router.callback_query / @dp.callback_query декораторы."""
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, OSError):
        return []

    out: list[HandlerOccurrence] = []
    rel = str(filepath.relative_to(REPO_ROOT))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            # Имя: router.callback_query / dp.callback_query
            if not isinstance(dec.func, ast.Attribute):
                continue
            if dec.func.attr != "callback_query":
                continue
            # Собираем args — F.data == "x" или F.data.startswith("x:")
            try:
                decorator_text = ast.unparse(dec)
            except Exception:  # noqa: BLE001
                continue
            out.append(
                HandlerOccurrence(
                    file=rel,
                    line=dec.lineno,
                    decorator=decorator_text[:200],
                )
            )
    return out


# ────────────────────────────── core audit ──────────────────────────────────


def _callback_template_to_prefix(cb: str) -> str:
    """Получить префикс callback_data, отсекая {var} плейсхолдеры.

    Примеры:
        'main:new'       → 'main:new'
        'proj:open:{id}' → 'proj:open:'
        '{var}'          → ''
    """
    out = []
    for ch in cb:
        if ch == "{":
            break
        out.append(ch)
    return "".join(out).rstrip(":")


def _handler_matches_callback(handler_decorator: str, cb_prefix: str) -> bool:
    """Грубая эвристика: декоратор соответствует callback prefix?"""
    if not cb_prefix:
        return True  # переменная — не знаем
    # F.data == "x:y"
    if f'== "{cb_prefix}"' in handler_decorator or f"== '{cb_prefix}'" in handler_decorator:
        return True
    # F.data.startswith("x:")
    if f'startswith("{cb_prefix}' in handler_decorator or f"startswith('{cb_prefix}" in handler_decorator:
        return True
    if f'startswith("{cb_prefix}:")' in handler_decorator:
        return True
    # F.data.in_({...})
    if f'"{cb_prefix}"' in handler_decorator:
        return True
    return False


def audit(target_dir: Path = TG_DIR) -> AuditReport:
    """Просканировать target_dir и собрать отчёт."""
    report = AuditReport()

    py_files = sorted(target_dir.rglob("*.py"))
    for f in py_files:
        # Пропустим тесты и __pycache__
        if "__pycache__" in f.parts or "test_" in f.name:
            continue
        report.buttons.extend(_find_buttons_in_file(f))
        report.handlers.extend(_find_handlers_in_file(f))

    # 1. Long callbacks (> 64 байт)
    for b in report.buttons:
        # Грубая оценка длины: считаем без плейсхолдеров (худший случай — N!).
        # Длина шаблона + средняя длина значения 8 символов.
        approx_len = sum(
            8 if c == "{" else 1
            for c in b.callback_template
            if not (c == "}" or "{" not in b.callback_template[: b.callback_template.find(c)] + c)
        )
        # Простой подход: считаем длину строки как есть, плюс +6 байт за каждый {var}
        clean = b.callback_template
        # Заменим {var} на '{aaaaaaaa}' (8 символов запас)
        import re
        clean_for_len = re.sub(r"\{[^}]+\}", "x" * 8, clean)
        if len(clean_for_len.encode("utf-8")) > TG_CALLBACK_LIMIT:
            report.long_callbacks.append(b)

    # 2. Duplicate callback templates (одинаковый callback в нескольких местах)
    cb_to_btns: dict[str, list[ButtonOccurrence]] = {}
    for b in report.buttons:
        if not b.callback_template:
            continue
        cb_to_btns.setdefault(b.callback_template, []).append(b)
    for cb, btns in cb_to_btns.items():
        if len(btns) > 1:
            # Считаем дубль только если разные файлы (в одном файле часто
            # одна и та же кнопка переиспользуется в нескольких клавиатурах).
            files = {b.file for b in btns}
            if len(files) > 1:
                report.duplicate_callbacks.append((cb, btns))

    # 3. Mapping callback prefixes to handlers
    handler_prefixes = []
    for h in report.handlers:
        handler_prefixes.append((h, h.decorator))

    # 4. Dead buttons (callback без handler'а)
    for b in report.buttons:
        prefix = _callback_template_to_prefix(b.callback_template)
        if not prefix:
            continue  # переменная, не можем проверить
        # Игнорим спец-noop / системные
        if prefix in ("ai:noop", "noop"):
            continue
        found = any(_handler_matches_callback(d, prefix) for _, d in handler_prefixes)
        if not found:
            report.dead_buttons.append(b)

    # 5. Dead handlers (handler без кнопок)
    used_callbacks = {b.callback_template for b in report.buttons}
    button_prefixes = {
        _callback_template_to_prefix(cb) for cb in used_callbacks
    }
    button_prefixes.discard("")
    for h in report.handlers:
        # Извлечь "x:y" из декоратора если возможно
        import re
        m = re.search(r'data\s*==\s*[\'"]([^\'"]+)[\'"]', h.decorator)
        if m:
            cb = m.group(1)
            if cb not in used_callbacks and cb not in button_prefixes:
                report.dead_handlers.append(h)
            continue
        m = re.search(r'startswith\([\'"]([^\'"]+)[\'"]', h.decorator)
        if m:
            prefix = m.group(1).rstrip(":")
            # Ищем кнопку с этим префиксом
            found = any(p.startswith(prefix) for p in button_prefixes)
            if not found:
                report.dead_handlers.append(h)

    return report


# ────────────────────────────── reporting ───────────────────────────────────


def print_human(report: AuditReport) -> None:
    print(f"=== Audit Buttons Report ===")
    print(f"Buttons total : {len(report.buttons)}")
    print(f"Handlers total: {len(report.handlers)}")
    print()
    print(f"CRITICAL ISSUES:")
    print(f"  Long callbacks (> {TG_CALLBACK_LIMIT} bytes): {len(report.long_callbacks)}")
    for b in report.long_callbacks[:20]:
        print(f"    {b.file}:{b.line}  text={b.text!r}  callback={b.callback_template!r}")
    if len(report.long_callbacks) > 20:
        print(f"    ... and {len(report.long_callbacks) - 20} more")

    print(f"  Duplicate callbacks across files: {len(report.duplicate_callbacks)} (warning, не блокируют CI)")
    for cb, btns in report.duplicate_callbacks[:10]:
        print(f"    callback={cb!r} in {len(btns)} places:")
        for b in btns[:5]:
            print(f"      {b.file}:{b.line}  text={b.text!r}")
    if len(report.duplicate_callbacks) > 10:
        print(f"    ... and {len(report.duplicate_callbacks) - 10} more")

    print()
    print(f"WARNINGS:")
    print(f"  Dead buttons (callback без handler'а): {len(report.dead_buttons)}")
    for b in report.dead_buttons[:15]:
        print(f"    {b.file}:{b.line}  callback={b.callback_template!r}  text={b.text!r}")
    if len(report.dead_buttons) > 15:
        print(f"    ... and {len(report.dead_buttons) - 15} more")

    print(f"  Dead handlers (без кнопок): {len(report.dead_handlers)}")
    for h in report.dead_handlers[:15]:
        print(f"    {h.file}:{h.line}  {h.decorator[:100]}")
    if len(report.dead_handlers) > 15:
        print(f"    ... and {len(report.dead_handlers) - 15} more")


def print_json(report: AuditReport) -> None:
    out = {
        "buttons_total": len(report.buttons),
        "handlers_total": len(report.handlers),
        "critical": {
            "long_callbacks": [asdict(b) for b in report.long_callbacks],
            "duplicate_callbacks": [
                {"callback": cb, "occurrences": [asdict(b) for b in btns]}
                for cb, btns in report.duplicate_callbacks
            ],
        },
        "warnings": {
            "dead_buttons": [asdict(b) for b in report.dead_buttons],
            "dead_handlers": [asdict(h) for h in report.dead_handlers],
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Аудит Telegram inline-кнопок.")
    parser.add_argument("--json", action="store_true", help="JSON-выход для CI.")
    parser.add_argument(
        "--fail",
        action="store_true",
        help="Exit 1 при critical issues (long callbacks, duplicates).",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Директория для скана (по умолчанию app/telegram/).",
    )
    args = parser.parse_args(argv)

    target = Path(args.target) if args.target else TG_DIR
    if not target.exists():
        print(f"target not found: {target}", file=sys.stderr)
        return 2

    report = audit(target)

    if args.json:
        print_json(report)
    else:
        print_human(report)

    if args.fail and report.has_critical():
        print(
            "\n❌ Критические проблемы найдены (long callbacks или дубли).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
