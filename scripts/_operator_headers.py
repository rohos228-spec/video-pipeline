"""Токен оператора для скриптов, которые сами запускают платные/управляющие POST на локальный сервер.

С W52 такие пути требуют заголовок ``X-VP-Operator`` и свой Origin. Токен выдаёт ``GET /api/auth/operator-token``.
"""
from __future__ import annotations


async def attach_operator_token(client, base: str) -> None:
    """Подставить Origin и X-VP-Operator в заголовки клиента по умолчанию (без вывода значения)."""
    from urllib.parse import urlsplit

    u = urlsplit(base)
    base = f"{u.scheme}://{u.netloc}"  # base может содержать префикс /api
    try:
        r = await client.get(f"{base}/api/auth/operator-token", headers={"Origin": base})
        token = (r.json() or {}).get("token") if r.status_code == 200 else None
    except Exception:  # noqa: BLE001 - без токена запрос просто получит 403, как и раньше
        token = None
    client.headers.update({"Origin": base})
    if token:
        client.headers.update({"X-VP-Operator": str(token)})
