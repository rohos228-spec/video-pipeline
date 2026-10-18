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


class _Reply:
    intervals: list[tuple[float, float]] = []
    seen: list[int] = []

    def __init__(self) -> None:
        self.status_code = 200
        self.headers = {"content-type": "application/json"}
        self._body = {
            "model": sb.MODEL,
            "choices": [
                {
                    "message": {"role": "assistant", "content": "[Альфа бежит.]"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        self.text = json.dumps(self._body, ensure_ascii=False)
        self.content = self.text.encode("utf-8")

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_e: object) -> bool:
        return False

    async def aclose(self) -> None:
        return None

    def stream(self, *_a: object, **_k: object) -> None:
        raise AssertionError("scene_boundaries ушёл в stream, а не в POST 52pay")

    async def post(self, *_a: object, **_k: object) -> _Reply:
        start = time.monotonic()
        await asyncio.sleep(0.35)
        _Reply.intervals.append((start, time.monotonic()))
        _Reply.seen.append(spend_guard.inflight_usd()["count"])
        return _Reply()


def _install_live(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.services import api_tracker_hook, gpt_api, pay52_routes

    async def _ok(_url: str) -> None:
        return None

    # Только 52pay: без ключа vibecode запасного пути нет.
    monkeypatch.setattr(settings, "vibecode_api_key", "")
    monkeypatch.setattr(settings, "pay52_gpt_api_key", "pk-test")
    monkeypatch.setattr(gpt_api, "_ensure_url_host_resolves", _ok)
    monkeypatch.setattr(api_tracker_hook, "check_api_allowed", lambda **_k: None)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert pay52_routes.resolve_text(sb.MODEL) is not None
    _Reply.intervals = []
    _Reply.seen = []


@pytest.mark.asyncio
async def test_live_stream_calls_overlap_without_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _quiet_gate(monkeypatch, run_cap=0.0)
    _install_live(monkeypatch)

    async def one(tag: str) -> str:
        return await sb.call_opus("user", "system", tag, tmp_path / tag, tries=1)

    a, b = await asyncio.gather(one("a"), one("b"))
    assert a.strip() and b.strip()
    assert len(_Reply.intervals) == 2
    left, right = _Reply.intervals
    assert left[0] < right[1] and right[0] < left[1]


@pytest.mark.asyncio
async def test_one_live_call_reserves_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    estimate = spend_guard.DEFAULT_REQUEST_ESTIMATE_USD
    _quiet_gate(monkeypatch, run_cap=estimate * 1.5)
    _install_live(monkeypatch)
    text = await sb.call_opus("user", "system", "one", tmp_path / "one", tries=1)
    assert text.strip()
    assert _Reply.seen == [1]


@pytest.mark.asyncio
async def test_direct_stream_http_is_blocked_without_an_outer_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Прямой _chat_completions_stream_http без внешнего paid_call не шлёт HTTP."""
    import httpx

    from app.services import gpt_api

    estimate = spend_guard.DEFAULT_REQUEST_ESTIMATE_USD
    _quiet_gate(monkeypatch, run_cap=0.0)

    def _caps() -> dict[str, float]:
        return {"daily": estimate * 0.5, "project": 0.0, "run": 0.0}

    async def _sums(_session: object, **_k: object) -> dict[str, float]:
        return {"run": 0.0, "project": 0.0, "daily": 0.0}

    monkeypatch.setattr(spend_guard, "caps", _caps)
    monkeypatch.setattr(spend_guard, "conservative_sums", _sums)
    hits = {"n": 0}

    class _BlockedClient:
        def __init__(self, *_a: object, **_k: object) -> None:
            hits["n"] += 1

        async def __aenter__(self) -> _BlockedClient:
            hits["n"] += 1
            return self

        async def __aexit__(self, *_e: object) -> bool:
            return False

        def stream(self, *_a: object, **_k: object) -> _BlockedClient:
            hits["n"] += 1
            return self

    monkeypatch.setattr(httpx, "AsyncClient", _BlockedClient)
    with pytest.raises(spend_guard.PaidCallBlocked):
        await gpt_api._chat_completions_stream_http(
            url="http://llm.invalid/v1/chat/completions",
            headers={"Authorization": "Bearer vk-test"},
            body={"model": "gpt-5.6-sol", "messages": []},
            timeout=5,
            use_model="gpt-5.6-sol",
        )
    assert hits["n"] == 0


@pytest.mark.asyncio
async def test_concurrent_calls_cannot_both_pass_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    estimate = spend_guard.DEFAULT_REQUEST_ESTIMATE_USD
    _quiet_gate(monkeypatch, run_cap=estimate * 1.5)

    async def slow_sums(_session: object, **_k: object) -> dict[str, float]:
        assert spend_guard._inflight_lock.locked()
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
async def test_apply_ops_batches_parallel_through_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services.apply_ops_batches import run_apply_ops_batched
    from app.services.gpt_operator_client import OperatorApiResult

    _quiet_gate(monkeypatch, run_cap=0.0)
    peak = 0
    current = 0
    reserves = 0
    lock = asyncio.Lock()

    async def fake_run(**kwargs: object) -> OperatorApiResult:
        nonlocal peak, current, reserves
        path = kwargs["input_paths"][0]
        frames = json.loads(Path(path).read_text(encoding="utf-8"))["frames"]
        async with spend_guard.paid_call("gpt", "batch", estimate_usd=0.05):
            reserves += 1
            async with lock:
                current += 1
                peak = max(peak, current)
            await asyncio.sleep(0.05)
            async with lock:
                current -= 1
            assert spend_guard.inflight_usd()["count"] >= 1
        ops = [
            {"frame_uuid": fr["uuid"], "fields": {"main_action": "x"}}
            for fr in frames
        ]
        return OperatorApiResult(
            reply_text='{"ops":[]}',
            output_paths=[path],
            apply_ops={"ops": ops},
        )

    monkeypatch.setattr("app.services.apply_ops_batches.run_operator_api", fake_run)
    frames = [
        {"uuid": f"{i:024d}", "voiceover_text": f"текст {i}"} for i in range(3)
    ]
    ctx = tmp_path / "db_frames.json"
    ctx.write_text("{}", encoding="utf-8")
    res = await run_apply_ops_batched(
        project_dir=tmp_path,
        node_key="n_excel_gpt_2",
        role="excel_gpt",
        output_mode="project_file",
        prompt="p",
        accompanying="",
        db_ctx={"frames": frames},
        ctx_path=ctx,
        project_id=1,
        dense=True,
        chunk_size=1,
        parallel_max=3,
        stagger_sec=0,
    )
    assert reserves == 3
    assert peak == 3
    assert len(res.apply_ops["ops"]) == 3


def test_e2e_text_nodes_port_is_ephemeral() -> None:
    import importlib.util
    import socket
    import sys

    path = Path(__file__).resolve().parents[1] / "scripts" / "e2e_vibecode_text_nodes.py"
    spec = importlib.util.spec_from_file_location("e2e_vibecode_text_nodes", path)
    assert spec is not None and spec.loader is not None
    stand = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = stand
    spec.loader.exec_module(stand)

    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.bind(("127.0.0.1", 18767))
    held.listen(1)
    try:
        picked = stand.free_port()
    finally:
        held.close()
    assert picked != 18767
    assert stand.PORT != 18767
