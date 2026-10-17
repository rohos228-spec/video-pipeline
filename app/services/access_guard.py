from __future__ import annotations

import contextvars
import hmac
import ipaddress
import json
import os
import re
import secrets
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from loguru import logger

from app.settings import settings

_IN_PROCESS_HOSTS = {"testclient"}

OPERATOR_HEADER = "X-VP-Operator"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_INTERNAL_HOSTS = frozenset({"action-layer.invalid"})
_FORWARD_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host")

_OPERATOR_TOKEN: str | None = None
operator_token_verified: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "vp_operator_token_verified", default=False)



def is_loopback_host(host: str | None) -> bool:
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


def _has_forward_headers(headers) -> bool:
    return any(headers.get(h) for h in _FORWARD_HEADERS)


def _local_only_check(request: Request) -> None:
    if settings.web_auth_enabled or settings.orchestrator_allow_remote_without_auth:
        return
    client = request.client
    peer_local = is_loopback_host(client.host if client else None)
    proxied = _has_forward_headers(request.headers)
    if not peer_local or proxied:
        raise HTTPException(status_code=403, detail={
            "code": "local_only",
            "message": ("Веб-авторизация выключена: изменяющие запросы принимаются только "
                        "с localhost напрямую (не через обратный прокси). Включите "
                        "WEB_AUTH_USER/WEB_AUTH_PASSWORD или "
                        "ORCHESTRATOR_ALLOW_REMOTE_WITHOUT_AUTH=true.")})


async def local_only_when_no_auth(request: Request) -> None:
    _local_only_check(request)


async def local_only_for_mutations(request: Request) -> None:
    if request.method.upper() in SAFE_METHODS:
        return
    _local_only_check(request)


LocalOnlyDep = Depends(local_only_when_no_auth)
LocalOnly = Annotated[None, Depends(local_only_when_no_auth)]
MutationLocalOnlyDep = Depends(local_only_for_mutations)



def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name) or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def operator_token_enforced() -> bool:
    return (os.environ.get("VP_OPERATOR_TOKEN_ENFORCE") or "1").strip().lower() not in {
        "0", "false", "no", "off"}


def _norm_origin(origin: str) -> str | None:
    try:
        parts = urlsplit(origin.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def allowed_origins() -> list[str]:
    port = int(getattr(settings, "web_port", 8765) or 8765)
    out: list[str] = []
    for host in ("localhost", "127.0.0.1", "[::1]"):
        for p in (3000, port):
            out.append(f"http://{host}:{p}")
    web_host = (getattr(settings, "web_host", "") or "").strip()
    if web_host and web_host not in ("0.0.0.0", "::", "[::]") and not is_loopback_bind(web_host):
        out.append(f"http://{web_host}:{port}")
    for extra in _env_list("VP_ALLOWED_ORIGINS"):
        norm = _norm_origin(extra)
        if norm is None:
            logger.warning("VP_ALLOWED_ORIGINS: значение {!r} пропущено (нужен схема://хост[:порт])", extra)
            continue
        out.append(norm)
    seen: set[str] = set()
    return [o for o in out if not (o in seen or seen.add(o))]


def allowed_hostnames() -> set[str]:
    hosts = {"localhost"}
    web_host = (getattr(settings, "web_host", "") or "").strip().lower()
    if web_host and web_host not in ("0.0.0.0", "::", "[::]"):
        hosts.add(web_host.strip("[]"))
    hosts.update(h.lower().strip("[]") for h in _env_list("VP_ALLOWED_HOSTS"))
    return hosts


def _split_host_header(value: str) -> tuple[str, str]:
    v = (value or "").strip().lower()
    if v.startswith("["):
        end = v.find("]")
        if end == -1:
            return "", ""
        rest = v[end + 1:]
        return v[1:end], rest[1:] if rest.startswith(":") else ""
    if v.count(":") == 1:
        h, _, p = v.partition(":")
        return h, p
    return v, ""


def host_allowed(host_header: str | None) -> bool:
    name, _port = _split_host_header(host_header or "")
    if not name:
        return False
    if name in _INTERNAL_HOSTS or name in allowed_hostnames() or name.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    return "." not in name


def origin_allowed(origin: str | None, host_header: str | None) -> bool:
    if not origin or origin.strip().lower() == "null":
        return False
    norm = _norm_origin(origin)
    if norm is None:
        return False
    if norm in allowed_origins():
        return True
    parts = urlsplit(norm)
    host_name, host_port = _split_host_header(host_header or "")
    if not host_name or not host_allowed(host_header):
        return False
    origin_host = (parts.hostname or "").lower()
    origin_port = str(parts.port or (443 if parts.scheme == "https" else 80))
    return origin_host == host_name and origin_port == (host_port or origin_port)



def get_operator_token() -> str:
    global _OPERATOR_TOKEN
    if _OPERATOR_TOKEN is None:
        _OPERATOR_TOKEN = secrets.token_urlsafe(32)
    return _OPERATOR_TOKEN


def rotate_operator_token() -> str:
    global _OPERATOR_TOKEN
    _OPERATOR_TOKEN = secrets.token_urlsafe(32)
    return _OPERATOR_TOKEN


def check_operator_token(candidate: str | None) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), get_operator_token().encode("utf-8"))


_TOKEN_PATHS = [re.compile(p) for p in (
    r"^/action-layer/",
    r"^/change-control/",
    r"^/projects/[^/]+/observations/kill-switch",
    r"^/projects/[^/]+/media-recovery/",
    r"^/projects/[^/]+/stop$",
    r"^/projects/[^/]+/steps/[^/]+/run$",
    r"^/projects/[^/]+/(resume|continue)$",
    r"^/hitl/[^/]+/decision$",
    r"^/projects/[^/]+/mass-lanes/start$",
    r"^/runs/[^/]+/cancel$",
    r"^/projects/[^/]+/publications/",
    r".*publish.*",
    r"^/projects/[^/]+/finish/",
    r"^/projects/[^/]+/montage-board/(queue|montage|recover-outsee)$",
    r"^/projects/[^/]+/montage-board/frames/[^/]+/scene-(action-generate|generate-with-images|variants|improve)$",
    r"^/projects/[^/]+/observations/(launch|auto)?$",
    r"^/projects/[^/]+/observations/[^/]+/actions/(start|retry)$",
    r"^/outsee/generate$",
    r"^/kie-create/generate$",
    r"^/sidebar-layout/gen-queue/(enqueue|bulk-enqueue)$",
    r"^/runs/from-workflow/",
    r"^/prompt-studio/projects/[^/]+/gpt-verdict/[^/]+/run$",
    r"^/fleet/(local|nodes/[^/]+)/powershell",
    r"^/db/projects/[^/]+/orchestrator/",
)]


def path_needs_operator_token(path: str, method: str) -> bool:
    if method.upper() in SAFE_METHODS:
        return False
    p = path[4:] if path.startswith("/api/") else path
    return any(rx.match(p) for rx in _TOKEN_PATHS)



def _hdr(scope, name: bytes) -> str | None:
    for k, v in scope.get("headers") or ():
        if k == name:
            return v.decode("latin-1")
    return None


class SecurityGuardMiddleware:

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        path = scope.get("path", "")
        if kind not in ("http", "websocket") or not (path.startswith("/api") or path.startswith("/ws")):
            await self.app(scope, receive, send)
            return
        verdict = self._verdict(scope, path)
        if verdict is not None:
            await self._deny(scope, send, *verdict)
            return
        marker = operator_token_verified.set(check_operator_token(_hdr(scope, b"x-vp-operator")))
        try:
            await self.app(scope, receive, send)
        finally:
            operator_token_verified.reset(marker)

    @staticmethod
    def _verdict(scope, path: str) -> tuple[str, str] | None:
        method = (scope.get("method") or "GET").upper()
        host = _hdr(scope, b"host")
        if not host_allowed(host):
            return ("bad_host", "Запрос с чужим именем хоста отклонён (защита от DNS-rebinding). "
                    "Откройте Studio по адресу http://localhost:8765 или добавьте имя в VP_ALLOWED_HOSTS.")
        origin = _hdr(scope, b"origin")
        mutating = scope.get("type") == "websocket" or method not in SAFE_METHODS
        if origin is not None and mutating and not origin_allowed(origin, host):
            return ("bad_origin", "Запрос со страницы с чужим адресом (Origin) отклонён: "
                    "изменять данные можно только из своего Studio.")
        if (origin is None and mutating
                and (_hdr(scope, b"sec-fetch-site") or "").lower() == "cross-site"):
            return ("bad_origin", "Межсайтовый запрос без Origin отклонён.")
        if (scope.get("type") == "http" and operator_token_enforced()
                and path_needs_operator_token(path, method)
                and not check_operator_token(_hdr(scope, b"x-vp-operator"))):
            return ("operator_token_required",
                    "Нужен токен оператора: обновите страницу Studio и повторите действие.")
        return None

    @staticmethod
    async def _deny(scope, send, code: str, message: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": {"code": code, "message": message}},
                          ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": 403, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})



def warn_if_exposed_without_auth() -> bool:
    if settings.web_auth_enabled or is_loopback_bind(settings.web_host):
        return False
    logger.warning(
        "SECURITY: web auth is OFF and WEB_HOST={} is not loopback (allowed only because "
        "ORCHESTRATOR_ALLOW_REMOTE_WITHOUT_AUTH is set). "
        "Set WEB_AUTH_USER/WEB_AUTH_PASSWORD or bind to 127.0.0.1 (see docs/SECURITY_DEFAULTS.md).",
        settings.web_host)
    return True


class UnsafeBindError(RuntimeError):
    pass


def assert_safe_bind() -> None:
    if settings.web_auth_enabled or is_loopback_bind(settings.web_host):
        return
    if settings.orchestrator_allow_remote_without_auth:
        warn_if_exposed_without_auth()
        return
    raise UnsafeBindError(
        f"Отказ старта: WEB_HOST={settings.web_host!r} слушает не только этот компьютер, а вход "
        "выключен. Задайте WEB_AUTH_USER и WEB_AUTH_PASSWORD, либо верните WEB_HOST=127.0.0.1. "
        "Осознанное исключение: ORCHESTRATOR_ALLOW_REMOTE_WITHOUT_AUTH=true.")
