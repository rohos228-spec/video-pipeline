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
    raw = uuid.uuid4().hex[:16]
    return f"{prefix}{raw}"[:REQUEST_ID_MAX]


def get_request_id() -> str:
    return _request_id.get() or ""


def get_run_id() -> str:
    return _run_id.get() or ""


def get_project_id() -> int | None:
    try:
        from app.services.observation import _cost_project
        value = _cost_project.get()
        if value is not None:
            return int(value)
    except Exception:
        pass
    value = _project_id.get()
    return int(value) if value is not None else None


def set_request_id(value: str | None) -> contextvars.Token:
    return _request_id.set((str(value)[:REQUEST_ID_MAX]) if value else None)


def reset_request_id(token: contextvars.Token) -> None:
    try:
        _request_id.reset(token)
    except Exception:
        pass


def correlation() -> dict:
    return {"project_id": get_project_id(), "run_id": get_run_id(), "request_id": get_request_id()}


@contextmanager
def request_scope(request_id: str | None = None, *, run_id: str | None = None,
                  project_id: int | None = None) -> Iterator[dict]:
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
            from app.services.observation import _cost_project as cost_var
            cost_token = cost_var.set(int(project_id))
        except Exception:
            cost_var = None
    try:
        yield correlation()
    finally:
        if cost_var is not None and cost_token is not None:
            try:
                cost_var.reset(cost_token)
            except Exception:
                pass
        for var, token in reversed(tokens):
            try:
                var.reset(token)
            except Exception:
                pass


@contextmanager
def ensure_request_scope(prefix: str = "op-", *, run_id: str | None = None,
                         project_id: int | None = None) -> Iterator[dict]:
    rid = get_request_id() or new_request_id(prefix)
    with request_scope(rid, run_id=run_id, project_id=project_id) as snap:
        yield snap


def project_scope(project_id: int | None, *, run_id: str | None = None, prefix: str = "op-"):
    return ensure_request_scope(prefix, run_id=run_id, project_id=project_id)


def new_run_id(kind: str, project_id: int | None, tick: str | None = None) -> str:
    return f"{kind}:{project_id if project_id is not None else 0}:{tick or uuid.uuid4().hex[:8]}"[:RUN_ID_MAX]


def native_run_id(project_id: int, tick: str | None = None) -> str:
    return new_run_id("native", int(project_id), tick)


def set_project_id(project_id: int | None) -> contextvars.Token:
    return _project_id.set(int(project_id) if project_id is not None else None)


def reset_project_id(token: contextvars.Token) -> None:
    try:
        _project_id.reset(token)
    except Exception:
        pass


def scoped_async(kind: str, project_of):
    import functools

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                pid = project_of(*args, **kwargs)
                pid = int(pid) if pid is not None else None
            except Exception:
                pid = None
            run = get_run_id() or new_run_id(kind, pid)
            with ensure_request_scope(f"{kind}-", run_id=run, project_id=pid):
                return await fn(*args, **kwargs)
        wrapper.__scoped_kind__ = kind
        return wrapper
    return deco



_auto_request: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_auto_request", default=None)


def bind_run(run_id: str, project_id: int | None = None, request_prefix: str = "run-") -> None:
    _run_id.set(str(run_id)[:RUN_ID_MAX])
    if project_id is not None:
        _project_id.set(int(project_id))
    if not get_request_id():
        rid = new_request_id(request_prefix)
        _request_id.set(rid)
        _auto_request.set(rid)


def unbind_run(run_id: str | None = None) -> None:
    if run_id is not None and get_run_id() != str(run_id)[:RUN_ID_MAX]:
        return
    _run_id.set(None)
    _project_id.set(None)
    auto = _auto_request.get()
    if auto and _request_id.get() == auto:
        _request_id.set(None)
    _auto_request.set(None)
