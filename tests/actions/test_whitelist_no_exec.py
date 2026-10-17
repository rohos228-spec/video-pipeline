"""Заготовка приёмки R0b: в реестре Action Layer нет правки кода и исполнения.

План: docs/fable/PLAN.md §2.2.5 и §6; таблица — docs/fable/actions_audit.md.

Пока реестр не влит в main (R0a без тега pipeline-base-v1, код #342 в main не переносился),
проверка реестра пропускается. Детектор exec гоняется на локальных заглушках и после
появления реестра применяется к каждому действию без смены правил.

Имена, которых не должно быть в реестре: edit_files, run_tests, exec, shell, write_file.
Обёртка subprocess — вызов subprocess.* / os.system / eval / exec / compile в исходнике
исполнителя (и во вложенных callable из замыкания).
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
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
    }
)

# Куда смотреть после R0b. Сначала будущий модуль WS2, затем реестр PR #342.
_REGISTRY_MODULES = (
    "app.orchestrator.actions",
    "app.services.action_layer.spec",
)

_EXEC_NAMES = frozenset({"eval", "exec", "compile"})
_SUBPROCESS_ATTRS = frozenset(
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


def _load_registry_module():
    errors: list[str] = []
    for name in _REGISTRY_MODULES:
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            errors.append(f"{name}: {exc}")
    pytest.skip(
        "Реестр Action Layer в main ещё отсутствует "
        "(ожидается после merge R0b, PR #342 orchestrator-observe-v1, "
        "sha 3ec7c957). До этого код оркестратора в main не переносится. "
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


def _is_exec_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id in _EXEC_NAMES:
        return True
    if not isinstance(func, ast.Attribute):
        return False
    if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
        return True
    if (
        func.attr == "system"
        and isinstance(func.value, ast.Name)
        and func.value.id == "os"
    ):
        return True
    if func.attr in _SUBPROCESS_ATTRS and isinstance(func.value, ast.Name):
        if func.value.id in {"subprocess", "sp"}:
            return True
    return False


def exec_calls_in_source(source: str) -> list[str]:
    """Имена запрещённых вызовов в исходнике одного исполняемого фрагмента."""
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return []
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_exec_call(node):
            found.append(ast.dump(node.func))
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


def exec_calls_of(fn: Callable, *, seen: set[int] | None = None) -> list[str]:
    """Запрещённые вызовы в fn и в callable из его замыкания (обёртки history)."""
    seen = seen if seen is not None else set()
    ident = id(fn)
    if ident in seen:
        return []
    seen.add(ident)
    found: list[str] = []
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError):
        source = ""
    if source:
        found.extend(exec_calls_in_source(source))
    for inner in _closure_callables(fn):
        found.extend(exec_calls_of(inner, seen=seen))
    return found


def test_exec_detector_flags_subprocess_eval_and_os_system() -> None:
    source = (
        "import subprocess, os\n"
        "def sample():\n"
        "    subprocess.run(['echo'])\n"
        "    subprocess.Popen(['echo'])\n"
        "    os.system('echo')\n"
        "    eval('1')\n"
        "    exec('x=1')\n"
        "    compile('x=1', '<s>', 'exec')\n"
    )
    found = exec_calls_in_source(source)
    assert len(found) == 6


def test_exec_detector_ignores_internal_api_call() -> None:
    source = (
        "async def sample(ctx, params):\n"
        "    return await ctx.call('GET', '/projects/1')\n"
    )
    assert exec_calls_in_source(source) == []


def test_exec_detector_sees_wrapper_closure() -> None:
    def orig():
        return None

    def backup():
        import subprocess

        subprocess.check_output(["echo"])

    def wrapper():
        backup()
        return orig()

    # Замыкание через default-arg не создаётся; собираем настоящее.
    def make(inner):
        def wrapped():
            return inner()

        return wrapped

    wrapped = make(backup)
    found = exec_calls_of(wrapped)
    assert found, "обёртка subprocess в замыкании должна быть видна"


_VERB = re.compile(r"delete|remove|clear|overwrite|unlink|rmtree", re.IGNORECASE)
_TOKEN = re.compile(
    r"^[A-Za-z0-9_-]*(?:delete|remove|clear|overwrite)[A-Za-z0-9_-]*$",
    re.IGNORECASE,
)
_ROUTERS = Path(__file__).resolve().parents[2] / "app" / "web" / "routers"
_ROUTE_INDEX: list[tuple[str, ast.AST]] | None = None


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _string_values(node: ast.AST) -> list[str]:
    """Строковые значения вызова. Ключи словаря не считаются вызовом."""
    found: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        found.append(node.value)
    elif isinstance(node, ast.JoinedStr):
        for part in node.values:
            found.extend(_string_values(part))
    elif isinstance(node, ast.Dict):
        for value in node.values:
            found.extend(_string_values(value))
    elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for item in node.elts:
            found.extend(_string_values(item))
    return found


def _static_suffix(node: ast.AST) -> str:
    parts: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        parts.append(node.value)
    elif isinstance(node, ast.JoinedStr):
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                parts.append(part.value)
            else:
                parts.append("{}")
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts.append(_static_suffix(node.left))
        parts.append(_static_suffix(node.right))
    text = "".join(parts)
    text = re.sub(r"/\{}+", "/", text)
    return text


def _api_path_node(node: ast.Call) -> ast.AST | None:
    name = _call_name(node.func)
    if name == "call":
        if len(node.args) >= 2:
            return node.args[1]
        for kw in node.keywords:
            if kw.arg == "path":
                return kw.value
    if name == "_post_file" and len(node.args) >= 3:
        return node.args[2]
    return None


def _method_of(node: ast.Call) -> str:
    if _call_name(node.func) != "call" or not node.args:
        return ""
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.upper()
    return ""


def _call_strings(node: ast.Call) -> list[str]:
    found: list[str] = []
    for arg in node.args:
        found.extend(_string_values(arg))
    for kw in node.keywords:
        found.extend(_string_values(kw.value))
    return found


def _direct_delete_reason(node: ast.Call) -> str | None:
    if _api_path_node(node) is None and _call_name(node.func) != "call":
        return None
    if _method_of(node) == "DELETE":
        return "http.delete"
    path_node = _api_path_node(node)
    path = _static_suffix(path_node) if path_node is not None else ""
    if _VERB.search(path):
        return f"path:{path}"
    for value in _call_strings(node):
        if _TOKEN.fullmatch(value):
            return f"token:{value}"
    return None


def _segments(path: str) -> list[str]:
    out: list[str] = []
    for part in path.split("/"):
        if not part or part.startswith("{") or part == "{}":
            continue
        out.append(part)
    return out


def _route_matches(route: str, suffix: str) -> bool:
    route_segs = _segments(route)
    call_segs = _segments(suffix)
    if not route_segs or not call_segs:
        return False
    if len(call_segs) >= len(route_segs):
        return call_segs[-len(route_segs):] == route_segs
    return route_segs[-len(call_segs):] == call_segs


def _route_index() -> list[tuple[str, str, ast.AST]]:
    global _ROUTE_INDEX
    if _ROUTE_INDEX is not None:
        return _ROUTE_INDEX
    rows: list[tuple[str, str, ast.AST]] = []
    for path in sorted(_ROUTERS.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call) or not deco.args:
                    continue
                method = _call_name(deco.func).upper()
                if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    continue
                arg = deco.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    rows.append((method, arg.value, node))
    _ROUTE_INDEX = rows
    return rows


_HANDLER_VERB = re.compile(
    r"delete|remove|overwrite|unlink|rmtree|clear_(?:file|dir|data|output|storage|frame)",
    re.IGNORECASE,
)


def _handler_deletes(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _HANDLER_VERB.search(_call_name(node.func)):
            return True
    return False


def _follow_reason(node: ast.Call) -> str | None:
    path_node = _api_path_node(node)
    if path_node is None:
        return None
    suffix = _static_suffix(path_node)
    call_segs = _segments(suffix)
    # Один общий сегмент (coverage, projects) совпадает со многими маршрутами.
    if len(call_segs) < 2:
        return None
    method = _method_of(node) or "POST"
    matched: list[tuple[str, ast.AST]] = []
    best = 0
    for route_method, route, handler in _route_index():
        if route_method != method or not _route_matches(route, suffix):
            continue
        score = len(_segments(route))
        if score > best:
            matched = [(route, handler)]
            best = score
        elif score == best:
            matched.append((route, handler))
    if not matched or not all(_handler_deletes(handler) for _route, handler in matched):
        return None
    route, _handler = matched[0]
    return f"handler:{method} {route}"


def studio_delete_reasons_in_source(source: str) -> list[str]:
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return []
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        direct = _direct_delete_reason(node)
        if direct:
            found.append(direct)
            continue
        followed = _follow_reason(node)
        if followed:
            found.append(followed)
    return found


def _same_module_helpers(fn: Callable) -> list[Callable]:
    try:
        source = inspect.getsource(fn)
        tree = ast.parse(textwrap.dedent(source))
    except (OSError, TypeError, SyntaxError):
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.append(node.func.id)
    globs = getattr(fn, "__globals__", {})
    module = getattr(fn, "__module__", None)
    found: list[Callable] = []
    for name in names:
        target = globs.get(name)
        if (
            inspect.isfunction(target)
            and getattr(target, "__module__", None) == module
            and target is not fn
        ):
            found.append(target)
    return found


def studio_delete_reasons_of(fn: Callable, *, seen: set[int] | None = None, depth: int = 0) -> list[str]:
    seen = seen if seen is not None else set()
    ident = id(fn)
    if ident in seen:
        return []
    seen.add(ident)
    found: list[str] = []
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError):
        source = ""
    if source:
        found.extend(studio_delete_reasons_in_source(source))
    for inner in _closure_callables(fn):
        found.extend(studio_delete_reasons_of(inner, seen=seen, depth=depth))
    if depth < 1:
        for helper in _same_module_helpers(fn):
            found.extend(studio_delete_reasons_of(helper, seen=seen, depth=depth + 1))
    return found


def test_studio_delete_detector_sees_http_delete_and_not_dict_key() -> None:
    hit = studio_delete_reasons_in_source(
        "async def sample(ctx, params):\n"
        "    return await ctx.call('DELETE', f'/projects/{params[\"project_id\"]}/frames/{params[\"frame_id\"]}')\n"
    )
    assert hit
    quiet = studio_delete_reasons_in_source(
        "async def sample(ctx, params):\n"
        "    return {'deleted': 1, 'number': params['frame_id']}\n"
    )
    assert quiet == []
    child = studio_delete_reasons_in_source(
        "async def sample(ctx, params):\n"
        "    return await ctx.call('POST', f'/p/{params[\"project_id\"]}/coverage',\n"
        "                          json={'type': 'coverage_delete', 'frame_number': 1})\n"
    )
    assert any("coverage_delete" in item for item in child)


def test_executor_calling_studio_delete_is_not_keep() -> None:
    from app.services.action_layer.verdict import verdict_of

    module = _load_registry_module()
    specs = _specs(module)
    keep_but_deletes: list[str] = []
    for spec in specs:
        action_id = _action_id(spec)
        fn = _executor(spec)
        if fn is None or not studio_delete_reasons_of(fn):
            continue
        if verdict_of(action_id) == "keep":
            keep_but_deletes.append(action_id)
    assert not keep_but_deletes, (
        "исполнитель зовёт удаление/перезапись студии, вердикт не может быть keep: "
        + ", ".join(sorted(keep_but_deletes))
    )
    assert verdict_of("group.list") == "keep"
    assert verdict_of("montage.cancel_queue_ops") == "keep"
    assert verdict_of("frame.restore") == "keep"


def test_whitelist_has_no_code_exec_tools() -> None:
    module = _load_registry_module()
    specs = _specs(module)
    ids = [_action_id(spec) for spec in specs]
    forbidden_hits = sorted(
        action_id
        for action_id in ids
        if action_id in FORBIDDEN_IDS
        or action_id.split(".")[-1] in FORBIDDEN_IDS
    )
    assert not forbidden_hits, (
        "в реестре есть запрещённые инструменты правки кода/исполнения: "
        + ", ".join(forbidden_hits)
    )
    offenders: list[str] = []
    for spec, action_id in zip(specs, ids, strict=True):
        fn = _executor(spec)
        if fn is None:
            continue
        if exec_calls_of(fn):
            offenders.append(action_id)
    assert not offenders, (
        "исполнители выглядят как обёртки subprocess/eval/exec/compile: "
        + ", ".join(sorted(offenders))
    )
