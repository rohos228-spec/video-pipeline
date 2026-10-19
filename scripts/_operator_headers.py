from __future__ import annotations


async def attach_operator_token(client, base: str) -> None:
    from urllib.parse import urlsplit

    u = urlsplit(base)
    base = f"{u.scheme}://{u.netloc}"
    try:
        r = await client.get(f"{base}/api/auth/operator-token", headers={"Origin": base})
        token = (r.json() or {}).get("token") if r.status_code == 200 else None
    except Exception:
        token = None
    client.headers.update({"Origin": base})
    if token:
        client.headers.update({"X-VP-Operator": str(token)})
