"""Генератор docs/CALLBACK_INVENTORY.md из реестра CB Enum + AST-скана.

Полезно для:
- Cursor-агентов которые мигрируют конкретный handler — видят сразу
  где какой callback используется.
- Audit'а: какие prefix'ы не использованы (мертвые CB-константы).
- Документации для новых разработчиков.

Использование:
    python -m scripts.cb_inventory                 # печатает на stdout
    python -m scripts.cb_inventory -o docs/CALLBACK_INVENTORY.md

Запускается также в CI как часть валидации (вернёт exit 1 если найдены
mertvye CB-константы).
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path

from app.telegram.callback_registry import CB

REPO_ROOT = Path(__file__).resolve().parents[1]

# Где сканируем
_SCAN_PATHS = [
    REPO_ROOT / "app" / "telegram",
    REPO_ROOT / "app" / "services",
]


def _extract_callbacks(py: Path) -> list[tuple[str, int]]:
    """Извлечь все callback_data (шаблоны) из файла с номерами строк."""
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return []
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        nm = (
            fn.attr
            if isinstance(fn, ast.Attribute)
            else (fn.id if isinstance(fn, ast.Name) else "")
        )
        if nm != "InlineKeyboardButton":
            continue
        for kw in node.keywords:
            if kw.arg != "callback_data":
                continue
            v = kw.value
            if isinstance(v, ast.Constant):
                out.append((str(v.value), v.lineno))
            elif isinstance(v, ast.JoinedStr):
                parts = []
                for p in v.values:
                    if isinstance(p, ast.Constant):
                        parts.append(str(p.value))
                    elif isinstance(p, ast.FormattedValue):
                        try:
                            parts.append("{" + ast.unparse(p.value) + "}")
                        except Exception:  # noqa: BLE001
                            parts.append("{?}")
                out.append(("".join(parts), v.lineno))
    return out


def _find_handler_lines(py: Path) -> list[tuple[str, int]]:
    """Извлечь декораторы @dp.callback_query / @router.callback_query."""
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return []
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            if not isinstance(dec.func, ast.Attribute):
                continue
            if dec.func.attr != "callback_query":
                continue
            try:
                src = ast.unparse(dec)
            except Exception:  # noqa: BLE001
                continue
            out.append((src[:140], dec.lineno))
    return out


def _scan() -> dict:
    """Сканировать весь репо и собрать данные."""
    callbacks_by_prefix: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    handlers_by_prefix: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    all_locations: list[tuple[str, str, int]] = []

    for root in _SCAN_PATHS:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__pycache__" in py.parts or "test_" in py.name:
                continue
            rel = str(py.relative_to(REPO_ROOT))

            for template, line in _extract_callbacks(py):
                fixed_prefix = template.split("{")[0].rstrip(":")
                # Найдём CB-префикс
                best_cb = None
                for cb in CB:
                    if fixed_prefix == cb.value or fixed_prefix.startswith(cb.value + ":"):
                        if best_cb is None or len(cb.value) > len(best_cb.value):
                            best_cb = cb
                key = best_cb.name if best_cb else f"<UNKNOWN:{fixed_prefix}>"
                callbacks_by_prefix[key].append((template, rel, line))
                all_locations.append((template, rel, line))

            for src, line in _find_handler_lines(py):
                # Извлечь callback_data из декоратора
                import re

                for m in re.finditer(
                    r'(?:data\s*==|startswith)\s*\(?\s*["\']([^"\']+)["\']', src
                ):
                    s = m.group(1)
                    fixed = s.split("{")[0].rstrip(":")
                    best_cb = None
                    for cb in CB:
                        if fixed == cb.value or fixed.startswith(cb.value + ":"):
                            if best_cb is None or len(cb.value) > len(best_cb.value):
                                best_cb = cb
                    key = best_cb.name if best_cb else f"<UNKNOWN:{fixed}>"
                    handlers_by_prefix[key].append((src, rel, line))

    return {
        "callbacks_by_prefix": dict(callbacks_by_prefix),
        "handlers_by_prefix": dict(handlers_by_prefix),
        "total_callbacks": len(all_locations),
    }


def render_markdown(data: dict) -> str:
    """Сборка markdown-документа."""
    callbacks = data["callbacks_by_prefix"]
    handlers = data["handlers_by_prefix"]

    lines: list[str] = []
    lines.append("# Callback Inventory")
    lines.append("")
    lines.append("Авто-сгенерировано из `app/telegram/callback_registry.CB` + AST-скана")
    lines.append("`app/telegram/**/*.py` и `app/services/**/*.py`.")
    lines.append("")
    lines.append(
        f"**{len(list(CB))}** CB-префиксов · "
        f"**{data['total_callbacks']}** callback_data в коде."
    )
    lines.append("")
    lines.append("> Для регенерации: `python -m scripts.cb_inventory -o docs/CALLBACK_INVENTORY.md`")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Группа: используемые CB-константы
    used_cbs = sorted(set(callbacks.keys()) | set(handlers.keys()))
    unused_cbs = sorted({c.name for c in CB} - set(used_cbs))

    lines.append("## Используемые CB-константы")
    lines.append("")
    for cb_name in used_cbs:
        if cb_name.startswith("<UNKNOWN"):
            continue
        try:
            cb = CB[cb_name]
        except KeyError:
            continue
        cb_callbacks = callbacks.get(cb_name, [])
        cb_handlers = handlers.get(cb_name, [])
        lines.append(f"### `CB.{cb_name}` = `{cb.value!r}`")
        lines.append("")
        lines.append(
            f"Кнопок: **{len(cb_callbacks)}** · Handler-декораторов: **{len(cb_handlers)}**"
        )
        lines.append("")

        if cb_callbacks:
            # Группируем по файлу
            by_file: dict[str, list[tuple[str, int]]] = defaultdict(list)
            for tmpl, f, ln in cb_callbacks:
                by_file[f].append((tmpl, ln))
            lines.append("**Кнопки:**")
            lines.append("")
            for f in sorted(by_file.keys()):
                items = by_file[f]
                items.sort(key=lambda x: x[1])
                preview = ", ".join(f"L{ln}" for _, ln in items[:5])
                if len(items) > 5:
                    preview += f", +{len(items) - 5} more"
                unique_tmpls = sorted({t for t, _ in items})
                lines.append(f"- `{f}` ({preview}): `" + "`, `".join(unique_tmpls[:3]) + "`")
                if len(unique_tmpls) > 3:
                    lines.append(f"  - … и ещё {len(unique_tmpls) - 3} вариантов")
            lines.append("")

        if cb_handlers:
            lines.append("**Handler'ы:**")
            lines.append("")
            for src, f, ln in cb_handlers[:5]:
                lines.append(f"- `{f}:{ln}` — `{src[:100]}`")
            if len(cb_handlers) > 5:
                lines.append(f"- … и ещё {len(cb_handlers) - 5} handler'ов")
            lines.append("")
        else:
            lines.append("⚠️ **Нет handler'ов!** Кнопка не отреагирует на клик.")
            lines.append("")
        lines.append("")

    if unused_cbs:
        lines.append("---")
        lines.append("")
        lines.append(f"## Неиспользуемые CB-константы ({len(unused_cbs)})")
        lines.append("")
        lines.append(
            "Эти префиксы определены в CB Enum, но не встречаются ни в кнопках, ни в "
            "handler-декораторах. Возможно — кандидаты на удаление, либо префиксы для "
            "будущих фич."
        )
        lines.append("")
        for cb_name in unused_cbs:
            try:
                cb = CB[cb_name]
            except KeyError:
                continue
            lines.append(f"- `CB.{cb_name}` = `{cb.value!r}`")
        lines.append("")

    # Unknown
    unknown_keys = [k for k in callbacks if k.startswith("<UNKNOWN")]
    if unknown_keys:
        lines.append("---")
        lines.append("")
        lines.append(f"## ⚠️ Callback'и БЕЗ регистрации в CB ({len(unknown_keys)})")
        lines.append("")
        lines.append(
            "Эти кнопки используют префиксы которые не зарегистрированы в CB Enum. "
            "Это нарушение инварианта (см. `tests/test_all_callbacks_in_cb_registry.py`)."
        )
        lines.append("")
        for k in unknown_keys:
            items = callbacks[k]
            lines.append(f"- `{k}` ({len(items)} вхождений)")
            for tmpl, f, ln in items[:3]:
                lines.append(f"  - `{f}:{ln}`: `{tmpl}`")
        lines.append("")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate callback inventory MD.")
    parser.add_argument("-o", "--output", default=None, help="Path to output .md file.")
    parser.add_argument(
        "--fail-on-unused",
        action="store_true",
        help="Exit 1 если есть unused CB-константы (для CI).",
    )
    parser.add_argument(
        "--fail-on-unknown",
        action="store_true",
        help="Exit 1 если есть callback'и без записи в CB.",
    )
    args = parser.parse_args(argv)

    data = _scan()
    md = render_markdown(data)
    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"saved to {args.output}", file=sys.stderr)
    else:
        print(md)

    callbacks = data["callbacks_by_prefix"]
    used_cbs = set(callbacks.keys()) | set(data["handlers_by_prefix"].keys())
    unused = sorted({c.name for c in CB} - used_cbs)
    unknown = [k for k in callbacks if k.startswith("<UNKNOWN")]

    if args.fail_on_unknown and unknown:
        print(f"\n❌ Found {len(unknown)} unregistered prefixes", file=sys.stderr)
        return 1
    if args.fail_on_unused and unused:
        print(f"\n⚠️  {len(unused)} unused CB-constants:", file=sys.stderr)
        for u in unused:
            print(f"  - {u}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
