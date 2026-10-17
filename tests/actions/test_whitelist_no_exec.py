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
import os
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
_OS_DELETE = frozenset({"remove", "unlink", "rmdir", "removedirs"})
_PATH_DELETE = frozenset({"unlink", "rmdir"})
_PATH_CREATE = frozenset({"touch", "mkdir"})
_PATH_MOVE = frozenset({"rename", "replace"})
_DELETE_KINDS = frozenset(
    {
        "shutil.rmtree",
        "os.remove",
        "os.unlink",
        "os.rmdir",
        "os.removedirs",
        "os.rename",
        "os.replace",
        "os.truncate",
        "Path.unlink",
        "Path.rmdir",
        "Path.rename",
        "Path.replace",
        "Path.truncate",
        "truncate",
        "getattr",
        "importlib.import_module",
        "__import__",
        "sql",
        "http.delete",
    }
)
_SQL_WORD = re.compile(r"\b(DELETE|DROP|TRUNCATE)\b", re.IGNORECASE)
_FS_ROOTS = frozenset({"os", "shutil", "pathlib"})
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
        except ImportError as exc:
            pytest.fail(
                f"ошибка импорта реестра {name}: {type(exc).__name__}: {exc}"
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


def _resolve_path(*parts: str) -> str | None:
    """Склеивает части и схлопывает `.` и `..`. Неоднозначно или выше корня — None."""
    segments: list[str] = []
    absolute = False
    for part in parts:
        text = part.replace("\\", "/")
        if text.startswith("/"):
            absolute = True
            segments = []
        for seg in text.split("/"):
            if seg in ("", "."):
                continue
            if seg == "..":
                if not segments or segments[-1] in {_DYN, ".."}:
                    return None
                segments.pop()
                continue
            if _DYN in seg:
                segments.append(_DYN)
            else:
                segments.append(seg)
    if any(seg == ".." for seg in segments):
        return None
    body = "/".join(segments)
    return f"/{body}" if absolute else body


def _is_path_join(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "join":
        return True
    if not isinstance(func, ast.Attribute) or func.attr != "join":
        return False
    value = func.value
    if isinstance(value, ast.Name) and value.id in {"os", "path"}:
        return True
    return (
        isinstance(value, ast.Attribute)
        and value.attr == "path"
        and isinstance(value.value, ast.Name)
    )


def _sketch(node: ast.AST | None, env: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _resolve_path(node.value)
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.Attribute):
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "settings"
            and node.attr == "data_dir"
        ):
            return "{settings.data_dir}"
        if node.attr != "parent":
            return None
        base = _sketch(node.value, env)
        if base is None:
            return None
        return _resolve_path(base, "..")
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        left = _sketch(node.left, env)
        right = _sketch(node.right, env)
        if left is None and right is None:
            return None
        sep = "/" if isinstance(node.op, ast.Div) else ""
        return _resolve_path(
            f"{left if left is not None else _DYN}{sep}{right if right is not None else _DYN}"
        )
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
        return _resolve_path("".join(parts))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "_data_root":
            return "{settings.data_dir}"
        if node.func.id == "Path":
            if not node.args:
                return None
            pieces: list[str] = []
            for arg in node.args:
                piece = _sketch(arg, env)
                if piece is None:
                    return None
                pieces.append(piece)
            return _resolve_path(*pieces)
    if isinstance(node, ast.Call) and _is_path_join(node):
        if not node.args:
            return None
        pieces = []
        for arg in node.args:
            piece = _sketch(arg, env)
            if piece is None:
                return None
            pieces.append(piece)
        return _resolve_path(*pieces)
    return None


def _target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for elt in target.elts:
            names.extend(_target_names(elt))
        return names
    return []


def _param_names(fn_node: ast.AST) -> list[str]:
    args = getattr(fn_node, "args", None)
    if args is None:
        return []
    names = [arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    if args.vararg is not None:
        names.append(args.vararg.arg)
    if args.kwarg is not None:
        names.append(args.kwarg.arg)
    return names


def _bind_env(fn_node: ast.AST, env: dict[str, str]) -> dict[str, str]:
    """Неоднозначное имя (параметр, ветка, распаковка, переприсваивание) — denied."""
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

    for name in _param_names(fn_node):
        note(name, None)

    def walk(node: ast.AST, *, conditional: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn_node:
            return
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                note(name, None)
        if isinstance(node, (ast.For, ast.AsyncFor)):
            for name in _target_names(node.target):
                note(name, None)
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for name in _target_names(item.optional_vars):
                        note(name, None)
        if isinstance(node, ast.ExceptHandler) and isinstance(node.name, str):
            note(node.name, None)
        if isinstance(node, ast.Assign):
            sketch = None if conditional else _sketch(node.value, bound)
            for target in node.targets:
                unpacked = not isinstance(target, ast.Name)
                for name in _target_names(target):
                    note(name, None if unpacked else sketch)
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
        ):
            sketch = None if conditional else _sketch(node.value, bound)
            unpacked = not isinstance(node.target, ast.Name)
            for name in _target_names(node.target):
                note(name, None if unpacked else sketch)
        elif isinstance(node, ast.NamedExpr):
            sketch = None if conditional else _sketch(node.value, bound)
            for name in _target_names(node.target):
                note(name, sketch)
        elif isinstance(node, ast.AugAssign):
            for name in _target_names(node.target):
                note(name, None)
        nested = conditional or isinstance(
            node,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.Try,
                ast.ExceptHandler,
                ast.With,
                ast.AsyncWith,
                ast.IfExp,
            ),
        )
        for child in ast.iter_child_nodes(node):
            walk(child, conditional=nested)

    walk(fn_node, conditional=False)
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


def paths_share_root(path: str, root: str) -> bool:
    """Путь лежит в корне после realpath. Сравнение через commonpath, не по строке."""
    try:
        real_path = os.path.realpath(path)
        real_root = os.path.realpath(root)
        return os.path.commonpath([real_path, real_root]) == real_root
    except (OSError, ValueError):
        return False


def _logical_path(sketch: str) -> str:
    text = sketch.replace("\\", "/").replace(
        "{settings.data_dir}", str(_REPO_ROOT / "data")
    )
    if os.path.isabs(text):
        return text
    return str(_REPO_ROOT / text)


def _sketch_under(sketch: str, root: str) -> bool:
    return paths_share_root(_logical_path(sketch), _logical_path(root))


def _under_snapshot(sketch: str) -> bool:
    if _has_dotdot(sketch):
        return False
    concrete = sketch.replace(_DYN, "dynseg")
    roots = (
        f"data/{_SNAPSHOT_DIR}",
        "{settings.data_dir}/" + _SNAPSHOT_DIR,
    )
    return any(_sketch_under(concrete, root) for root in roots)


def _path_permitted(sketch: str, action_id: str) -> bool:
    resolved = _resolve_path(sketch)
    if not resolved:
        return False
    if _under_snapshot(resolved):
        return action_id in _SNAPSHOT_ALLOW
    if _DYN in resolved:
        return False
    if _is_allow_path(resolved):
        return True
    return action_id == _RAG_ID and _is_rag_path(resolved)


def _is_allow_path(sketch: str) -> bool:
    if _DYN in sketch or _has_dotdot(sketch):
        return False
    return any(_sketch_under(sketch, marker) for marker in _ALLOW_MARKERS)


def _is_rag_path(sketch: str) -> bool:
    if _DYN in sketch or _has_dotdot(sketch):
        return False
    real = os.path.realpath(_logical_path(sketch))
    return any(
        real == os.path.realpath(_logical_path("{settings.data_dir}/" + suffix))
        for suffix in _RAG_SUFFIXES
    )


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


def _top_level_kind_compares(test: ast.AST) -> list[ast.Compare] | None:
    """Сравнение на самом test или на верхнем уровне And. Вложенные not/or не доказываем."""
    if isinstance(test, ast.Compare):
        return [test]
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return [item for item in test.values if isinstance(item, ast.Compare)]
    return None


def _if_proved_false(
    test: ast.AST, file_kind: str | None, file_kinds: frozenset[str] | None
) -> bool:
    if file_kind is None or file_kinds is None:
        return False
    compares = _top_level_kind_compares(test)
    if compares is None:
        return False
    for node in compares:
        gate = _kind_compare(node)
        if gate == "in" and file_kind not in file_kinds:
            return True
        if gate == "not in" and file_kind in file_kinds:
            return True
    return False


def _if_proved_true(
    test: ast.AST, file_kind: str | None, file_kinds: frozenset[str] | None
) -> bool:
    if file_kind is None or file_kinds is None:
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
    file_kinds: frozenset[str] | None,
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
    watched = _OS_RENAME | _OS_DELETE | frozenset({"truncate"})
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root == "os":
                    modules.add(alias.asname or root)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".", 1)[0] == "os":
            for alias in node.names:
                if alias.name == "*":
                    for fname in watched:
                        funcs[fname] = fname
                elif alias.name in watched:
                    funcs[alias.asname or alias.name] = alias.name
    return modules, funcs


def _fs_surface(tree: ast.AST) -> tuple[set[str], set[str], dict[str, str]]:
    """Имена модулей os/shutil/pathlib и алиасы их функций. Алиас сам по себе опасен."""
    modules: set[str] = set()
    path_names = {"Path"}
    tainted: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _FS_ROOTS:
                    modules.add(alias.asname or root)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".", 1)[0] in _FS_ROOTS:
            root = node.module.split(".", 1)[0]
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == "Path":
                    path_names.add(local)
                elif alias.name != "*":
                    tainted[local] = f"{root}.{alias.name}"
    return modules, path_names, tainted


def _local_aliases(
    fn_node: ast.AST,
    modules: set[str],
    path_names: set[str],
    tainted: dict[str, str],
) -> dict[str, str]:
    found = dict(tainted)
    for node in ast.walk(fn_node):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        value = node.value
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Attribute):
            continue
        if not isinstance(value.value, ast.Name):
            continue
        if value.value.id in modules or value.value.id in path_names:
            found[target.id] = f"{value.value.id}.{value.attr}"
    return found


def _const_text(node: ast.AST | None, env: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    return None


def _sql_texts(node: ast.AST, env: dict[str, str]) -> list[str]:
    found: list[str] = []
    direct = _const_text(node, env)
    if direct is not None:
        found.append(direct)
    if isinstance(node, ast.Call):
        for arg in node.args:
            found.extend(_sql_texts(arg, env))
        for kw in node.keywords:
            if kw.arg in {None, "sql", "statement", "sqltext"}:
                found.extend(_sql_texts(kw.value, env))
    if isinstance(node, ast.JoinedStr):
        chunks: list[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                chunks.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                inner = _const_text(part.value, env)
                chunks.append(inner if inner is not None else "")
        found.append("".join(chunks))
    return found


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_sql_delete(call: ast.Call, env: dict[str, str]) -> bool:
    name = _call_name(call.func)
    if name not in {"execute", "executescript", "text"}:
        return False
    blobs: list[str] = []
    for arg in call.args:
        blobs.extend(_sql_texts(arg, env))
    for kw in call.keywords:
        if kw.arg in {None, "sql", "statement", "sqltext"}:
            blobs.extend(_sql_texts(kw.value, env))
    return any(_SQL_WORD.search(blob) for blob in blobs)


def _http_method(call: ast.Call, env: dict[str, str]) -> str | None:
    node = None
    for kw in call.keywords:
        if kw.arg in {"method", "http_method"}:
            node = kw.value
    if node is None and call.args:
        node = call.args[0]
    text = _const_text(node, env)
    if text is None:
        return None
    return text.upper()


def _is_http_delete(call: ast.Call, env: dict[str, str]) -> bool:
    name = _call_name(call.func)
    if name not in {"call", "request"}:
        return False
    return _http_method(call, env) == "DELETE"


def _is_path_replace(call: ast.Call, func: ast.Attribute, path_names: set[str]) -> bool:
    if func.attr != "replace":
        return False
    if isinstance(func.value, ast.Name) and func.value.id in path_names:
        return True
    if any(kw.arg in {"old", "new"} for kw in call.keywords):
        return False
    return len(call.args) == 1 or any(kw.arg == "target" for kw in call.keywords)


def _receiver_is_path(node: ast.AST, env: dict[str, str]) -> bool:
    """Path.replace есть и у str. Файловой операцией считаем только путь."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Path":
        return True
    sketch = _sketch(node, env)
    if not sketch:
        return False
    text = sketch.replace("\\", "/")
    return "/" in text or text.startswith("{settings.data_dir}") or text.startswith("data")


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
    tainted: dict[str, str] | None = None,
    path_names: set[str] | None = None,
) -> WriteHit | None:
    func = call.func
    tainted = tainted or {}
    path_names = path_names or {"Path"}
    kind: str | None = None
    dest: ast.AST | None = None
    src: ast.AST | None = None
    if _call_name(func) == "getattr":
        kind = "getattr"
    elif _call_name(func) == "__import__":
        kind = "__import__"
    elif _call_name(func) == "import_module":
        kind = "importlib.import_module"
    elif _is_sql_delete(call, env):
        kind = "sql"
    elif _is_http_delete(call, env):
        kind = "http.delete"
    elif isinstance(func, ast.Name) and func.id == "open":
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
        if attr in _OS_DELETE:
            dest = _call_arg(call, 0, "path")
        else:
            src = _call_arg(call, 0, "src")
            dest = _call_arg(call, 1, "dst")
    elif isinstance(func, ast.Name) and func.id in tainted:
        kind = "alias:" + tainted[func.id]
        dest = _call_arg(call, 0, "path")
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
            attr in (_OS_RENAME | frozenset({"truncate"}))
            and isinstance(func.value, ast.Name)
            and func.value.id in os_modules
        ):
            kind = f"os.{attr}"
            if attr == "truncate":
                dest = _call_arg(call, 0, "path")
            else:
                src = _call_arg(call, 0, "src")
                dest = _call_arg(call, 1, "dst")
        elif (
            attr in _OS_DELETE
            and isinstance(func.value, ast.Name)
            and func.value.id in os_modules
        ):
            kind = f"os.{attr}"
            dest = _call_arg(call, 0, "path")
        elif attr in _PATH_DELETE or attr == "truncate":
            kind = f"Path.{attr}" if attr != "truncate" else "Path.truncate"
            dest = func.value
        elif attr in _PATH_CREATE:
            kind = f"Path.{attr}"
            dest = func.value
        elif attr == "rename" or _is_path_replace(call, func, path_names):
            kind = f"Path.{attr}"
            src = func.value
            dest = _call_arg(call, 0, "target")
    if kind is None:
        return None
    sketch = _sketch(dest, env)
    source = _sketch(src, env) if src is not None else None
    denied = kind in _DELETE_KINDS or kind.startswith("alias:")
    if denied:
        under = sketch is None or _under_snapshot(sketch)
        if source and _under_snapshot(source):
            under = True
        return WriteHit(
            kind,
            sketch,
            function,
            source=source,
            allowed=False,
            snapshot_delete=under,
        )
    if kind in {"shutil.move"}:
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
    fs_modules, path_names, imported = _fs_surface(tree)
    hits: list[WriteHit] = []
    for fn_node in tree.body:
        if not isinstance(fn_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        env = _bind_env(fn_node, consts)
        tainted = _local_aliases(fn_node, fs_modules | os_modules, path_names, imported)
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
                tainted,
                path_names,
            )
            if hit is not None:
                hits.append(hit)
    return hits


def _rebound_names(fn_node: ast.AST) -> set[str]:
    """Имена, которые внутри функции уже не являются свободной переменной h."""
    found = set(_param_names(fn_node))

    def walk(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn_node:
            return
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            found.update(node.names)
        if isinstance(node, (ast.For, ast.AsyncFor)):
            found.update(_target_names(node.target))
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    found.update(_target_names(item.optional_vars))
        if isinstance(node, ast.ExceptHandler) and isinstance(node.name, str):
            found.add(node.name)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                found.update(_target_names(target))
        elif isinstance(node, ast.AnnAssign):
            found.update(_target_names(node.target))
        elif isinstance(node, (ast.NamedExpr, ast.AugAssign)):
            found.update(_target_names(node.target))
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(fn_node)
    return found


def _closure_cell(fn: Callable, name: str):
    code = getattr(fn, "__code__", None)
    if code is None or name not in code.co_freevars:
        return None
    cells = getattr(fn, "__closure__", None) or ()
    index = code.co_freevars.index(name)
    if index >= len(cells):
        return None
    try:
        return cells[index].cell_contents
    except ValueError:
        return None


def _handler_kind(fn: Callable) -> str | None:
    """kind только из ячейки замыкания h, если h и _FILE_KINDS внутри не затенены."""
    try:
        source = inspect.getsource(fn)
        parsed = ast.parse(textwrap.dedent(source))
    except (OSError, TypeError, SyntaxError):
        return None
    fn_nodes = [
        node
        for node in parsed.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if len(fn_nodes) != 1:
        return None
    rebound = _rebound_names(fn_nodes[0])
    if "h" in rebound or "_FILE_KINDS" in rebound:
        return None
    value = _closure_cell(fn, "h")
    if value is None:
        return None
    kind = getattr(value, "kind", None)
    if isinstance(kind, str):
        return kind
    return None


def _file_kinds_of(fn: Callable) -> frozenset[str] | None:
    value = getattr(fn, "__globals__", {}).get("_FILE_KINDS")
    if isinstance(value, frozenset) and all(isinstance(item, str) for item in value):
        return value
    return None


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
        fs_modules, path_names, imported = _fs_surface(module)
        tainted = _local_aliases(fn_node, fs_modules | os_modules, path_names, imported)
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
                tainted,
                path_names,
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
    assert header["keep"] == 90
    assert header["modify"] == 12
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
        "montage.delete_frame",
        "node.clear_check_agent",
        "storage.clear_files",
    ]
    assert len(modify_ids) == header["modify"]
    assert header["fs_breach"] == (
        "group.delete",
        "montage.delete_frame",
        "node.clear_check_agent",
        "prompt.delete_master",
        "storage.clear_files",
    )
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


def test_missing_registry_module_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str):
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(pytest.skip.Exception, match="Реестр Action Layer в этом дереве отсутствует"):
        _load_registry_module()


def test_other_import_error_fails_not_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(name: str):
        if name == "app.orchestrator.actions":
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        raise ImportError(f"cannot import {name}")

    monkeypatch.setattr(importlib, "import_module", broken)
    with pytest.raises(pytest.fail.Exception, match="ошибка импорта реестра"):
        _load_registry_module()


def test_inner_missing_dependency_fails_not_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(name: str):
        raise ModuleNotFoundError("No module named 'sqlalchemy'", name="sqlalchemy")

    monkeypatch.setattr(importlib, "import_module", broken)
    with pytest.raises(pytest.fail.Exception, match="exc.name='sqlalchemy'"):
        _load_registry_module()


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


def test_shutil_rmtree_alias_is_denied() -> None:
    source = (
        "import shutil as sh\n"
        "from shutil import rmtree as rt\n"
        "def wipe():\n"
        "    sh.rmtree('data/reports/old')\n"
        "    rt('data/action_layer_snapshots/old')\n"
    )
    hits = analyze_module_source(source, "history.rollback")
    assert [hit.kind for hit in hits] == ["shutil.rmtree", "shutil.rmtree"]
    assert [hit.allowed for hit in hits] == [False, False]
    assert [hit.snapshot_delete for hit in hits] == [False, True]


def test_os_remove_is_denied() -> None:
    source = (
        "import os as o\n"
        "def save():\n"
        "    o.remove('data/reports/a.txt')\n"
        "    o.remove('data/action_layer_snapshots/a.txt')\n"
    )
    hits = analyze_module_source(source, "history.rollback")
    assert [hit.kind for hit in hits] == ["os.remove", "os.remove"]
    assert [hit.allowed for hit in hits] == [False, False]
    assert [hit.snapshot_delete for hit in hits] == [False, True]


def test_os_unlink_is_denied() -> None:
    source = (
        "from os import unlink\n"
        "def save():\n"
        "    unlink('templates/a')\n"
        "    unlink('data/action_layer_snapshots/b')\n"
    )
    hits = analyze_module_source(source, "montage.upload_voice")
    assert [hit.kind for hit in hits] == ["os.unlink", "os.unlink"]
    assert [hit.allowed for hit in hits] == [False, False]
    assert [hit.snapshot_delete for hit in hits] == [False, True]


def test_os_rmdir_is_denied() -> None:
    source = (
        "import os\n"
        "def save():\n"
        "    os.rmdir('data/node_overrides/empty')\n"
        "    os.rmdir('data/action_layer_snapshots/empty')\n"
    )
    hits = analyze_module_source(source, "montage.upload_music")
    assert [hit.kind for hit in hits] == ["os.rmdir", "os.rmdir"]
    assert [hit.allowed for hit in hits] == [False, False]
    assert [hit.snapshot_delete for hit in hits] == [False, True]


def test_os_removedirs_is_denied() -> None:
    source = (
        "from os import removedirs as rd\n"
        "def save():\n"
        "    rd('data/reports/nested/empty')\n"
        "    rd('data/action_layer_snapshots/nested')\n"
    )
    hits = analyze_module_source(source, "history.rollback")
    assert [hit.kind for hit in hits] == ["os.removedirs", "os.removedirs"]
    assert [hit.allowed for hit in hits] == [False, False]
    assert [hit.snapshot_delete for hit in hits] == [False, True]


def test_path_unlink_is_denied() -> None:
    source = (
        "def save():\n"
        "    Path('data/reports/a').unlink()\n"
        "    p = Path('data/action_layer_snapshots/a')\n"
        "    p.unlink(missing_ok=True)\n"
    )
    hits = analyze_module_source(source, "history.rollback")
    assert [hit.kind for hit in hits] == ["Path.unlink", "Path.unlink"]
    assert [hit.allowed for hit in hits] == [False, False]
    assert [hit.snapshot_delete for hit in hits] == [False, True]


def test_path_rmdir_is_denied() -> None:
    source = (
        "def save(folder):\n"
        "    Path('data/reports/empty').rmdir()\n"
        "    snap = Path('data/action_layer_snapshots/empty')\n"
        "    snap.rmdir()\n"
        "    folder.rmdir()\n"
    )
    hits = analyze_module_source(source, "history.rollback")
    assert [hit.kind for hit in hits] == ["Path.rmdir", "Path.rmdir", "Path.rmdir"]
    assert [hit.allowed for hit in hits] == [False, False, False]
    assert [hit.snapshot_delete for hit in hits] == [False, True, True]


def test_path_rename_and_replace_check_both_sides() -> None:
    source = (
        "def save(text):\n"
        "    Path('data/reports/a').replace('data/reports/b')\n"
        "    src = Path('templates/a')\n"
        "    src.rename('data/reports/a')\n"
        "    Path('data/action_layer_snapshots/a').replace('data/reports/b')\n"
        "    text.replace('a', 'b')\n"
        "    note = 'прежняя версия'\n"
        "    note.replace('прежняя', '')\n"
    )
    hits = analyze_module_source(source, "history.rollback")
    assert [hit.kind for hit in hits] == ["Path.replace", "Path.rename", "Path.replace"]
    assert [hit.allowed for hit in hits] == [False, False, False]
    assert [hit.snapshot_delete for hit in hits] == [False, False, True]
    foreign = analyze_module_source(source, "group.delete")
    assert foreign[2].allowed is False
    assert foreign[2].snapshot_delete is True


def test_path_touch_and_mkdir_follow_allowlist() -> None:
    source = (
        "def save():\n"
        "    Path('data/reports/a').touch()\n"
        "    Path('templates/a').mkdir()\n"
        "    folder = _data_root() / 'action_layer_snapshots' / 'dyn'\n"
        "    folder.mkdir(parents=True)\n"
    )
    hits = analyze_module_source(source, "history.rollback")
    assert [hit.kind for hit in hits] == ["Path.touch", "Path.mkdir", "Path.mkdir"]
    assert [hit.allowed for hit in hits] == [True, False, True]
    assert [hit.snapshot_delete for hit in hits] == [False, False, False]
    other = analyze_module_source(source, "prompt.save_master")
    assert [hit.allowed for hit in other] == [True, False, False]


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
    assert [hit.allowed for hit in hits] == [True, False, False, False, False, False, False]
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


def test_allow_prefix_is_component_not_substring() -> None:
    source = (
        "def save():\n"
        "    open('data/reports/ok.txt', 'w')\n"
        "    open('data/reports_evil/x', 'w')\n"
        "    open('x/data/reports/../../etc/passwd', 'w')\n"
        "    open('foo_data/reports/a', 'w')\n"
        "    open('app/services/data/reports/x.py', 'w')\n"
        "    open('templates/node_groups/data/reports/a.md', 'w')\n"
        "    open('/etc/data/reports/x', 'w')\n"
    )
    hits = analyze_module_source(source, "prompt.save_master")
    assert [hit.allowed for hit in hits] == [True, False, False, False, False, False, False]


def test_allowed_evil_is_not_under_allowed(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    evil = tmp_path / "allowed_evil"
    root.mkdir()
    evil.mkdir()
    assert paths_share_root(str(root / "ok.txt"), str(root))
    assert not paths_share_root(str(evil / "x"), str(root))
    assert not paths_share_root("/allowed_evil/x", "/allowed")
    assert not paths_share_root("/allowed_evil", "/allowed")


def _symlinks_available() -> bool:
    """Windows без права на symlink даёт WinError 1314. Это не падение теста."""
    if os.name == "nt":
        return False
    probe = Path(os.environ.get("TMPDIR", "/tmp")) / f"vp-symlink-probe-{os.getpid()}"
    try:
        probe.mkdir(parents=True, exist_ok=True)
        os.symlink(str(probe / "target"), str(probe / "link"))
        return True
    except OSError:
        return False
    finally:
        import shutil

        shutil.rmtree(probe, ignore_errors=True)


def test_plain_file_under_reports_stays_allowed(tmp_path: Path) -> None:
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    assert paths_share_root(str(reports / "ok.txt"), str(reports))


@pytest.mark.skipif(not _symlinks_available(), reason="symlink недоступен")
def test_symlink_inside_reports_pointing_outside_is_denied(tmp_path: Path) -> None:
    reports = tmp_path / "data" / "reports"
    outside = tmp_path / "outside"
    reports.mkdir(parents=True)
    outside.mkdir()
    link = reports / "escape"
    link.symlink_to(outside, target_is_directory=True)
    escaped = link / "secret.txt"
    assert not paths_share_root(str(escaped), str(reports))
    source = f"def save():\n    open({str(escaped)!r}, 'w')\n"
    hits = analyze_module_source(source, "prompt.save_master")
    assert len(hits) == 1
    assert hits[0].allowed is False


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
    # После resolve внутрь allowlist путь разрешён, выход наружу — нет.
    assert [hit.allowed for hit in hits] == [False, True, False, True, True]


def test_path_constructor_resolves_every_argument() -> None:
    source = (
        "def save(a):\n"
        "    Path('data/reports', '../../app/x.py').write_text('z')\n"
        "    Path(a, '../..').write_text('z')\n"
        "    Path('data/reports', 'ok.txt').write_text('z')\n"
    )
    hits = analyze_module_source(source, "prompt.save_master")
    assert [hit.allowed for hit in hits] == [False, False, True]


def test_parent_is_resolved_before_allowlist() -> None:
    source = (
        "def save():\n"
        "    inside = (settings.data_dir / 'reports' / 'x').parent / 'ok.txt'\n"
        "    outside = (settings.data_dir / 'reports' / 'x').parent.parent / 'orch.db'\n"
        "    inside.write_text('a')\n"
        "    outside.write_text('b')\n"
    )
    hits = analyze_module_source(source, "prompt.save_master")
    assert [hit.allowed for hit in hits] == [True, False]


def test_os_path_join_is_resolved_before_allowlist() -> None:
    source = (
        "import os\n"
        "ALLOWED = 'data/reports'\n"
        "def save():\n"
        "    open(os.path.join(ALLOWED, '..', 'x'), 'w')\n"
        "    open(os.path.join(ALLOWED, 'ok.txt'), 'w')\n"
    )
    hits = analyze_module_source(source, "prompt.save_master")
    assert [hit.allowed for hit in hits] == [False, True]


def test_allowed_root_constant_is_shadowed() -> None:
    source = (
        "ALLOWED = 'data/reports'\n"
        "TARGET = 'data/reports/a.txt'\n"
        "def save():\n"
        "    ALLOWED = 'templates/secret'\n"
        "    open(os.path.join(ALLOWED, 'x'), 'w')\n"
        "def save_param(TARGET):\n"
        "    open(TARGET, 'w')\n"
        "def save_branch(p, ok):\n"
        "    if ok:\n"
        "        p = 'data/reports/a.txt'\n"
        "    open(p, 'w')\n"
        "def save_rebind():\n"
        "    ALLOWED = 'data/reports/a.txt'\n"
        "    ALLOWED = 'templates/x'\n"
        "    open(ALLOWED, 'w')\n"
    )
    hits = analyze_module_source(source, "prompt.save_master")
    assert hits
    assert all(hit.allowed is False for hit in hits)


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


def _open_targets(source: str, *, file_kind: str, file_kinds: frozenset[str] | None) -> list[str]:
    fn_node = ast.parse(textwrap.dedent(source)).body[0]
    found: list[str] = []
    for call in _live_calls(fn_node, file_kind=file_kind, file_kinds=file_kinds):
        func = call.func
        if not isinstance(func, ast.Name) or func.id != "open" or not call.args:
            continue
        arg = call.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.append(arg.value)
    return found


def test_negated_and_or_file_kind_gates_are_not_pruned() -> None:
    kinds = frozenset({"file_voice", "file_music"})
    negated = (
        "def executor(h):\n"
        "    if not (h.kind in _FILE_KINDS):\n"
        "        open('templates/secret.txt', 'w')\n"
    )
    disjunction = (
        "def executor(h, force):\n"
        "    if h.kind in _FILE_KINDS or force:\n"
        "        open('templates/also.txt', 'w')\n"
    )
    assert _open_targets(negated, file_kind="master_prompt", file_kinds=kinds) == [
        "templates/secret.txt"
    ]
    assert _open_targets(disjunction, file_kind="master_prompt", file_kinds=kinds) == [
        "templates/also.txt"
    ]


def test_and_file_kind_gate_still_prunes_unreachable_body() -> None:
    source = (
        "def executor(h):\n"
        "    if h.kind in _FILE_KINDS and isinstance(h, dict):\n"
        "        open('templates/secret.txt', 'w')\n"
    )
    kinds = frozenset({"file_voice", "file_music"})
    assert _open_targets(source, file_kind="master_prompt", file_kinds=kinds) == []
    assert _open_targets(source, file_kind="file_voice", file_kinds=kinds) == [
        "templates/secret.txt"
    ]


def test_loop_h_is_not_the_closure_handler() -> None:
    spec = type("Handler", (), {"kind": "master_prompt"})()

    def make(spec):
        def executor(items):
            marker = spec
            for h in items:
                if h.kind in _FILE_KINDS:
                    open("templates/secret.txt", "w")
            return marker

        return executor

    handler = type("Handler", (), {"kind": "master_prompt"})()

    def make_bound(h):
        def executor(ctx, params):
            if h.kind in _FILE_KINDS and isinstance(params, dict):
                open("templates/secret.txt", "w")
            return params

        return executor

    fn = make(spec)
    bound = make_bound(handler)
    saved = fn.__globals__.get("_FILE_KINDS")
    fn.__globals__["_FILE_KINDS"] = frozenset({"file_voice", "file_music"})
    try:
        hits = reachable_write_hits(fn, "prompt.save_master")
        assert any(hit.sketch == "templates/secret.txt" for hit in hits)
        kept = reachable_write_hits(bound, "prompt.save_master")
        assert not any(hit.sketch == "templates/secret.txt" for hit in kept)
    finally:
        if saved is None:
            fn.__globals__.pop("_FILE_KINDS", None)
        else:
            fn.__globals__["_FILE_KINDS"] = saved


def test_missing_file_kinds_does_not_prune() -> None:
    def executor(h):
        if h.kind in _FILE_KINDS:
            open("templates/secret.txt", "w")

    assert _file_kinds_of(executor) is None
    source = inspect.getsource(executor)
    assert _open_targets(source, file_kind="master_prompt", file_kinds=None) == [
        "templates/secret.txt"
    ]


def test_getattr_and_importlib_are_denied() -> None:
    source = (
        "import importlib\n"
        "def save(os, p):\n"
        "    getattr(os, 'remove')(p)\n"
        "    importlib.import_module('os').remove(p)\n"
        "    __import__('os').remove(p)\n"
    )
    hits = analyze_module_source(source, "prompt.save_master")
    assert [hit.kind for hit in hits] == ["getattr", "importlib.import_module", "__import__"]
    assert all(hit.allowed is False for hit in hits)


def test_os_shutil_pathlib_aliases_are_denied() -> None:
    source = (
        "import os.path\n"
        "from shutil import rmtree as r\n"
        "def save(p):\n"
        "    os.remove(p)\n"
        "    f = os.remove\n"
        "    f(p)\n"
        "    r('data/reports/old')\n"
        "    m = Path.unlink\n"
        "    m(p)\n"
    )
    hits = analyze_module_source(source, "prompt.save_master")
    assert [hit.kind for hit in hits] == [
        "os.remove",
        "alias:os.remove",
        "shutil.rmtree",
        "alias:Path.unlink",
    ]
    assert all(hit.allowed is False for hit in hits)


def test_replace_rename_truncate_are_denied() -> None:
    source = (
        "import os\n"
        "from pathlib import Path as P\n"
        "def save(f, text):\n"
        "    os.replace('data/reports/a', 'data/reports/b')\n"
        "    os.rename('data/reports/a', 'data/reports/b')\n"
        "    os.truncate('data/reports/a', 0)\n"
        "    P('x').replace('data/reports/y')\n"
        "    Path.replace('data/reports/a', 'data/reports/b')\n"
        "    Path('data/reports/a').rename('data/reports/b')\n"
        "    f.truncate()\n"
        "    open('templates/secret.txt', 'w')\n"
        "    open('data/reports/ok.txt', 'w')\n"
        "    text.replace('a', 'b')\n"
    )
    hits = analyze_module_source(source, "history.rollback")
    assert [hit.kind for hit in hits] == [
        "os.replace",
        "os.rename",
        "os.truncate",
        "Path.replace",
        "Path.replace",
        "Path.rename",
        "Path.truncate",
        "open",
        "open",
    ]
    assert [hit.allowed for hit in hits] == [
        False, False, False, False, False, False, False, False, True,
    ]


def test_sql_delete_drop_truncate_are_denied() -> None:
    source = (
        "def save(conn, session):\n"
        "    conn.execute('DELETE FROM scenes')\n"
        "    conn.executescript('DROP TABLE frames')\n"
        "    session.execute(text('TRUNCATE snapshots'))\n"
        "    conn.execute('SELECT 1')\n"
    )
    hits = analyze_module_source(source, "prompt.save_master")
    assert [hit.kind for hit in hits] == ["sql", "sql", "sql", "sql"]
    assert all(hit.allowed is False for hit in hits)


def test_http_delete_is_denied() -> None:
    source = (
        "def save(ctx, requests):\n"
        "    ctx.call('DELETE', '/projects/1/scenes/1')\n"
        "    ctx.call(method='DELETE', url='/x')\n"
        "    requests.request('DELETE', '/y')\n"
        "    ctx.call('GET', '/projects/1')\n"
    )
    hits = analyze_module_source(source, "group.delete")
    assert [hit.kind for hit in hits] == ["http.delete", "http.delete", "http.delete"]
    assert all(hit.allowed is False for hit in hits)


def test_whitelist_has_no_code_exec_tools() -> None:
    assert sys.version_info >= _MIN_PY, (
        "zip(strict=True) требует Python 3.10+; CI этого репозитория — 3.12"
    )
    header = load_audit_header()
    modify_ids = modify_ids_from_audit()
    assert len(modify_ids) == header["modify"]
    assert header["fs_breach"] == (
        "group.delete",
        "montage.delete_frame",
        "node.clear_check_agent",
        "prompt.delete_master",
        "storage.clear_files",
    )
    assert set(header["fs_breach"]) <= set(modify_ids)
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
    breach_ids = set(header["fs_breach"])
    found_breach: set[str] = set()
    for action_id, spec in zip(ids, specs, strict=True):
        fn = _executor(spec)
        if fn is None:
            continue
        if exec_calls_of(fn, label=action_id):
            offenders.append(action_id)
        hits = reachable_write_hits(fn, action_id)
        bad = [hit for hit in hits if not hit.allowed]
        if bad:
            found_breach.add(action_id)
            if action_id not in breach_ids:
                unexpected.append(action_id + ": " + "; ".join(str(hit) for hit in bad))
        elif action_id in breach_ids:
            unexpected.append(action_id + " в fs_breach, но детектор ничего не запретил")
        if action_id in header["snapshot_allow"]:
            deletes = [hit for hit in hits if hit.snapshot_delete]
            if deletes:
                unexpected.append(
                    action_id + " удаляет снимок: " + "; ".join(str(hit) for hit in deletes)
                )
    if found_breach != breach_ids:
        unexpected.append(
            "fs_breach не совпал с детектором: "
            + ", ".join(sorted(found_breach ^ breach_ids))
        )
    assert not offenders, (
        "исполнители выглядят как обёртки subprocess/eval/exec/compile: "
        + ", ".join(sorted(offenders))
    )
    assert not unexpected, (
        "запись в ФС вне allowlist либо удаление снимка:\n" + "\n".join(unexpected)
    )
