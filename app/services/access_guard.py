"""Доступ к изменяющим ручкам, когда веб-авторизация выключена (умолчание).

``local_only_when_no_auth`` — одна переиспользуемая зависимость FastAPI:
  * веб-авторизация включена -> ничего не меняет (работает AuthDep);
  * выключена -> запрос принимается только с loopback (127.0.0.1 / ::1),
    если не задан ORCHESTRATOR_ALLOW_REMOTE_WITHOUT_AUTH=true.
"""
from __future__ import annotations

import ipaddress
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from loguru import logger

from app.settings import settings

# Имя хоста, которое подставляет тестовый клиент Starlette (сетевого пира нет).
_IN_PROCESS_HOSTS = {"testclient"}


def is_loopback_host(host: str | None) -> bool:
    """None (нет сетевого пира: in-process/unix-сокет) считается локальным."""
    if host is None:
        return True
    h = str(host).strip().lower()
    if h in _IN_PROCESS_HOSTS or h == "localhost":
        return True
    if h.startswith("::ffff:"):
        h = h[7:]
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def is_loopback_bind(host: str | None) -> bool:
    return is_loopback_host((host or "").strip() or "127.0.0.1")


async def local_only_when_no_auth(request: Request) -> None:
    if settings.web_auth_enabled or settings.orchestrator_allow_remote_without_auth:
        return
    client = request.client
    if not is_loopback_host(client.host if client else None):
        raise HTTPException(status_code=403, detail={
            "code": "local_only",
            "message": ("Веб-авторизация выключена: изменяющие запросы принимаются только "
                        "с localhost. Включите WEB_AUTH_USER/WEB_AUTH_PASSWORD или "
                        "ORCHESTRATOR_ALLOW_REMOTE_WITHOUT_AUTH=true.")})


LocalOnlyDep = Depends(local_only_when_no_auth)
LocalOnly = Annotated[None, Depends(local_only_when_no_auth)]


def warn_if_exposed_without_auth() -> bool:
    """Предупреждение при старте: авторизации нет, а сервер слушает не loopback."""
    if settings.web_auth_enabled or is_loopback_bind(settings.web_host):
        return False
    logger.warning(
        "SECURITY: web auth is OFF and WEB_HOST={} is not loopback; CORS allows any origin. "
        "Set WEB_AUTH_USER/WEB_AUTH_PASSWORD or bind to 127.0.0.1 (see docs/SECURITY_DEFAULTS.md).",
        settings.web_host)
    return True
