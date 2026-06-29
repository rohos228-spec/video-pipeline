"""HTTP client for fleet agent endpoints."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

from app.fleet.transfer_state import (
    FleetTransferCancelled,
    check_transfer_cancelled,
    is_transfer_cancelled,
    parse_project_id_from_label,
)


class FleetAgentError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"agent HTTP {status}: {detail[:200]}")


def _log_transfer_progress(
    label: str,
    sent: int,
    total: int,
    *,
    state: dict[str, int | float],
    direction: str = "transfer",
    project_id: int | None = None,
    phase: str = "",
    target: str = "",
) -> None:
    if total <= 0:
        return
    pct = min(100, sent * 100 // total)
    now = time.monotonic()
    sent_mb = sent / (1024 * 1024)
    total_mb = total / (1024 * 1024)
    last_pct = int(state.get("last_pct", -1))
    last_ts = float(state.get("last_ts", 0.0))
    if pct >= 100:
        if state.get("logged_100"):
            return
        state["logged_100"] = 1
    elif not (pct >= last_pct + 5 or now - last_ts >= 30):
        return
    else:
        state["last_pct"] = pct - (pct % 5)
        state["last_ts"] = now

    logger.info(
        "{} {} {}% ({:.0f}/{:.0f} MB)",
        label,
        direction,
        pct,
        sent_mb,
        total_mb,
    )
    if pct >= 100:
        state["last_pct"] = 100
        state["last_ts"] = now

    pid = project_id
    if pid is None:
        pid = parse_project_id_from_label(label)
    if pid is not None and is_transfer_cancelled(pid):
        return
    if pid is not None:
        from app.fleet.transfer_state import emit_fleet_transfer_sync

        dir_map = {
            "upload": "to_hub",
            "send": "to_hub",
            "download": "from_agent",
            "receive": "to_hub",
        }
        emit_fleet_transfer_sync(
            pid,
            phase=phase or direction,
            direction=dir_map.get(direction, direction),
            percent=pct,
            sent_mb=sent_mb,
            total_mb=total_mb,
            message=f"{direction} {pct}% ({sent_mb:.0f}/{total_mb:.0f} MB)",
            target=target,
            status="active",
        )


def _headers(token: str) -> dict[str, str]:
    h: dict[str, str] = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _format_agent_error(status: int, text: str) -> str:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            detail = data.get("detail")
            if isinstance(detail, str):
                return detail
            if detail is not None:
                return json.dumps(detail, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    return (text or f"HTTP {status}")[:500]


def _parse_response_json(text: str) -> Any:
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FleetAgentError(
            502,
            f"invalid JSON from agent: {exc}; body={text[:200]!r}",
        ) from exc


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
                raise FleetAgentError(resp.status, _format_agent_error(resp.status, text))
            return _parse_response_json(text)


async def agent_get_bytes(
    base_url: str,
    token: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    timeout_sec: int = 3600,
) -> bytes:
    url = base_url.rstrip("/") + path
    timeout = aiohttp.ClientTimeout(total=timeout_sec, sock_read=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=_headers(token), params=params) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise FleetAgentError(resp.status, _format_agent_error(resp.status, text))
            return await resp.read()


async def agent_download_to_file(
    base_url: str,
    token: str,
    path: str,
    dest: Path,
    *,
    params: dict[str, str] | None = None,
    timeout_sec: int = 3600,
    progress_label: str = "",
    project_id: int | None = None,
) -> int:
    """Stream agent file to disk (large fleet bundles). Returns byte count."""
    url = base_url.rstrip("/") + path
    timeout = aiohttp.ClientTimeout(total=timeout_sec, sock_read=timeout_sec)
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    prog: dict[str, int | float] = {"last_pct": -1, "last_ts": 0.0}
    label = progress_label or "fleet download"
    pid = project_id or parse_project_id_from_label(label)
    if pid is not None:
        check_transfer_cancelled(pid)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=_headers(token), params=params) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise FleetAgentError(resp.status, _format_agent_error(resp.status, text))
            expected = int(resp.content_length or 0)
            if expected:
                logger.info("{} download START ({:.0f} MB expected)", label, expected / (1024 * 1024))
            with dest.open("wb") as out:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    if pid is not None:
                        check_transfer_cancelled(pid)
                    out.write(chunk)
                    total += len(chunk)
                    _log_transfer_progress(
                        label,
                        total,
                        expected or total,
                        state=prog,
                        direction="download",
                        phase="download",
                        project_id=pid,
                    )
    logger.info("{} download DONE ({:.0f} MB)", label, total / (1024 * 1024))
    return total


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
                raise FleetAgentError(resp.status, _format_agent_error(resp.status, text))
            return _parse_response_json(text)


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
                raise FleetAgentError(resp.status, _format_agent_error(resp.status, text))
            return _parse_response_json(text)


async def agent_upload_file(
    base_url: str,
    token: str,
    path: str,
    *,
    file_bytes: bytes,
    filename: str,
    timeout_sec: int = 3600,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    form = aiohttp.FormData()
    form.add_field("file", file_bytes, filename=filename)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=_headers(token), data=form) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise FleetAgentError(resp.status, _format_agent_error(resp.status, text))
            return _parse_response_json(text)


async def agent_upload_file_path(
    base_url: str,
    token: str,
    path: str,
    file_path: Path,
    *,
    filename: str | None = None,
    timeout_sec: int = 7200,
    extra_form: dict[str, str] | None = None,
    progress_label: str = "",
    project_id: int | None = None,
) -> dict[str, Any]:
    """Stream large file to hub (fleet bundle push)."""
    url = base_url.rstrip("/") + path
    timeout = aiohttp.ClientTimeout(total=timeout_sec, sock_read=timeout_sec)
    fname = filename or file_path.name
    total = file_path.stat().st_size
    label = progress_label or "fleet upload"
    pid = project_id or parse_project_id_from_label(label)
    if pid is not None:
        check_transfer_cancelled(pid)
    logger.info("{} upload START ({:.0f} MB → {})", label, total / (1024 * 1024), base_url)

    class _ProgressFile:
        def __init__(self) -> None:
            self._fh = file_path.open("rb")
            self.sent = 0
            self.state: dict[str, int | float] = {"last_pct": -1, "last_ts": 0.0}

        def read(self, n: int = -1) -> bytes:
            if pid is not None:
                check_transfer_cancelled(pid)
            chunk = self._fh.read(8 * 1024 * 1024 if n < 0 else n)
            if chunk:
                self.sent += len(chunk)
                _log_transfer_progress(
                    label,
                    self.sent,
                    total,
                    state=self.state,
                    direction="upload",
                    phase="upload",
                    target=base_url,
                    project_id=pid,
                )
            return chunk

        def close(self) -> None:
            self._fh.close()

    pf = _ProgressFile()
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            form = aiohttp.FormData()
            form.add_field(
                "file",
                pf,
                filename=fname,
                content_type="application/gzip",
            )
            for key, val in (extra_form or {}).items():
                form.add_field(key, val)
            async with session.post(url, headers=_headers(token), data=form) as resp:
                if pid is not None:
                    check_transfer_cancelled(pid)
                text = await resp.text()
                if resp.status >= 400:
                    raise FleetAgentError(resp.status, _format_agent_error(resp.status, text))
                logger.info("{} upload DONE", label)
                return _parse_response_json(text)
    finally:
        pf.close()


async def ping_agent(base_url: str, token: str) -> dict[str, Any]:
    return await agent_get(base_url, token, "/api/fleet/local/info", timeout_sec=15)
