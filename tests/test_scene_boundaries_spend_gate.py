"""Поведение шлюза spend на живом пути call_opus → chat. Только моки."""

from __future__ import annotations

import asyncio
import json

import pytest


class _Route:
    api_model = "gpt-5.6-sol"
    url = "http://127.0.0.1:9/v1/chat/completions"

    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer test"}


class _Resp:
    def __init__(self, status: int = 200, text: str = "ok-text") -> None:
        self.status_code = status
        if status >= 400:
            self.text = text
            self._payload = {"error": text}
        else:
            self._payload = {
                "choices": [
                    {
                        "message": {"content": text},
                        "finish_reason": "stop",
                    }
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


class _HoldClient:
    """Один POST. На время запроса снаружи читают inflight."""

    def __init__(self, hold: float = 0.05) -> None:
        self.hold = hold
        self.posts = 0
        self.seen: list[float] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, _url, headers=None, json=None):  # noqa: A002
        from app.services import spend_guard

        self.posts += 1
        self.seen.append(spend_guard.inflight())
        await asyncio.sleep(self.hold)
        return _Resp()


@pytest.mark.asyncio
async def test_one_live_call_reserves_once(tmp_path, monkeypatch) -> None:
    """Один call_opus: inflight=1 на время HTTP и успех при cap 1.5."""
    from app.services import spend_guard
    from app.services.scene_boundaries import call_opus

    spend_guard.reset()
    spend_guard.set_cap(1.5)
    client = _HoldClient()
    _install_llm(monkeypatch, client)

    text = await call_opus("привет", "система", "once", tmp_path)

    assert text.strip()
    assert client.posts == 1
    assert client.seen == [1.0]
    assert spend_guard.reservations() == 1
    assert spend_guard.peak() == 1.0
    assert spend_guard.inflight() == 0.0


class _BlockAfter500:
    def __init__(self) -> None:
        self.posts = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, _url, headers=None, json=None):  # noqa: A002
        from app.services import spend_guard

        self.posts += 1
        spend_guard.set_cap(0)
        return _Resp(status=500, text="upstream down")


@pytest.mark.asyncio
async def test_block_mid_retry_stops_further_tries(tmp_path, monkeypatch) -> None:
    """500, затем cap=0: один HTTP, SpendBlocked наружу, второго sleep нет."""
    from app.services import spend_guard
    from app.services.scene_boundaries import call_opus
    from app.services.spend_guard import SpendBlocked

    spend_guard.reset()
    spend_guard.set_cap(1.5)
    client = _BlockAfter500()
    _install_llm(monkeypatch, client)
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def _fast(delay: float = 0, *args, **kwargs):
        sleeps.append(float(delay))
        if float(delay) >= 15:
            return None
        return await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fast)

    with pytest.raises(SpendBlocked):
        await call_opus("привет", "система", "block", tmp_path)

    assert client.posts == 1
    assert len(sleeps) <= 1
    assert all(delay < 15 for delay in sleeps)


@pytest.mark.asyncio
async def test_llm_pack_limit_from_env_default_8(tmp_path, monkeypatch) -> None:
    """Лимит пачек из LLM_PACK_PARALLEL, иначе 8; до лимита старт одновременный."""
    import time

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
