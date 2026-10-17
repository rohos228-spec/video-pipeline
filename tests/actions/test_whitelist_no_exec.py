"""Приёмка R0b: в реестре Action Layer нет правки кода и исполнения.

План: docs/fable/PLAN.md §2.2.5 и §6. Счётчики и вердикт modify —
машинная шапка docs/fable/actions_audit.md (тест читает её сам).

Пока реестра нет в дереве (модуль не импортируется), проверка реестра
пропускается. Сломанный внутренний импорт — ошибка, не пропуск.
Детектор exec и разбор записи в ФС гоняются и без реестра.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
import sys
import textwrap
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

FORBIDDEN_IDS = frozenset(
    {
        "edit_files",
        "run_tests",
        "exec",
        "shell",
        "write_file",
        "fix_bugs",
    }
)

# Сначала будущий пакет WS2, затем реестр PR #342.
_REGISTRY_MODULES = (
    "app.orchestrator.actions",
    "app.services.action_layer.spec",
)

_PACKAGE_ROOTS = (
    "app/orchestrator/actions",
    "app/services/action_layer",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUDIT_PATH = _REPO_ROOT / "docs" / "fable" / "actions_audit.md"

# CI #342: .github/workflows/tests.yml, python-version "3.12"
# (три job, около строк 37, 69, 130). pyproject: requires-python >=3.11,<3.13.
# zip(..., strict=True) есть с Python 3.10 — оставляем.
_MIN_PY = (3, 10)

_EXEC_BARE = frozenset({"eval", "exec", "compile", "__import__"})
_SUBPROCESS_FUNCS = frozenset(
    {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
    }
)
_ASYNCIO_FUNCS = frozenset(
    {"create_subprocess_exec", "create_subprocess_shell"}
)
_OS_EXACT = frozenset({"system", "popen", "posix_spawn", "posix_spawnp"})
_PTY_FUNCS = frozenset({"spawn"})

# copy* пишут только назначение. move удаляет источник. rmtree запрещён всегда.
_SHUTIL_WRITE = {
    "copy": (None, 1),
    "copy2": (None, 1),
    "copyfile": (None, 1),
    "copytree": (None, 1),
    "move": (0, 1),
}
_OS_RENAME = frozenset({"rename", "replace"})
_DYN = "<dyn>"

_ALLOW_MARKERS = (
    "data/reports",
    "data/node_overrides",
    "{settings.data_dir}/reports",
    "{settings.data_dir}/node_overrides",
)
_RAG_ID = "rag.reindex"
_RAG_SUFFIXES = ("orch_rag.db", "orch_rag.db-wal", "orch_rag.db-shm")
_SNAPSHOT_DIR = "action_layer_snapshots"
# Решение куратора: каталог снимков разрешён только этим трём id.
_SNAPSHOT_ALLOW = frozenset(
    {
        "montage.upload_voice",
        "montage.upload_music",
        "history.rollback",
    }
)

_HEADER_RE = re.compile(
    r"<!--\s*actions-audit-header\s*(.*?)\s*-->",
    re.DOTALL,
)
_MODIFY_RE = re.compile(
    r"^### modify\n(.*?)(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)
_MODIFY_ITEM_RE = re.compile(r"^\d+\.\s+`([^`]+)`", re.MULTILINE)


class WriteHit:
    def __init__(
        self,
        kind: str,
        sketch: str | None,
        function: str,
        *,
        source: str | None,
        allowed: bool,
        snapshot_delete: bool,
    ):
        self.kind = kind
        self.sketch = sketch
        self.function = function
        self.source = source
        self.allowed = allowed
        self.snapshot_delete = snapshot_delete

    def __str__(self) -> str:
        where = self.sketch if self.sketch is not None else "<путь не доказан>"
        extra = f" из {self.source}" if self.source is not None else ""
        mark = "удаление снимка" if self.snapshot_delete else self.kind
        return f"{self.function}: {mark}{extra} → {where}"


def _audit_text() -> str:
    if not _AUDIT_PATH.is_file():
        pytest.fail(f"нет файла аудита {_AUDIT_PATH}")
    try:
        return _AUDIT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        pytest.fail(f"нечитаемый аудит {_AUDIT_PATH}: {exc}")


def load_audit_header(text: str | None = None) -> dict:
    """Поля шапки actions_audit.md. Тест реестра берёт счётчики только отсюда."""
    raw = _audit_text() if text is None else text
    match = _HEADER_RE.search(raw)
    if match is None:
        pytest.fail("в actions_audit.md нет шапки actions-audit-header")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    required = (
        "source",
        "sha",
        "date",
        "actions",
        "keep",
        "modify",
        "remove",
        "fs_breach",
        "snapshot_allow",
    )
    missing = [key for key in required if key not in fields]
    if missing:
        pytest.fail("в шапке аудита нет полей: " + ", ".join(missing))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fields["date"]):
        pytest.fail(f"дата шапки не ISO: {fields['date']!r}")
    counts = {}
    for key in ("actions", "keep", "modify", "remove"):
        try:
            counts[key] = int(fields[key])
        except ValueError:
            pytest.fail(f"счётчик {key} не число: {fields[key]!r}")
    def _id_list(raw: str) -> tuple[str, ...]:
        return tuple(part.strip() for part in raw.split(",") if part.strip())

    return {
        "source": fields["source"],
        "sha": fields["sha"],
        "date": fields["date"],
        "actions": counts["actions"],
        "keep": counts["keep"],
        "modify": counts["modify"],
        "remove": counts["remove"],
        "fs_breach": _id_list(fields["fs_breach"]),
        "snapshot_allow": _id_list(fields["snapshot_allow"]),
    }


def is_forbidden_action_id(action_id: str) -> bool:
    return action_id in FORBIDDEN_IDS or action_id.split(".")[-1] in FORBIDDEN_IDS


def modify_ids_from_audit(text: str | None = None) -> list[str]:
    raw = _audit_text() if text is None else text
    match = _MODIFY_RE.search(raw)
    if match is None:
        pytest.fail("в actions_audit.md нет раздела ### modify")
    return _MODIFY_ITEM_RE.findall(match.group(1))


def _assert_registry_counts(ids: list[str], header: dict) -> None:
    assert ids, "реестр пуст: тест не должен быть зелёным при нуле действий"
    assert len(ids) == header["actions"], (
        f"в реестре {len(ids)} действий, шапка аудита указывает {header['actions']}"
    )
    assert len(set(ids)) == len(ids), "в реестре повторяются id"
    assert header["actions"] == header["keep"] + header["modify"] + header["remove"], (
        "шапка: actions != keep + modify + remove"
    )


def _registry_module_absent(exc: ModuleNotFoundError, module: str) -> bool:
    """True только если не найден сам модуль реестра или его родительский пакет."""
    missing = exc.name or ""
    return bool(missing) and (missing == module or module.startswith(missing + "."))


def _load_registry_module():
    errors: list[str] = []
    for name in _REGISTRY_MODULES:
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if _registry_module_absent(exc, name):
                errors.append(f"{name}: {exc}")
                continue
            pytest.fail(
                f"внутренний импорт реестра сломан ({name}), "
                f"это не отсутствие модуля (exc.name={exc.name!r}): {exc}"
            )
        except Exception as exc:
            pytest.fail(
                f"реестр {name} не импортируется: {type(exc).__name__}: {exc}"
            )
    pytest.skip(
        "Реестр Action Layer в этом дереве отсутствует "
        "(нет app.orchestrator.actions и app.services.action_layer.spec). "
        "Проверенные модули: " + "; ".join(errors)
    )


def _specs(module):
    ensure = getattr(module, "ensure_builtin_actions", None)
    registry = getattr(module, "registry", None)
    if callable(ensure) and registry is not None and hasattr(registry, "list"):
        ensure()
        return list(registry.list())
    listed = getattr(module, "registry", None)
    if callable(listed):
        reg = listed()
        if hasattr(reg, "list"):
            return list(reg.list())
    names = getattr(module, "ACTION_IDS", None)
    if isinstance(names, Iterable) and not isinstance(names, (str, bytes)):
        return list(names)
    pytest.fail(
        f"{module.__name__} импортирован, но не отдаёт реестр "
        "(ensure_builtin_actions+registry.list, registry() или ACTION_IDS)"
    )


def _action_id(spec) -> str:
    if isinstance(spec, str):
        return spec
    for attr in ("id", "name", "action_id"):
        value = getattr(spec, attr, None)
        if isinstance(value, str) and value:
            return value
    raise AssertionError(f"не удалось прочитать id действия: {spec!r}")


def _executor(spec):
    if isinstance(spec, str):
        return None
    for attr in ("executor", "handler", "fn"):
        fn = getattr(spec, attr, None)
        if callable(fn):
            return fn
    return None


def _import_bindings(tree: ast.AST) -> tuple[dict[str, tuple[str, str | None]], set[str]]:
    """Локальное имя → (модуль, исходное имя | None если это сам модуль).

    Второй элемент — модули, импортированные через ``import *``.
    """
    bound: dict[str, tuple[str, str | None]] = {}
    stars: set[str] = set()
    interesting = {"subprocess", "os", "asyncio", "pty"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root not in interesting:
                    continue
                local = alias.asname or root
                bound[local] = (root, None)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            if root not in interesting:
                continue
            for alias in node.names:
                if alias.name == "*":
                    stars.add(root)
                    continue
                local = alias.asname or alias.name
                bound[local] = (root, alias.name)
    return bound, stars


def _os_name_is_exec(name: str) -> bool:
    return name in _OS_EXACT or name.startswith("exec") or name.startswith("spawn")


def _imported_name_is_exec(module: str, name: str) -> bool:
    if module == "subprocess":
        return name in _SUBPROCESS_FUNCS
    if module == "os":
        return _os_name_is_exec(name)
    if module == "asyncio":
        return name in _ASYNCIO_FUNCS
    if module == "pty":
        return name in _PTY_FUNCS
    return False


def _attr_is_exec(module: str, attr: str) -> bool:
    # Любой вызов на модуле subprocess — запуск процесса (прежний контракт).
    # У asyncio/os/pty флагаем только исполняющие имена, иначе asyncio.run
    # и os.path дали бы ложные срабатывания.
    if module == "subprocess":
        return True
    return _imported_name_is_exec(module, attr)


def _is_exec_call(
    node: ast.Call,
    bindings: dict[str, tuple[str, str | None]],
    stars: set[str],
) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        if func.id in _EXEC_BARE:
            return True
        info = bindings.get(func.id)
        if info is not None and info[1] is not None:
            return _imported_name_is_exec(info[0], info[1])
        return any(_imported_name_is_exec(module, func.id) for module in stars)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        recv = func.value.id
        info = bindings.get(recv)
        if info is not None and info[1] is None:
            module = info[0]
        elif recv in {"subprocess", "os", "asyncio", "pty"} and recv not in bindings:
            module = recv
        else:
            return False
        return _attr_is_exec(module, func.attr)
    return False


def _call_label(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return ast.dump(func)


def exec_calls_in_source(source: str) -> list[str]:
    """Имена запрещённых вызовов. SyntaxError не глотается."""
    tree = ast.parse(textwrap.dedent(source))
    bindings, stars = _import_bindings(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_exec_call(node, bindings, stars):
            found.append(_call_label(node.func))
    return found


def _closure_callables(fn: Callable) -> list[Callable]:
    out: list[Callable] = []
    closure = getattr(fn, "__closure__", None) or ()
    for cell in closure:
        try:
            value = cell.cell_contents
        except ValueError:
            continue
        if callable(value) and value is not fn:
            out.append(value)
    return out


def exec_calls_of(fn: Callable, *, seen: set[int] | None = None, label: str | None = None) -> list[str]:
    """Запрещённые вызовы в fn и в callable из его замыкания."""
    seen = seen if seen is not None else set()
    ident = id(fn)
    if ident in seen:
        return []
    seen.add(ident)
    name = label or getattr(fn, "__qualname__", repr(fn))
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError) as exc:
        pytest.fail(f"{name}: исходник не читается ({exc})")
    found = exec_calls_in_source(source)
    for inner in _closure_callables(fn):
        found.extend(exec_calls_of(inner, seen=seen, label=name))
    return found


def _read_python(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        pytest.fail(f"нечитаемый исходник {path}: {exc}")


def scan_registry_packages() -> list[str]:
    """AST всего пакета реестра, не только замыканий исполнителей."""
    roots = [_REPO_ROOT / rel for rel in _PACKAGE_ROOTS]
    existing = [root for root in roots if root.is_dir()]
    if not existing:
        pytest.fail(
            "реестр загружен, но нет каталогов исходников: "
            + ", ".join(_PACKAGE_ROOTS)
        )
    hits: list[str] = []
    for root in existing:
        for path in sorted(root.rglob("*.py")):
            source = _read_python(path)
            try:
                found = exec_calls_in_source(source)
            except SyntaxError as exc:
                pytest.fail(f"SyntaxError в {path}: {exc}")
            if found:
                hits.append(f"{path}: {', '.join(found)}")
    return hits


def _module_string_constants(tree: ast.AST) -> dict[str, str]:
    env: dict[str, str] = {}
    for node in tree.body if isinstance(tree, ast.Module) else []:
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        if (
            len(targets) == 1
            and isinstance(targets[0], ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            env[targets[0].id] = value.value
    return env


def _shutil_bindings(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    modules: set[str] = set()
    funcs: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "shutil":
                    modules.add(alias.asname or "shutil")
        elif isinstance(node, ast.ImportFrom) and node.module == "shutil":
            for alias in node.names:
                if alias.name == "*":
                    for fname in (*_SHUTIL_WRITE, "rmtree"):
                        funcs[fname] = fname
                elif alias.name in _SHUTIL_WRITE or alias.name == "rmtree":
                    funcs[alias.asname or alias.name] = alias.name
    return modules, funcs


def _sketch(node: ast.AST | None, env: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.Attribute):
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "settings"
            and node.attr == "data_dir"
        ):
            return "{settings.data_dir}"
        base = _sketch(node.value, env)
        if base is None:
            return None
        return f"{base}.{node.attr}"
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        left = _sketch(node.left, env)
        right = _sketch(node.right, env)
        if left is None and right is None:
            return None
        sep = "/" if isinstance(node.op, ast.Div) else ""
        return f"{left if left is not None else _DYN}{sep}{right if right is not None else _DYN}"
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                parts.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                inner = _sketch(part.value, env)
                if inner is None:
                    return None
                parts.append(inner)
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "_data_root":
            return "{settings.data_dir}"
        if node.func.id == "Path" and node.args:
            return _sketch(node.args[0], env)
    return None


def _bind_env(fn_node: ast.AST, env: dict[str, str]) -> dict[str, str]:
    """Имя с несколькими разными присваиваниями становится неизвестным (denied)."""
    bound = dict(env)
    seen: dict[str, str | None] = {}

    def note(name: str, sketch: str | None) -> None:
        if name not in seen:
            seen[name] = sketch
        elif seen[name] != sketch:
            seen[name] = None
        if seen[name] is None:
            bound.pop(name, None)
        else:
            bound[name] = seen[name]

    def walk(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn_node:
            return
        if isinstance(node, ast.Assign):
            sketch = _sketch(node.value, bound)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    note(target.id, sketch)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            note(node.target.id, _sketch(node.value, bound))
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            note(node.target.id, _sketch(node.value, bound))
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            note(node.target.id, None)
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(fn_node)
    return bound


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _mode_from_call(call: ast.Call, index: int, default: str | None) -> str | None:
    for kw in call.keywords:
        if kw.arg == "mode":
            return _const_str(kw.value)
    if len(call.args) > index:
        return _const_str(call.args[index])
    return default


def _mode_is_write(mode: str | None) -> bool:
    if mode is None:
        return True
    return any(ch in mode for ch in "wax+")


def _path_segments(sketch: str) -> list[str]:
    return [part for part in sketch.replace("\\", "/").split("/") if part]


def _has_dotdot(sketch: str) -> bool:
    """Сегмент `..` и в исходной строке, и после нормализации `.` / `..`."""
    if any(part == ".." for part in _path_segments(sketch)):
        return True
    normalized: list[str] = []
    for part in sketch.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if normalized:
                normalized.pop()
            else:
                normalized.append("..")
        else:
            normalized.append(part)
    return any(part == ".." for part in normalized)


def _under_snapshot(sketch: str) -> bool:
    if _has_dotdot(sketch):
        return False
    segments = _path_segments(sketch)
    roots = (
        ["data", _SNAPSHOT_DIR],
        ["{settings.data_dir}", _SNAPSHOT_DIR],
    )
    return any(segments[: len(root)] == root for root in roots)


def _path_permitted(sketch: str, action_id: str) -> bool:
    if _has_dotdot(sketch):
        return False
    if _under_snapshot(sketch):
        return action_id in _SNAPSHOT_ALLOW
    if _DYN in sketch:
        return False
    if _is_allow_path(sketch):
        return True
    return action_id == _RAG_ID and _is_rag_path(sketch)


def _is_allow_path(sketch: str) -> bool:
    text = sketch.replace("\\", "/")
    for marker in _ALLOW_MARKERS:
        start = 0
        while True:
            idx = text.find(marker, start)
            if idx < 0:
                break
            before_ok = idx == 0 or text[idx - 1] in "/{."
            after = text[idx + len(marker):]
            after_ok = after == "" or after.startswith("/")
            if before_ok and after_ok:
                return True
            start = idx + 1
    return False


def _is_rag_path(sketch: str) -> bool:
    text = sketch.replace("\\", "/")
    return any(text == "{settings.data_dir}/" + suffix for suffix in _RAG_SUFFIXES)


def _kind_compare(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return None
    left = node.left
    right = node.comparators[0]
    if not (
        isinstance(left, ast.Attribute)
        and left.attr == "kind"
        and isinstance(left.value, ast.Name)
        and left.value.id == "h"
        and isinstance(right, ast.Name)
        and right.id == "_FILE_KINDS"
    ):
        return None
    if isinstance(node.ops[0], ast.In):
        return "in"
    if isinstance(node.ops[0], ast.NotIn):
        return "not in"
    return None


def _if_proved_false(test: ast.AST, file_kind: str | None, file_kinds: frozenset[str]) -> bool:
    if file_kind is None:
        return False
    for node in ast.walk(test):
        gate = _kind_compare(node)
        if gate == "in" and file_kind not in file_kinds:
            return True
        if gate == "not in" and file_kind in file_kinds:
            return True
    return False


def _if_proved_true(test: ast.AST, file_kind: str | None, file_kinds: frozenset[str]) -> bool:
    if file_kind is None:
        return False
    gate = _kind_compare(test)
    if gate == "in" and file_kind in file_kinds:
        return True
    if gate == "not in" and file_kind not in file_kinds:
        return True
    return False


def _live_calls(
    fn_node: ast.AST,
    *,
    file_kind: str | None,
    file_kinds: frozenset[str],
) -> list[ast.Call]:
    found: list[ast.Call] = []

    def walk(node: ast.AST, *, root: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)) and not root:
            return
        if isinstance(node, ast.If):
            if _if_proved_false(node.test, file_kind, file_kinds):
                for stmt in node.orelse:
                    walk(stmt, root=False)
                return
            if _if_proved_true(node.test, file_kind, file_kinds):
                for stmt in node.body:
                    walk(stmt, root=False)
                return
            for stmt in (*node.body, *node.orelse):
                walk(stmt, root=False)
            return
        if isinstance(node, ast.Call):
            found.append(node)
        for child in ast.iter_child_nodes(node):
            walk(child, root=False)

    walk(fn_node, root=True)
    return found


def _os_move_bindings(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    modules: set[str] = set()
    funcs: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    modules.add(alias.asname or "os")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".", 1)[0] == "os":
            for alias in node.names:
                if alias.name == "*":
                    for fname in _OS_RENAME:
                        funcs[fname] = fname
                elif alias.name in _OS_RENAME:
                    funcs[alias.asname or alias.name] = alias.name
    return modules, funcs


def _call_arg(call: ast.Call, index: int, *keywords: str) -> ast.AST | None:
    if len(call.args) > index:
        return call.args[index]
    for kw in call.keywords:
        if kw.arg in keywords:
            return kw.value
    return None


def _write_hit(
    call: ast.Call,
    env: dict[str, str],
    function: str,
    action_id: str,
    shutil_modules: set[str],
    shutil_funcs: dict[str, str],
    os_modules: set[str],
    os_funcs: dict[str, str],
) -> WriteHit | None:
    func = call.func
    kind: str | None = None
    dest: ast.AST | None = None
    src: ast.AST | None = None
    if isinstance(func, ast.Name) and func.id == "open":
        if _mode_is_write(_mode_from_call(call, 1, "r" if call.args else None)):
            kind = "open"
            dest = _call_arg(call, 0, "file")
    elif isinstance(func, ast.Name) and func.id in shutil_funcs:
        attr = shutil_funcs[func.id]
        kind = f"shutil.{attr}"
        if attr == "rmtree":
            dest = _call_arg(call, 0, "path")
        else:
            src_i, dst_i = _SHUTIL_WRITE[attr]
            if src_i is not None:
                src = _call_arg(call, src_i, "src")
            dest = _call_arg(call, dst_i, "dst")
    elif isinstance(func, ast.Name) and func.id in os_funcs:
        attr = os_funcs[func.id]
        kind = f"os.{attr}"
        src = _call_arg(call, 0, "src")
        dest = _call_arg(call, 1, "dst")
    elif isinstance(func, ast.Attribute):
        attr = func.attr
        if attr == "open":
            if _mode_is_write(_mode_from_call(call, 0, "r" if not call.args else None)):
                kind = "open"
                dest = func.value
        elif attr in {"write_text", "write_bytes"}:
            kind = attr
            dest = func.value
        elif isinstance(func.value, ast.Name) and func.value.id in shutil_modules:
            if attr == "rmtree":
                kind = "shutil.rmtree"
                dest = _call_arg(call, 0, "path")
            elif attr in _SHUTIL_WRITE:
                kind = f"shutil.{attr}"
                src_i, dst_i = _SHUTIL_WRITE[attr]
                if src_i is not None:
                    src = _call_arg(call, src_i, "src")
                dest = _call_arg(call, dst_i, "dst")
        elif (
            attr in _OS_RENAME
            and isinstance(func.value, ast.Name)
            and func.value.id in os_modules
        ):
            kind = f"os.{attr}"
            src = _call_arg(call, 0, "src")
            dest = _call_arg(call, 1, "dst")
    if kind is None:
        return None
    sketch = _sketch(dest, env)
    source = _sketch(src, env) if src is not None else None
    if kind == "shutil.rmtree":
        return WriteHit(
            kind,
            sketch,
            function,
            source=None,
            allowed=False,
            snapshot_delete=sketch is None or _under_snapshot(sketch),
        )
    if kind in {"shutil.move", "os.rename", "os.replace"}:
        dest_ok = bool(sketch) and _path_permitted(sketch, action_id)
        src_ok = bool(source) and _path_permitted(source, action_id)
        return WriteHit(
            kind,
            sketch,
            function,
            source=source,
            allowed=dest_ok and src_ok,
            snapshot_delete=source is None or _under_snapshot(source),
        )
    return WriteHit(
        kind,
        sketch,
        function,
        source=None,
        allowed=bool(sketch) and _path_permitted(sketch, action_id),
        snapshot_delete=False,
    )


def analyze_module_source(source: str, action_id: str) -> list[WriteHit]:
    """Записи ФС во всех функциях фрагмента. Путь вне allowlist — не allowed."""
    tree = ast.parse(textwrap.dedent(source))
    consts = _module_string_constants(tree)
    shutil_modules, shutil_funcs = _shutil_bindings(tree)
    os_modules, os_funcs = _os_move_bindings(tree)
    hits: list[WriteHit] = []
    for fn_node in tree.body:
        if not isinstance(fn_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        env = _bind_env(fn_node, consts)
        for call in _live_calls(fn_node, file_kind=None, file_kinds=frozenset()):
            hit = _write_hit(
                call,
                env,
                fn_node.name,
                action_id,
                shutil_modules,
                shutil_funcs,
                os_modules,
                os_funcs,
            )
            if hit is not None:
                hits.append(hit)
    return hits


def _handler_kind(fn: Callable) -> str | None:
    for cell in getattr(fn, "__closure__", None) or ():
        try:
            value = cell.cell_contents
        except ValueError:
            continue
        kind = getattr(value, "kind", None)
        if isinstance(kind, str):
            return kind
    return None


def _file_kinds_of(fn: Callable) -> frozenset[str]:
    value = getattr(fn, "__globals__", {}).get("_FILE_KINDS")
    if isinstance(value, frozenset) and all(isinstance(item, str) for item in value):
        return value
    return frozenset({"file_voice", "file_music"})


def _callee(owner: Callable, call: ast.Call):
    func = call.func
    if not isinstance(func, ast.Name):
        return None
    module = inspect.getmodule(owner)
    if module is None:
        return None
    target = getattr(module, func.id, None)
    if inspect.isfunction(target) or inspect.iscoroutinefunction(target):
        return target
    return None


def reachable_write_hits(fn: Callable, action_id: str) -> list[WriteHit]:
    """Записи по AST исполнителя, его замыканий и функций того же модуля, которых он зовёт по имени."""
    hits: list[WriteHit] = []
    seen: set[int] = set()

    def visit(call_fn: Callable, *, prune: bool) -> None:
        ident = id(call_fn)
        if ident in seen:
            return
        seen.add(ident)
        label = f"{action_id}:{getattr(call_fn, '__qualname__', repr(call_fn))}"
        try:
            source = inspect.getsource(call_fn)
            filename = inspect.getfile(call_fn)
        except (OSError, TypeError) as exc:
            pytest.fail(f"{label}: исходник не читается ({exc})")
        try:
            module_src = Path(filename).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            pytest.fail(f"{label}: нечитаемый исходник {filename} ({exc})")
        try:
            entry = ast.parse(textwrap.dedent(source))
            module = ast.parse(module_src)
        except SyntaxError as exc:
            pytest.fail(f"{label}: SyntaxError ({exc})")
        fn_nodes = [
            node
            for node in entry.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if len(fn_nodes) != 1:
            pytest.fail(f"{label}: в исходнике {len(fn_nodes)} функций, ожидалась одна")
        fn_node = fn_nodes[0]
        file_kind = _handler_kind(call_fn) if prune else None
        file_kinds = _file_kinds_of(call_fn) if file_kind is not None else frozenset()
        consts = _module_string_constants(module)
        env = _bind_env(fn_node, consts)
        shutil_modules, shutil_funcs = _shutil_bindings(module)
        os_modules, os_funcs = _os_move_bindings(module)
        for call in _live_calls(fn_node, file_kind=file_kind, file_kinds=file_kinds):
            hit = _write_hit(
                call,
                env,
                fn_node.name,
                action_id,
                shutil_modules,
                shutil_funcs,
                os_modules,
                os_funcs,
            )
            if hit is not None:
                hits.append(hit)
            target = _callee(call_fn, call)
            if target is not None and target is not call_fn:
                visit(target, prune=False)
        for inner in _closure_callables(call_fn):
            visit(inner, prune=False)

    visit(fn, prune=True)
    return hits


def test_audit_header_counters() -> None:
    header = load_audit_header()
    modify_ids = modify_ids_from_audit()
    assert header["source"] == "#342"
    assert header["sha"].startswith("3ec7c957")
    assert header["date"] == "2026-10-17"
    assert header["actions"] == 102
    assert header["keep"] == 93
    assert header["modify"] == 9
    assert header["remove"] == 0
    assert header["actions"] == header["keep"] + header["modify"] + header["remove"]
    assert modify_ids == [
        "prompt.save_master",
        "prompt.create_master",
        "prompt.delete_master",
        "group.save_from_selection",
        "group.update",
        "group.delete",
        "montage.upload_voice",
        "montage.upload_music",
        "history.rollback",
    ]
    assert len(modify_ids) == header["modify"]
    assert header["fs_breach"] == ()
    assert set(header["snapshot_allow"]) == _SNAPSHOT_ALLOW


def test_empty_registry_is_not_green() -> None:
    header = load_audit_header()
    with pytest.raises(AssertionError, match="реестр пуст"):
        _assert_registry_counts([], header)


def test_skip_only_when_registry_module_itself_is_missing() -> None:
    spec = "app.services.action_layer.spec"
    assert _registry_module_absent(ModuleNotFoundError(name=spec), spec)
    assert _registry_module_absent(ModuleNotFoundError(name="app.services.action_layer"), spec)
    assert _registry_module_absent(ModuleNotFoundError(name="app.services"), spec)
    assert not _registry_module_absent(ModuleNotFoundError(name="sqlalchemy"), spec)
    assert not _registry_module_absent(
        ModuleNotFoundError(name="app.services.action_layer.builtin_wave3"),
        spec,
    )
    assert not _registry_module_absent(ModuleNotFoundError(), spec)


def test_exec_detector_flags_specific_calls() -> None:
    source = (
        "import subprocess, os, asyncio, pty\n"
        "from subprocess import check_output as co\n"
        "from os import popen as op, execv as ev\n"
        "from asyncio import create_subprocess_exec as cse, create_subprocess_shell as css\n"
        "from pty import spawn as ps\n"
        "import re\n"
        "def sample(ctx):\n"
        "    subprocess.run(['echo'])\n"
        "    subprocess.Popen(['echo'])\n"
        "    asyncio.create_subprocess_exec('echo')\n"
        "    asyncio.create_subprocess_shell('echo')\n"
        "    os.system('echo')\n"
        "    os.popen('echo')\n"
        "    os.execv('/bin/echo', ['echo'])\n"
        "    os.spawnv(os.P_NOWAIT, '/bin/echo', ['echo'])\n"
        "    os.posix_spawn('/bin/echo', (), (), ())\n"
        "    pty.spawn('/bin/echo')\n"
        "    co(['echo'])\n"
        "    op('echo')\n"
        "    ev('/bin/echo', ['echo'])\n"
        "    cse('echo')\n"
        "    css('echo')\n"
        "    ps('/bin/echo')\n"
        "    eval('1')\n"
        "    exec('x=1')\n"
        "    compile('x=1', '<s>', 'exec')\n"
        "    __import__('os')\n"
        "    re.compile(r'a')\n"
        "    return ctx.call('GET', '/projects/1')\n"
    )
    expected = [
        "subprocess.run",
        "subprocess.Popen",
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "os.system",
        "os.popen",
        "os.execv",
        "os.spawnv",
        "os.posix_spawn",
        "pty.spawn",
        "co",
        "op",
        "ev",
        "cse",
        "css",
        "ps",
        "eval",
        "exec",
        "compile",
        "__import__",
    ]
    found = exec_calls_in_source(source)
    assert found == expected
    assert "re.compile" not in found


def test_exec_detector_ignores_internal_api_call() -> None:
    source = (
        "async def sample(ctx, params):\n"
        "    return await ctx.call('GET', '/projects/1')\n"
    )
    assert exec_calls_in_source(source) == []


def test_syntax_error_is_not_an_empty_result() -> None:
    with pytest.raises(SyntaxError):
        exec_calls_in_source("def (")


def test_unreadable_source_fails(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="нечитаемый исходник"):
        _read_python(tmp_path)


def test_exec_detector_sees_check_output_in_closure() -> None:
    def backup():
        import subprocess

        subprocess.check_output(["echo"])

    def make(inner):
        def wrapped():
            return inner()

        return wrapped

    found = exec_calls_of(make(backup))
    assert "subprocess.check_output" in found


def test_fs_allowlist_reports_and_node_overrides() -> None:
    source = (
        "def save(path):\n"
        "    open('data/reports/a.txt', 'w')\n"
        "    open('data/node_overrides/b.json', 'a')\n"
        "    path.joinpath('data/reports/c.txt').write_text('x')\n"
        "    reports = 'data/node_overrides/d.bin'\n"
        "    open(reports, 'wb')\n"
        "    Path('data/reports/e.txt').open('w')\n"
        "    Path('data/reports/f.txt').write_bytes(b'z')\n"
    )
    # path.joinpath(...) не сводится к литералу data/reports — это отказ.
    hits = analyze_module_source(source, "prompt.save_master")
    allowed = [hit for hit in hits if hit.allowed]
    denied = [hit for hit in hits if not hit.allowed]
    assert {hit.kind for hit in allowed} >= {"open", "write_bytes"}
    assert len(allowed) == 5
    assert len(denied) == 1
    assert denied[0].kind == "write_text"


def test_fs_rejects_templates_and_foreign_snapshots() -> None:
    source = (
        "import shutil\n"
        "SNAPSHOT_DIR = 'action_layer_snapshots'\n"
        "def save(src):\n"
        "    open('templates/node_groups/a.json', 'w')\n"
        "    shutil.copy2(src, 'data/action_layer_snapshots/x')\n"
        "def _backup_file(src):\n"
        "    folder = _data_root() / SNAPSHOT_DIR / 'abc'\n"
        "    dst = folder / 'f'\n"
        "    shutil.copy2(src, dst)\n"
    )
    foreign = analyze_module_source(source, "prompt.save_master")
    assert foreign and all(not hit.allowed for hit in foreign)
    own = [hit for hit in analyze_module_source(source, "history.rollback") if hit.function == "_backup_file"]
    assert len(own) == 1 and own[0].allowed and own[0].kind == "shutil.copy2"
    assert not own[0].snapshot_delete


def test_rag_reindex_allows_only_its_db_files() -> None:
    source = (
        "def reindex():\n"
        "    db = settings.data_dir / 'orch_rag.db'\n"
        "    wal = settings.data_dir / 'orch_rag.db-wal'\n"
        "    shm = settings.data_dir / 'orch_rag.db-shm'\n"
        "    other = settings.data_dir / 'other.db'\n"
        "    nested = settings.data_dir / 'sub' / 'orch_rag.db'\n"
        "    db.write_bytes(b'')\n"
        "    wal.write_bytes(b'')\n"
        "    shm.write_bytes(b'')\n"
        "    other.write_bytes(b'')\n"
        "    nested.write_bytes(b'')\n"
    )
    own = analyze_module_source(source, _RAG_ID)
    assert [hit.allowed for hit in own] == [True, True, True, False, False]
    foreign = analyze_module_source(source, "prompt.save_master")
    assert foreign and all(not hit.allowed for hit in foreign)


def test_path_open_wb_is_a_write_and_rb_is_not() -> None:
    source = (
        "def save(tmp):\n"
        "    tmp.open('wb')\n"
        "    tmp.open('rb')\n"
        "    open('data/reports/a.txt', 'r')\n"
    )
    hits = analyze_module_source(source, "prompt.save_master")
    assert len(hits) == 1
    assert hits[0].kind == "open"
    assert hits[0].allowed is False


def test_fix_bugs_is_forbidden() -> None:
    assert "fix_bugs" in FORBIDDEN_IDS
    assert is_forbidden_action_id("fix_bugs")
    assert is_forbidden_action_id("browser.fix_bugs")
    assert not is_forbidden_action_id("project.pause")


def test_rmtree_is_always_denied() -> None:
    source = (
        "import shutil\n"
        "def wipe():\n"
        "    shutil.rmtree('data/reports/old')\n"
        "    shutil.rmtree('data/node_overrides/old')\n"
        "    shutil.rmtree('data/action_layer_snapshots')\n"
        "    shutil.rmtree('templates/node_groups')\n"
    )
    for action_id in (*_SNAPSHOT_ALLOW, "prompt.save_master"):
        hits = analyze_module_source(source, action_id)
        assert len(hits) == 4
        assert all(hit.kind == "shutil.rmtree" and not hit.allowed for hit in hits)
        assert hits[2].snapshot_delete


def test_move_and_rename_check_source_and_dest() -> None:
    source = (
        "import shutil, os\n"
        "from os import rename as rn, replace as rp\n"
        "def save(src):\n"
        "    shutil.move('data/reports/a', 'data/reports/b')\n"
        "    shutil.move('templates/a', 'data/reports/a')\n"
        "    shutil.move('data/reports/a', 'templates/b')\n"
        "    os.rename('data/node_overrides/a', 'data/reports/a')\n"
        "    os.replace('templates/a', 'data/node_overrides/a')\n"
        "    rn('data/reports/a', 'secrets/a')\n"
        "    rp('data/action_layer_snapshots/a', 'data/reports/a')\n"
    )
    hits = analyze_module_source(source, "history.rollback")
    assert [hit.allowed for hit in hits] == [True, False, False, True, False, False, True]
    assert [hit.snapshot_delete for hit in hits] == [False, False, False, False, False, False, True]
    foreign = analyze_module_source(source, "group.delete")
    assert foreign[-1].allowed is False
    assert foreign[-1].snapshot_delete


def test_reassigned_name_is_unknown_and_denied() -> None:
    changed = (
        "def save():\n"
        "    path = 'data/reports/a.txt'\n"
        "    path = 'templates/secret.txt'\n"
        "    open(path, 'w')\n"
    )
    same = (
        "def save():\n"
        "    path = 'data/reports/a.txt'\n"
        "    path = 'data/reports/a.txt'\n"
        "    open(path, 'w')\n"
    )
    unknown = (
        "def save(other):\n"
        "    path = 'data/reports/a.txt'\n"
        "    path = other\n"
        "    open(path, 'w')\n"
    )
    changed_hits = analyze_module_source(changed, "prompt.save_master")
    same_hits = analyze_module_source(same, "prompt.save_master")
    unknown_hits = analyze_module_source(unknown, "prompt.save_master")
    assert len(changed_hits) == 1 and changed_hits[0].allowed is False
    assert changed_hits[0].sketch is None
    assert len(same_hits) == 1 and same_hits[0].allowed is True
    assert len(unknown_hits) == 1 and unknown_hits[0].allowed is False


def test_dotdot_segment_is_rejected() -> None:
    source = (
        "def save():\n"
        "    open('data/reports/../secrets.txt', 'w')\n"
        "    open('data/reports/a/../../node_overrides/b.txt', 'w')\n"
        "    open('../data/reports/a.txt', 'w')\n"
        "    open('data/reports/./ok.txt', 'w')\n"
        "    open('data/node_overrides/../reports/c.txt', 'w')\n"
    )
    hits = analyze_module_source(source, "prompt.save_master")
    assert [hit.allowed for hit in hits] == [False, False, False, True, False]


def test_snapshot_dir_only_for_three_actions_and_they_do_not_delete() -> None:
    backup = (
        "import shutil\n"
        "SNAPSHOT_DIR = 'action_layer_snapshots'\n"
        "def _backup_file(state):\n"
        "    src = Path(state['real_path'])\n"
        "    folder = _data_root() / SNAPSHOT_DIR / uuid.uuid4().hex\n"
        "    dst = folder / src.name\n"
        "    shutil.copy2(src, dst)\n"
    )
    for action_id in _SNAPSHOT_ALLOW:
        hits = analyze_module_source(backup, action_id)
        assert len(hits) == 1
        assert hits[0].allowed
        assert hits[0].snapshot_delete is False
    other = analyze_module_source(backup, "prompt.save_master")
    assert len(other) == 1 and other[0].allowed is False
    deleting = (
        "import shutil, os\n"
        "def save():\n"
        "    shutil.rmtree('data/action_layer_snapshots/x')\n"
        "    shutil.move('data/action_layer_snapshots/a', 'data/reports/a')\n"
        "    os.rename('data/action_layer_snapshots/b', 'data/reports/b')\n"
    )
    for action_id in _SNAPSHOT_ALLOW:
        hits = analyze_module_source(deleting, action_id)
        assert all(hit.snapshot_delete for hit in hits)
        assert hits[0].allowed is False


def test_whitelist_has_no_code_exec_tools() -> None:
    assert sys.version_info >= _MIN_PY, (
        "zip(strict=True) требует Python 3.10+; CI этого репозитория — 3.12"
    )
    header = load_audit_header()
    modify_ids = modify_ids_from_audit()
    assert len(modify_ids) == header["modify"]
    assert header["fs_breach"] == ()
    assert set(header["snapshot_allow"]) == _SNAPSHOT_ALLOW
    assert set(header["snapshot_allow"]) <= set(modify_ids)

    module = _load_registry_module()
    specs = _specs(module)
    ids = [_action_id(spec) for spec in specs]
    _assert_registry_counts(ids, header)

    forbidden_hits = sorted(action_id for action_id in ids if is_forbidden_action_id(action_id))
    assert not forbidden_hits, (
        "в реестре есть запрещённые инструменты правки кода/исполнения: "
        + ", ".join(forbidden_hits)
    )

    package_hits = scan_registry_packages()
    assert not package_hits, "в пакете реестра есть exec-вызовы:\n" + "\n".join(package_hits)

    by_id = dict(zip(ids, specs, strict=True))
    missing = [action_id for action_id in modify_ids if action_id not in by_id]
    assert not missing, "в реестре нет действий modify из аудита: " + ", ".join(missing)

    offenders: list[str] = []
    unexpected: list[str] = []
    watched = set(modify_ids) | set(header["snapshot_allow"])
    for action_id, spec in zip(ids, specs, strict=True):
        fn = _executor(spec)
        if fn is None:
            continue
        if exec_calls_of(fn, label=action_id):
            offenders.append(action_id)
        if action_id not in watched:
            continue
        hits = reachable_write_hits(fn, action_id)
        bad = [hit for hit in hits if not hit.allowed]
        if bad:
            unexpected.append(action_id + ": " + "; ".join(str(hit) for hit in bad))
        if action_id in header["snapshot_allow"]:
            deletes = [hit for hit in hits if hit.snapshot_delete]
            if deletes:
                unexpected.append(
                    action_id + " удаляет снимок: " + "; ".join(str(hit) for hit in deletes)
                )
    assert not offenders, (
        "исполнители выглядят как обёртки subprocess/eval/exec/compile: "
        + ", ".join(sorted(offenders))
    )
    assert not unexpected, (
        "запись в ФС вне allowlist либо удаление снимка:\n" + "\n".join(unexpected)
    )
