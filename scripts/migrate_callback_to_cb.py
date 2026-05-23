"""Auto-rewrite: inline `callback_data="x:y"` → `make_callback(CB.X_Y, ...)`.

Helper-tool для Phase E.4 steps 3-9 (миграция handler'ов из bot.py).
Сканирует Python-файл, находит литералы callback_data и f-string'и,
строит trial-replacement используя CB Enum, печатает unified diff.

По умолчанию DRY-RUN — ничего не меняет. С `--apply` пишет изменения.

Использование:
    python -m scripts.migrate_callback_to_cb app/telegram/menu.py
        → dry-run, печатает diff и список матчей.

    python -m scripts.migrate_callback_to_cb app/telegram/menu.py --apply
        → реально переписывает файл.

    python -m scripts.migrate_callback_to_cb app/telegram/ --recursive
        → весь каталог.

Ограничения:
- НЕ обрабатывает `F.data.startswith(...)` / `F.data == ...` — только
  `callback_data="..."` в `InlineKeyboardButton(...)`.
- Не трогает уже мигрированный код (использующий `make_callback(CB.X)`).
- Не добавляет import'ы — это нужно сделать руками после прогона
  (агент должен видеть TODO-комментарии).
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from app.telegram.callback_registry import CB


@dataclass
class Replacement:
    """Одна найденная inline callback_data → предложенная замена."""

    line: int
    col: int
    original: str  # callback_data="..." как в коде
    template: str  # шаблон без {var} (для матча CB)
    cb_name: str | None  # имя CB-константы, либо None если не нашли
    suggested: str  # предложенный код


# Карта: prefix value → CB member name. Строится один раз.
_PREFIX_TO_CB: dict[str, str] = {c.value: c.name for c in CB}


def _find_cb_for_prefix(prefix: str) -> str | None:
    """Найти ИМЯ CB-константы для данного префикса.

    Логика:
        - exact match: 'ai:approve' → CB.AI_APPROVE.name
        - параметризованный: 'ai:approve:42' → найти самый длинный CB
          который покрывает (cb == prefix или prefix.startswith(cb+':')).
    """
    if not prefix:
        return None
    # exact match быстро
    if prefix in _PREFIX_TO_CB:
        return _PREFIX_TO_CB[prefix]
    # longest match: ищем самый длинный CB-префикс, покрывающий наш
    best: tuple[str, str] | None = None
    for cb_value, cb_name in _PREFIX_TO_CB.items():
        if prefix.startswith(cb_value + ":"):
            if best is None or len(cb_value) > len(best[0]):
                best = (cb_value, cb_name)
    return best[1] if best else None


def _format_replacement(
    callback_template: str, cb_name: str, tail_parts: list[str]
) -> str:
    """Собрать `make_callback(CB.X, ...arg...)` из шаблона.

    tail_parts — части после prefix (могут быть строками или плейсхолдерами).
    """
    if not tail_parts:
        return f"make_callback(CB.{cb_name})"
    # Сериализуем аргументы — fstring placeholders → как переменные (без {})
    args = []
    for p in tail_parts:
        if p.startswith("{") and p.endswith("}"):
            # f-string placeholder — выкидываем фигурные
            args.append(p[1:-1])
        else:
            args.append(repr(p))
    return f"make_callback(CB.{cb_name}, {', '.join(args)})"


def _analyze_string_template(template: str) -> tuple[str, list[str]] | None:
    """Распарсить шаблон 'prefix1:prefix2:{var}:tail' → (prefix, tail_parts).

    `prefix` — максимально длинный CB-prefix покрывающий шаблон.
    `tail_parts` — куски после префикса.
    """
    # Найдём CB-prefix
    fixed_prefix = template.split("{")[0].rstrip(":")
    if not fixed_prefix:
        return None

    cb_name = _find_cb_for_prefix(fixed_prefix)
    if not cb_name:
        return None
    cb_value = next(c.value for c in CB if c.name == cb_name)

    # Tail после cb_value
    if template == cb_value:
        tail = ""
    elif template.startswith(cb_value + ":"):
        tail = template[len(cb_value) + 1:]
    else:
        return None

    # Разбиваем tail по `:` сохраняя порядок
    if not tail:
        return cb_name, []
    parts = tail.split(":")
    return cb_name, parts


def _find_replacements(source: str) -> list[Replacement]:
    """AST-сканер: найти все callback_data в InlineKeyboardButton(...)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    out: list[Replacement] = []
    lines = source.split("\n")

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
            template: str | None = None
            if isinstance(v, ast.Constant):
                template = str(v.value)
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
                    else:
                        parts.append("{?}")
                template = "".join(parts)
            else:
                continue  # пропускаем сложные выражения

            if template is None:
                continue

            # Пропустим если уже мигрирован (использует make_callback)
            line_text = lines[v.lineno - 1] if 0 < v.lineno <= len(lines) else ""
            if "make_callback" in line_text:
                continue

            analysis = _analyze_string_template(template)
            if analysis is None:
                out.append(Replacement(
                    line=v.lineno,
                    col=v.col_offset,
                    original=template,
                    template=template,
                    cb_name=None,
                    suggested=f'# TODO: prefix не найден в CB Enum: {template!r}',
                ))
                continue

            cb_name, tail = analysis
            suggested = _format_replacement(template, cb_name, tail)
            out.append(Replacement(
                line=v.lineno,
                col=v.col_offset,
                original=template,
                template=template,
                cb_name=cb_name,
                suggested=suggested,
            ))

    return out


def _build_rewrite(source: str, repls: list[Replacement]) -> str:
    """Применить замены к source (только литералы strings, не f-strings).

    f-string'и НЕ переписываем автоматически — слишком много edge cases.
    Только Constant string.
    """
    lines = source.split("\n")
    # Идём с конца чтобы не сместить line numbers
    for r in sorted(repls, key=lambda x: x.line, reverse=True):
        if r.cb_name is None:
            continue
        # Только если template не содержит {var} — простое substitute.
        if "{" in r.template:
            continue
        line = lines[r.line - 1]
        # Заменяем "callback_data='x:y'" / "callback_data=\"x:y\""
        # на "callback_data=make_callback(CB.X)"
        # Используем regex с обоих типов кавычек.
        patterns = [
            rf"callback_data\s*=\s*['\"]{re.escape(r.template)}['\"]",
        ]
        replaced = False
        for pat in patterns:
            new_line, n = re.subn(
                pat,
                f"callback_data={r.suggested}",
                line,
                count=1,
            )
            if n > 0:
                lines[r.line - 1] = new_line
                replaced = True
                break
        if not replaced:
            # Может быть это уже изменено или странный формат
            pass
    return "\n".join(lines)


def _process_file(
    path: Path, *, apply: bool = False, verbose: bool = True
) -> int:
    """Обработать один файл. Возвращает кол-во найденных замен."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"  ⚠ {path}: read error: {e}", file=sys.stderr)
        return 0

    repls = _find_replacements(source)
    if not repls:
        if verbose:
            print(f"  {path}: ничего не нашли (либо уже мигрировано)")
        return 0

    found = [r for r in repls if r.cb_name]
    todo = [r for r in repls if r.cb_name is None]
    fstrings = [r for r in found if "{" in r.template]
    constants = [r for r in found if "{" not in r.template]

    print(f"\n  {path}:")
    print(f"    matched in CB: {len(found)} (literals: {len(constants)}, f-strings: {len(fstrings)})")
    print(f"    NOT in CB (TODO): {len(todo)}")
    for r in found[:8]:
        line_preview = source.split("\n")[r.line - 1].strip()[:60]
        print(f"      L{r.line}: {r.template!r} → CB.{r.cb_name}")
        print(f"        was: {line_preview}")
    if len(found) > 8:
        print(f"      ... +{len(found) - 8} more")

    for r in todo[:5]:
        print(f"      L{r.line}: ⚠ {r.template!r} — добавь в CB Enum!")
    if len(todo) > 5:
        print(f"      ... +{len(todo) - 5} more TODOs")

    if apply:
        if not constants:
            print("    (f-string replacements не применяем автоматически — слишком много edge cases)")
            return len(found)
        new_source = _build_rewrite(source, repls)
        if new_source != source:
            path.write_text(new_source, encoding="utf-8")
            print(f"    ✓ rewritten {len(constants)} string literals")
            print("    ⚠ NB: не забудь импорты: from app.telegram.callback_registry import CB")
            print("    ⚠ NB:                    from app.telegram.keyboards import make_callback")

    return len(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate inline callback_data → CB constants.")
    parser.add_argument("target", help="Файл или директория для обработки.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Реально переписать (по умолчанию dry-run).",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Если target — директория, обрабатывать рекурсивно.",
    )
    args = parser.parse_args(argv)

    target = Path(args.target)
    if not target.exists():
        print(f"not found: {target}", file=sys.stderr)
        return 2

    if target.is_file():
        n = _process_file(target, apply=args.apply)
        print(f"\nTotal: 1 file, {n} matches")
        return 0

    # Directory
    if not args.recursive:
        print("target is directory — use --recursive", file=sys.stderr)
        return 2

    total = 0
    files = 0
    for py in target.rglob("*.py"):
        if "__pycache__" in py.parts or "test_" in py.name:
            continue
        n = _process_file(py, apply=args.apply, verbose=False)
        if n:
            files += 1
            total += n

    print(f"\nTotal: {files} files, {total} matches")
    if not args.apply:
        print("(dry-run; use --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
