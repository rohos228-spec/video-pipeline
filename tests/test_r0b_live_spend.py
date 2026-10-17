"""Живой путь call_opus: параллельные HTTP и один резерв на вызов."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import scene_boundaries as sb
from app.services import spend_guard
from app.settings import settings


def _quiet_gate(monkeypatch: pytest.MonkeyPatch, *, run_cap: float) -> None:
    from app.services import disk_guard, kill_switch

    async def guard_native(_sessions: object) -> None:
        return None

    monkeypatch.setattr(kill_switch, "guard_native", guard_native)
    monkeypatch.setattr(disk_guard, "check", lambda: SimpleNamespace(allowed=True))
    monkeypatch.setattr(
        spend_guard, "caps", lambda: {"daily": 0.0, "project": 0.0, "run": run_cap}
    )

    async def record_block(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(spend_guard, "_record_block", record_block)
    spend_guard.reset_inflight()


class _Stream:
    intervals: list[tuple[float, float]] = []
    seen: list[int] = []

    def __init__(self) -> None:
        self.status_code = 200

    async def __aenter__(self) -> _Stream:
        start = time.monotonic()
        await asyncio.sleep(0.35)
        _Stream.intervals.append((start, time.monotonic()))
        _Stream.seen.append(spend_guard.inflight_usd()["count"])
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def aiter_lines(self):
        body = {
            "choices": [
                {"delta": {"content": "[Альфа бежит.]"}, "finish_reason": None}
            ]
        }
        yield "data: " + json.dumps(body)
        yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
        yield "data: [DONE]"

    async def aread(self) -> bytes:
        return b""


class _Client:
    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_e: object) -> bool:
        return False

    def stream(self, *_a: object, **_k: object) -> _Stream:
        return _Stream()


def _install_live(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.services import gpt_api

    async def _ok(_url: str) -> None:
        return None

    monkeypatch.setattr(settings, "vibecode_api_key", "vk-test")
    monkeypatch.setattr(gpt_api, "_ensure_url_host_resolves", _ok)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    _Stream.intervals = []
    _Stream.seen = []


@pytest.mark.asyncio
async def test_live_stream_calls_overlap_without_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _quiet_gate(monkeypatch, run_cap=0.0)
    _install_live(monkeypatch)
    t0 = time.monotonic()

    async def one(tag: str) -> str:
        return await sb.call_opus("user", "system", tag, tmp_path / tag, tries=1)

    a, b = await asyncio.gather(one("a"), one("b"))
    elapsed = time.monotonic() - t0
    assert a.strip() and b.strip()
    assert len(_Stream.intervals) == 2
    left, right = _Stream.intervals
    assert left[0] < right[1] and right[0] < left[1]
    assert elapsed < 0.35 * 1.5


@pytest.mark.asyncio
async def test_one_live_call_reserves_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    estimate = spend_guard.DEFAULT_REQUEST_ESTIMATE_USD
    _quiet_gate(monkeypatch, run_cap=estimate * 1.5)
    _install_live(monkeypatch)
    text = await sb.call_opus("user", "system", "one", tmp_path / "one", tries=1)
    assert text.strip()
    assert _Stream.seen == [1]


@pytest.mark.asyncio
async def test_concurrent_calls_cannot_both_pass_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    estimate = spend_guard.DEFAULT_REQUEST_ESTIMATE_USD
    _quiet_gate(monkeypatch, run_cap=estimate * 1.5)
    started = 0
    release = asyncio.Event()

    async def slow_sums(_session: object, **_k: object) -> dict[str, float]:
        nonlocal started
        started += 1
        if started >= 2:
            release.set()
        await release.wait()
        return {"run": 0.0, "project": 0.0, "daily": 0.0}

    monkeypatch.setattr(spend_guard, "conservative_sums", slow_sums)
    results = await asyncio.gather(
        spend_guard.paid_gate(
            provider="gpt", what="a", estimate_usd=estimate, run_id="run-1"
        ),
        spend_guard.paid_gate(
            provider="gpt", what="b", estimate_usd=estimate, run_id="run-1"
        ),
        return_exceptions=True,
    )
    ok = [item for item in results if isinstance(item, spend_guard.PaidTicket)]
    blocked = [item for item in results if isinstance(item, spend_guard.PaidCallBlocked)]
    assert len(ok) == 1
    assert len(blocked) == 1
    assert spend_guard.inflight_usd(run_id="run-1")["count"] == 1
    ok[0].release()


@pytest.mark.asyncio
