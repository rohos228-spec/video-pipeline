"""HTTP client for fleet agent endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiohttp


class FleetAgentError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"agent HTTP {status}: {detail[:200]}")


def _headers(token: str) -> dict[str, str]:
    h: dict[str, str] = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def agent_get(
    base_url: str,
    token: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    timeout_sec: int = 60,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=_headers(token), params=params) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise FleetAgentError(resp.status, text)
            return await resp.json()


async def agent_get_bytes(
    base_url: str,
    token: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    timeout_sec: int = 600,
) -> bytes:
    url = base_url.rstrip("/") + path
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=_headers(token), params=params) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise FleetAgentError(resp.status, text)
            return await resp.read()


async def agent_post(
    base_url: str,
    token: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout_sec: int = 120,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            url, headers=_headers(token), json=json_body or {}
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise FleetAgentError(resp.status, text)
            if not text.strip():
                return {}
            return await resp.json()


async def agent_delete(
    base_url: str,
    token: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    timeout_sec: int = 60,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.delete(
            url, headers=_headers(token), params=params
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise FleetAgentError(resp.status, text)
            if not text.strip():
                return {}
            return await resp.json()


async def agent_upload_file(
    base_url: str,
    token: str,
    path: str,
    *,
    file_bytes: bytes,
    filename: str,
    timeout_sec: int = 600,
    extra_form: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    form = aiohttp.FormData()
    form.add_field("file", file_bytes, filename=filename)
    for key, value in (extra_form or {}).items():
        form.add_field(key, value)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=_headers(token), data=form) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise FleetAgentError(resp.status, text)
            if not text.strip():
                return {}
            return await resp.json()


async def agent_upload_file_path(
    base_url: str,
    token: str,
    path: str,
    file_path: Path,
    *,
    filename: str,
    timeout_sec: int = 600,
    progress_label: str = "",
    project_id: int | None = None,
    extra_form: dict[str, str] | None = None,
) -> dict[str, Any]:
    from app.fleet.transfer_state import (
        check_transfer_cancelled,
        parse_project_id_from_label,
        update_fleet_transfer,
    )

    pid = project_id
    if pid is None and progress_label:
        pid = parse_project_id_from_label(progress_label)

    size_mb = file_path.stat().st_size / (1024 * 1024)
    if pid is not None:
        check_transfer_cancelled(pid)
        await update_fleet_transfer(
            pid,
            phase="upload",
            direction="to_hub",
            percent=0,
            sent_mb=0,
            total_mb=size_mb,
            message=f"Отправка {size_mb:.0f} MB на hub (это долго, не закрывай Studio)…",
            target=base_url.rstrip("/"),
        )

    with file_path.open("rb") as fh:
        file_bytes = fh.read()

    if pid is not None:
        check_transfer_cancelled(pid)
        await update_fleet_transfer(
            pid,
            phase="upload",
            direction="to_hub",
            percent=50,
            sent_mb=size_mb * 0.5,
            total_mb=size_mb,
            message=f"Отправка {size_mb:.0f} MB на hub (это долго, не закрывай Studio)…",
            target=base_url.rstrip("/"),
        )

    result = await agent_upload_file(
        base_url,
        token,
        path,
        file_bytes=file_bytes,
        filename=filename,
        timeout_sec=timeout_sec,
        extra_form=extra_form,
    )

    if pid is not None:
        await update_fleet_transfer(
            pid,
            phase="upload",
            direction="to_hub",
            percent=100,
            sent_mb=size_mb,
            total_mb=size_mb,
            message=f"Отправлено на hub ({size_mb:.0f} MB)",
            target=base_url.rstrip("/"),
        )
    return result


async def ping_agent(base_url: str, token: str) -> dict[str, Any]:
    return await agent_get(base_url, token, "/api/fleet/local/info", timeout_sec=15)
