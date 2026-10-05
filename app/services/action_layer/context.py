"""Контекст исполнения действия и вызов существующего backend внутри процесса.

Почему in-process ASGI, а не прямые функции сервисов: эндпоинты монтажа сами
открывают сессию проекта (``get_project_session``), валидируют параметры,
коммитят и публикуют события. Вызов через ASGI-клиент того же приложения
сохраняет всё это без копирования логики и без сетевого соединения
(``ASGITransport`` не открывает сокет). Тесты подменяют зависимости приложения,
и действия автоматически работают на подменённой БД.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

API_PREFIX = "/api"
_BASE_URL = "http://action-layer.invalid"


class ActionCallError(RuntimeError):
    """Backend ответил ошибкой (status >= 400) или вернул неожиданное."""

    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


class WriteNotAllowed(RuntimeError):
    """Попытка записи из контекста только для чтения (preview / precondition)."""


ProgressSink = Callable[[float | None, str], Awaitable[None]]


@dataclass
class ActionContext:
    app: Any
    operator: str = "system"
    plan_id: str | None = None
    step_index: int | None = None
    idempotency_key: str | None = None
    read_only: bool = False
    progress_sink: ProgressSink | None = None
    _client: httpx.AsyncClient | None = field(default=None, repr=False)

    async def call(self, method: str, path: str, *, params: dict | None = None,
                   json: Any = None, timeout: float = 120.0) -> Any:
        """Вызов эндпоинта приложения (path без /api). Возвращает JSON."""
        method = method.upper()
        if self.read_only and method != "GET":
            raise WriteNotAllowed(f"{method} {path} is not allowed in read-only context")
        headers = {"X-Action-Layer-Operator": self.operator}
        if self.idempotency_key:
            headers["Idempotency-Key"] = self.idempotency_key
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL,
                                     timeout=timeout) as client:
            resp = await client.request(method, API_PREFIX + path, params=params,
                                        json=json, headers=headers)
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        if resp.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else body
            raise ActionCallError(resp.status_code, detail)
        return body

    async def progress(self, fraction: float | None, note: str = "") -> None:
        if self.progress_sink is not None:
            await self.progress_sink(fraction, note)

    def readonly_copy(self) -> "ActionContext":
        return ActionContext(app=self.app, operator=self.operator, plan_id=self.plan_id,
                             step_index=self.step_index, read_only=True)
