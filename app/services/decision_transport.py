"""Scoped transport accounting hook, not a new model client or executor."""
from contextvars import ContextVar

current=ContextVar('decision_http_context',default=None)


async def fact(kind,payload=None,*,body=None,request_id=None):
    hook=current.get()
    if hook is not None:await hook.fact(kind,payload or {},body=body,request_id=request_id)

