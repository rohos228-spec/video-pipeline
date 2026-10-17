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
import textwrap
from collections.abc import Callable, Iterable

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
