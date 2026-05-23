"""AST-сканер: каждый callback_data в app/ покрыт префиксом из CB Enum.

Регрессионный тест (Phase E.4 step 1 invariant): если кто-то добавит
inline-кнопку с новым callback_data, не зарегистрировав префикс в
`app/telegram/callback_registry.CB`, этот тест упадёт.

Сейчас (initial scan на cursor/full-implementation):
- 49 уникальных prefix'ов в коде (app/telegram + app/services).
- Все покрыты CB (58 префиксов в Enum, включая HITL и NOOP).

ВНИМАНИЕ: тест не блокирует CI до полной миграции bot.py — пока
проверяет только что есть **сюрприз** (новый prefix не в CB).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.telegram.callback_registry import CB

REPO_ROOT = Path(__file__).resolve().parents[1]

# Где сканируем callback_data
_SCAN_PATHS = [
    REPO_ROOT / "app" / "telegram",
    REPO_ROOT / "app" / "services",  # services/hitl.py делает hitl: callbacks
]


def _extract_callback_strings_from_file(py: Path) -> list[tuple[str, int]]:
    """Вернуть [(callback_template, lineno), ...] для всех InlineKeyboardButton."""
    try:
        text = py.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(py))
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
                s = str(v.value)
            elif isinstance(v, ast.JoinedStr):
                parts = []
                for p in v.values:
                    if isinstance(p, ast.Constant):
                        parts.append(str(p.value))
                    else:
                        parts.append("{}")
                s = "".join(parts)
            elif isinstance(v, ast.BinOp) and isinstance(v.op, ast.Add):
                # str + var конкатенация — берём как есть
                try:
                    s = ast.unparse(v)
                except Exception:  # noqa: BLE001
                    continue
            else:
                continue
            out.append((s, node.lineno))
    return out


def _scan_all() -> list[tuple[str, str, int]]:
    """Собрать ВСЕ (callback_template, file, lineno) из app/telegram + app/services."""
    out: list[tuple[str, str, int]] = []
    for root in _SCAN_PATHS:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            for s, line in _extract_callback_strings_from_file(py):
                out.append(
                    (s, str(py.relative_to(REPO_ROOT)), line)
                )
    return out


def _is_covered_by_cb(template: str) -> bool:
    """Покрыт ли префикс одним из значений CB enum?

    Считаем покрытым если:
    - точное совпадение (template == cb.value)
    - template начинается с cb.value + ":"
    - cb.value начинается с template + ":" (более общий префикс из CB)
    """
    # Извлекаем "fixed prefix" из шаблона (до первого {})
    prefix = template.split("{")[0].rstrip(":")
    if not prefix:
        return True  # variable-only callback — нечего сравнивать
    cb_values = {c.value for c in CB}
    for cb in cb_values:
        if prefix == cb or prefix.startswith(cb + ":") or cb.startswith(prefix + ":"):
            return True
    return False


def test_no_unregistered_callback_prefixes() -> None:
    """Каждый callback_data в app/telegram + app/services покрыт CB enum.

    Если этот тест упал — ты добавил новую кнопку с уникальным префиксом,
    не зарегистрировав его в `app/telegram/callback_registry.CB`.

    Fix: добавь константу в CB Enum, пересоздай реестр в `__all__`.
    """
    all_callbacks = _scan_all()
    assert all_callbacks, "scanner должен найти callback'и"

    not_covered: dict[str, list[tuple[str, int]]] = {}
    for template, file, line in all_callbacks:
        if _is_covered_by_cb(template):
            continue
        not_covered.setdefault(template, []).append((file, line))

    if not_covered:
        details = []
        for tmpl, locations in sorted(not_covered.items()):
            details.append(f"  {tmpl!r}:")
            for f, ln in locations[:3]:
                details.append(f"    - {f}:{ln}")
        pytest.fail(
            f"Найдено {len(not_covered)} prefix'ов БЕЗ записи в CB Enum:\n"
            + "\n".join(details)
            + "\n\nДобавь их в app/telegram/callback_registry.CB."
        )


def test_scan_finds_expected_volume() -> None:
    """Sanity: scanner находит ~150 callback'ов (не меньше 100)."""
    all_callbacks = _scan_all()
    assert len(all_callbacks) >= 100, (
        f"scanner нашёл только {len(all_callbacks)} — что-то сломалось?"
    )


def test_cb_includes_hitl_and_noop() -> None:
    """HITL и NOOP добавлены в CB (legacy bot.py использует их)."""
    cb_values = {c.value for c in CB}
    assert "hitl" in cb_values
    assert "noop" in cb_values


def test_extract_handles_constant() -> None:
    """Сканер ловит callback_data=ast.Constant."""
    import tempfile

    code = '''
from aiogram.types import InlineKeyboardButton

btn = InlineKeyboardButton(text="x", callback_data="proj:42:menu")
'''
    with tempfile.NamedTemporaryFile(
        mode="w", dir=REPO_ROOT / "tests", suffix="_tmp.py", delete=False
    ) as tmp:
        tmp.write(code)
        tmp_path = Path(tmp.name)
    try:
        result = _extract_callback_strings_from_file(tmp_path)
        assert ("proj:42:menu", 4) in result
    finally:
        tmp_path.unlink(missing_ok=True)


def test_extract_handles_fstring() -> None:
    """Сканер ловит callback_data=ast.JoinedStr (f-string)."""
    import tempfile

    code = '''
from aiogram.types import InlineKeyboardButton

pid = 7
btn = InlineKeyboardButton(text="x", callback_data=f"proj:{pid}:menu")
'''
    with tempfile.NamedTemporaryFile(
        mode="w", dir=REPO_ROOT / "tests", suffix="_tmp.py", delete=False
    ) as tmp:
        tmp.write(code)
        tmp_path = Path(tmp.name)
    try:
        result = _extract_callback_strings_from_file(tmp_path)
        # f-string template → "proj:{}:menu"
        templates = [s for s, _ in result]
        assert any("proj:" in t and ":menu" in t for t in templates), templates
    finally:
        tmp_path.unlink(missing_ok=True)
