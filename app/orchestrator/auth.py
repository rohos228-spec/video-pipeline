"""Доступ к ``/api/orchestrator/**``.

Bearer-токен из окружения ``ORCH_API_TOKEN``, сравнение constant-time.
Нет токена или неверный токен — 401. Значение токена никуда не пишется.
"""

from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import Header, HTTPException

_PREFIX = "/api/orchestrator"


def is_orchestrator_path(path: str) -> bool:
    """``/api/orchestrator`` и всё под ним, включая путь без завершающего слэша."""
    if path == _PREFIX:
        return True
    return path.startswith(_PREFIX + "/")


def expected_token() -> str:
    return os.environ.get("ORCH_API_TOKEN", "")


def bearer_ok(authorization: str | None) -> bool:
    if not authorization or not authorization.startswith("Bearer "):
        return False
    presented = authorization.removeprefix("Bearer ").strip()
    expected = expected_token()
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


async def require_orch_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Зависимость роутера: закрывает эндпоинты, даже если middleware не повесили."""
    if not bearer_ok(authorization):
        raise HTTPException(status_code=401, detail="unauthorized")


class OrchestratorAuthMiddleware:
    """Чистый ASGI-middleware (не BaseHTTPMiddleware — тот буферизует SSE)."""

    def __init__(self, app: object) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        path = scope.get("path") or ""
        # Websocket под /api/orchestrator/** не обходит Bearer: эндпоинтов нет,
        # но открытый upgrade был бы дырой.
        if scope.get("type") == "websocket" and is_orchestrator_path(path):
            await send(  # type: ignore[misc]
                {
                    "type": "websocket.http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(  # type: ignore[misc]
                {"type": "websocket.http.response.body", "body": b'{"detail":"unauthorized"}'}
            )
            return
        if scope.get("type") != "http":
            await self.app(scope, receive, send)  # type: ignore[misc]
            return
        if not is_orchestrator_path(path) or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)  # type: ignore[misc]
            return
        raw: bytes | None = None
        for key, value in scope.get("headers") or ():
            if key.lower() == b"authorization":
                raw = value
                break
        try:
            header = raw.decode("utf-8") if raw is not None else None
        except UnicodeDecodeError:
            header = None
        if not bearer_ok(header):
            body = b'{"detail":"unauthorized"}'
            await send(  # type: ignore[misc]
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})  # type: ignore[misc]
            return
        await self.app(scope, receive, send)  # type: ignore[misc]
