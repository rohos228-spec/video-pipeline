"""Лимит пачек R0a и живой параллельный call_opus. Не шлюз spend WS2."""

from __future__ import annotations

import asyncio
import json
import time

import pytest


class _Route:
    api_model = "gpt-5.6-sol"
    url = "http://127.0.0.1:9/v1/chat/completions"

    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer test"}


class _Resp:
    def __init__(self, text: str = "ok-text") -> None:
        self.status_code = 200
        self._payload = {
            "choices": [
                {"message": {"content": text}, "finish_reason": "stop"}
            ]
        }
        self.text = json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


def _install_llm(monkeypatch, client) -> None:
    async def _resolve(_url: str) -> None:
        return None

    monkeypatch.setattr(
        "app.services.pay52_routes.resolve_text", lambda _model=None: _Route()
    )
    monkeypatch.setattr(
        "app.services.api_tracker_hook.check_api_allowed", lambda **_k: None
    )
    monkeypatch.setattr("app.services.gpt_api._ensure_url_host_resolves", _resolve)
    monkeypatch.setattr("app.services.gpt_api._node_vibecode_override", lambda: False)
    monkeypatch.setattr("app.services.gpt_api._async_client", lambda **_k: client)


class _OverlapClient:
    """Два POST держатся одновременно: интервалы должны пересечься."""

    def __init__(self, hold: float = 0.35) -> None:
        self.hold = hold
        self.intervals: list[tuple[float, float]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, _url, headers=None, json=None):  # noqa: A002
        start = time.monotonic()
        await asyncio.sleep(self.hold)
        self.intervals.append((start, time.monotonic()))
        return _Resp()


@pytest.mark.asyncio
async def test_live_stream_calls_overlap_without_queue(tmp_path, monkeypatch) -> None:
    """Два call_opus идут вместе: HTTP пересекаются, сумма меньше 1.5× одного."""
    from app.services.scene_boundaries import call_opus

    client = _OverlapClient()
    _install_llm(monkeypatch, client)

    t0 = time.monotonic()
    one = await call_opus("один", "система", "one", tmp_path)
    one_dur = time.monotonic() - t0
    assert one.strip()
    assert len(client.intervals) == 1

    client.intervals.clear()
    t1 = time.monotonic()
    a, b = await asyncio.gather(
        call_opus("два", "система", "two", tmp_path),
        call_opus("три", "система", "three", tmp_path),
    )
    total = time.monotonic() - t1

    assert a.strip() and b.strip()
    assert len(client.intervals) == 2
    (s0, e0), (s1, e1) = client.intervals
    overlap = min(e0, e1) - max(s0, s1)
    assert overlap > 0
    assert total < one_dur * 1.5


@pytest.mark.asyncio
async def test_llm_pack_limit_from_env_default_8(tmp_path, monkeypatch) -> None:
    """Лимит пачек из LLM_PACK_PARALLEL, иначе 8; до лимита старт одновременный."""
    from app.services.apply_ops_batches import run_apply_ops_batched
    from app.services.gpt_operator_client import OperatorApiResult

    async def _run(limit: str | None) -> tuple[int, list[float]]:
        if limit is None:
            monkeypatch.delenv("LLM_PACK_PARALLEL", raising=False)
        else:
            monkeypatch.setenv("LLM_PACK_PARALLEL", limit)
        starts: list[float] = []
        inflight = 0
        max_inflight = 0
        lock = asyncio.Lock()

        async def fake_run(**kwargs):
            nonlocal inflight, max_inflight
            async with lock:
                inflight += 1
                max_inflight = max(max_inflight, inflight)
                starts.append(time.monotonic())
            await asyncio.sleep(0.15)
            path = kwargs["input_paths"][0]
            async with lock:
                inflight -= 1
            return OperatorApiResult(
                reply_text='{"ops":[]}',
                output_paths=[path],
                apply_ops={"ops": []},
            )

        monkeypatch.setattr(
            "app.services.apply_ops_batches.run_operator_api", fake_run
        )
        frames = [
            {"uuid": f"{i:024d}", "voiceover_text": f"vo {i}"} for i in range(10)
        ]
        ctx = tmp_path / f"db_{limit or 'default'}.json"
        ctx.write_text("{}", encoding="utf-8")
        await run_apply_ops_batched(
            project_dir=tmp_path,
            node_key="n_excel_gpt_fw_script",
            role="excel_gpt",
            output_mode="project_file",
            prompt="p",
            accompanying="",
            db_ctx={"frames": frames},
            ctx_path=ctx,
            project_id=1,
            dense=True,
            chunk_size=1,
            parallel_max=10,
            stagger_sec=0,
            allow_empty_ops=True,
        )
        return max_inflight, starts

    default_max, default_starts = await _run(None)
    assert default_max == 8
    assert max(default_starts[:8]) - min(default_starts[:8]) < 0.08

    capped_max, capped_starts = await _run("3")
    assert capped_max == 3
    assert max(capped_starts[:3]) - min(capped_starts[:3]) < 0.08
