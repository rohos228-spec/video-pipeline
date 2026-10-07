"""Сквозная корреляция записей: какой запрос, какой запуск, какой проект (шаг B02 плана регистратора).

Один крошечный модуль без зависимостей от остального приложения (его читают middleware аудита W2-2,
журнал трат, трекер вызовов и слой действий). Значения лежат в ``contextvars``: они переживают
``await`` и копируются в созданные задачи, поэтому фоновая запись траты, поставленная из запроса,
видит тот же ``request_id``, что и сам запрос.

* ``request_id``: один HTTP-запрос оператора (ставит middleware) либо один такт воркера/одна операция
  (ставит ``ensure_request_scope`` в точке входа, если запроса нет).
* ``run_id``: запуск, которому принадлежат траты: ``execution_id`` управляемого запуска,
  ``plan:<id>`` плана слоя действий либо ``native:<проект>:<такт>`` для ручного нативного контура.
* ``project_id``: проект; читается вместе со старым ``observation._cost_project`` (его ставят
  ``project_scope`` и ``advance_runner``), чтобы не было двух разных источников правды.

Модуль ничего не пишет в базу и не бросает исключений наружу.
"""
from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from typing import Iterator

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_request_id", default=None)
_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_run_id", default=None)
_project_id: contextvars.ContextVar[int | None] = contextvars.ContextVar("correlation_project_id", default=None)

REQUEST_ID_MAX = 64
RUN_ID_MAX = 80


def new_request_id(prefix: str = "") -> str:
    """Новый короткий идентификатор запроса (без префикса: 16 hex)."""
    raw = uuid.uuid4().hex[:16]
    return f"{prefix}{raw}"[:REQUEST_ID_MAX]


def get_request_id() -> str:
    """Текущий ``request_id`` или пустая строка (никогда ``None``)."""
    return _request_id.get() or ""


def get_run_id() -> str:
    return _run_id.get() or ""


def get_project_id() -> int | None:
    """Проект текущего контекста: ``observation._cost_project`` (старый путь), иначе собственная переменная."""
    try:
        from app.services.observation import _cost_project
        value = _cost_project.get()
        if value is not None:
            return int(value)
    except Exception:  # noqa: BLE001 - корреляция не должна ронять вызов
        pass
    value = _project_id.get()
    return int(value) if value is not None else None


def set_request_id(value: str | None) -> contextvars.Token:
    """Для middleware: ``token = set_request_id(rid)``; в конце ``reset_request_id(token)``."""
    return _request_id.set((str(value)[:REQUEST_ID_MAX]) if value else None)


def reset_request_id(token: contextvars.Token) -> None:
    try:
        _request_id.reset(token)
    except Exception:  # noqa: BLE001 - токен из другого контекста: значение просто остаётся
        pass


def correlation() -> dict:
    """Снимок ``{project_id, run_id, request_id}`` для записи в журнал."""
    return {"project_id": get_project_id(), "run_id": get_run_id(), "request_id": get_request_id()}


@contextmanager
def request_scope(request_id: str | None = None, *, run_id: str | None = None,
                  project_id: int | None = None) -> Iterator[dict]:
    """Установить корреляцию на время блока. Пустые аргументы оставляют внешнее значение."""
    tokens: list[tuple[contextvars.ContextVar, contextvars.Token]] = []
    cost_token = None
    cost_var = None
    if request_id:
        tokens.append((_request_id, _request_id.set(str(request_id)[:REQUEST_ID_MAX])))
    if run_id:
        tokens.append((_run_id, _run_id.set(str(run_id)[:RUN_ID_MAX])))
    if project_id is not None:
        tokens.append((_project_id, _project_id.set(int(project_id))))
        try:
            from app.services.observation import _cost_project as cost_var  # type: ignore[no-redef]
            cost_token = cost_var.set(int(project_id))
        except Exception:  # noqa: BLE001
            cost_var = None
    try:
        yield correlation()
    finally:
        if cost_var is not None and cost_token is not None:
            try:
                cost_var.reset(cost_token)
            except Exception:  # noqa: BLE001
                pass
        for var, token in reversed(tokens):
            try:
                var.reset(token)
            except Exception:  # noqa: BLE001
                pass


@contextmanager
def ensure_request_scope(prefix: str = "op-", *, run_id: str | None = None,
                         project_id: int | None = None) -> Iterator[dict]:
    """Как ``request_scope``, но если ``request_id`` ещё нет (воркер, очередь, фоновая операция), создаёт свой.

    Внутри HTTP-запроса, где middleware уже поставил ``request_id``, он сохраняется: трата привязана к
    запросу оператора, а не к внутренней операции."""
    rid = get_request_id() or new_request_id(prefix)
    with request_scope(rid, run_id=run_id, project_id=project_id) as snap:
        yield snap


def project_scope(project_id: int | None, *, run_id: str | None = None, prefix: str = "op-"):
    """Точка входа для регенераций, очереди генерации и нативного контура: проект, запуск, запрос."""
    return ensure_request_scope(prefix, run_id=run_id, project_id=project_id)


def new_run_id(kind: str, project_id: int | None, tick: str | None = None) -> str:
    """Запуск вне управляемого исполнения: ``<вид>:<проект>:<операция>`` (native, regen, queue)."""
    return f"{kind}:{project_id if project_id is not None else 0}:{tick or uuid.uuid4().hex[:8]}"[:RUN_ID_MAX]


def native_run_id(project_id: int, tick: str | None = None) -> str:
    """Запуск нативного (не управляемого) контура: ``native:<проект>:<такт>``."""
    return new_run_id("native", int(project_id), tick)


def set_project_id(project_id: int | None) -> contextvars.Token:
    """Для middleware аудита: проект из пути запроса (``reset`` токеном через ``reset_project_id``)."""
    return _project_id.set(int(project_id) if project_id is not None else None)


def reset_project_id(token: contextvars.Token) -> None:
    try:
        _project_id.reset(token)
    except Exception:  # noqa: BLE001
        pass


def scoped_async(kind: str, project_of):
    """Декоратор для async-функции, которая запускает платную работу над проектом (регенерация, очередь).

    ``project_of(*args, **kwargs) -> int | None``. Внутри: проект, запуск (уже связанный ``plan:``/исполнение
    сохраняется, иначе новый ``<kind>:<проект>:<операция>``) и запрос (оператора, иначе новый ``<kind>-...``).
    Это закрывает дыру B02: ``_cost_project`` ставился только в ``advance_runner``."""
    import functools

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                pid = project_of(*args, **kwargs)
                pid = int(pid) if pid is not None else None
            except Exception:  # noqa: BLE001 - без проекта строка всё равно получит запрос и запуск
                pid = None
            run = get_run_id() or new_run_id(kind, pid)
            with ensure_request_scope(f"{kind}-", run_id=run, project_id=pid):
                return await fn(*args, **kwargs)
        wrapper.__scoped_kind__ = kind
        return wrapper
    return deco


# -- bind / unbind: for code that cannot be wrapped in a ``with`` (reserve ... settle of the action layer) ----
# ``reserve`` and ``settle`` are two calls in the same task of a plan step: a value set inside an awaited
# coroutine stays visible to its caller and to tasks created afterwards, so ``bind_run`` in the first and
# ``unbind_run`` in the second bracket the paid work without touching the executor.

_auto_request: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_auto_request", default=None)


def bind_run(run_id: str, project_id: int | None = None, request_prefix: str = "run-") -> None:
    """Set run and project for the rest of this context; create a ``request_id`` if there is none."""
    _run_id.set(str(run_id)[:RUN_ID_MAX])
    if project_id is not None:
        _project_id.set(int(project_id))
    if not get_request_id():
        rid = new_request_id(request_prefix)
        _request_id.set(rid)
        _auto_request.set(rid)


def unbind_run(run_id: str | None = None) -> None:
    """Undo ``bind_run`` (only if the bound run is ``run_id`` when given). A request id that ``bind_run``
    created is dropped; one that came from the operator's request stays."""
    if run_id is not None and get_run_id() != str(run_id)[:RUN_ID_MAX]:
        return
    _run_id.set(None)
    _project_id.set(None)
    auto = _auto_request.get()
    if auto and _request_id.get() == auto:
        _request_id.set(None)
    _auto_request.set(None)
