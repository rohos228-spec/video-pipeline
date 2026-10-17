"""Батч через gather. Сдвиг по умолчанию 0, gap опционален."""

from __future__ import annotations

import pytest

from app.orchestrator.routing.model_router import LiveRouter
from tests.routing.fakes import FakeTransport, ok


def _batch(n: int, *, tier: str = "strong") -> list[dict]:
    return [
        {
            "tier": tier,
            "messages": [{"role": "user", "content": f"m{i}"}],
            "run_id": f"b{i}",
        }
        for i in range(n)
    ]


async def test_batch_default_gap_is_zero_and_parallel(config, sleeper):
    transport = FakeTransport([ok(f"r{i}") for i in range(3)])
    router = LiveRouter(config, transport=transport, sleeper=sleeper)
    responses = await router.call_batch(_batch(3))
    assert [item.text for item in responses] == ["r0", "r1", "r2"]
    assert sleeper.calls == []
    assert transport.max_in_flight == 3


async def test_optional_gap_is_not_clamped_to_2_5(config, sleeper):
    transport = FakeTransport([ok(f"r{i}") for i in range(3)])
    router = LiveRouter(config, transport=transport, sleeper=sleeper)
    await router.call_batch(_batch(3), gap=0.5)
    assert sleeper.calls == [0.5, 1.0]
    assert transport.max_in_flight == 3


async def test_single_item_batch_does_not_sleep(config, sleeper):
    transport = FakeTransport([ok("only")])
    router = LiveRouter(config, transport=transport, sleeper=sleeper)
    responses = await router.call_batch(_batch(1))
    assert responses[0].text == "only"
    assert sleeper.calls == []


async def test_batch_missing_run_id_stops_before_gather(config):
    transport = FakeTransport([ok("should-not-run")])
    router = LiveRouter(config, transport=transport)
    items = [
        {"tier": "auto", "messages": [{"role": "user", "content": "что думаешь?"}]},
        {
            "tier": "auto",
            "messages": [{"role": "user", "content": "ещё раз"}],
            "run_id": "a1",
        },
    ]
    with pytest.raises(ValueError, match="internal budget requires run_id"):
        await router.call_batch(items)
    assert transport.calls == []


async def test_auto_is_classified_before_gather(config, sleeper):
    transport = FakeTransport(
        [
            ok('{"tier":"fast","mode":"task","confidence":0.95}'),
            ok('{"tier":"strong","mode":"task","confidence":0.91}'),
            ok("body-0"),
            ok("body-1"),
        ]
    )
    router = LiveRouter(config, transport=transport, sleeper=sleeper)
    items = [
        {
            "tier": "auto",
            "messages": [{"role": "user", "content": "что думаешь?"}],
            "run_id": "a0",
        },
        {
            "tier": "auto",
            "messages": [{"role": "user", "content": "ещё раз"}],
            "run_id": "a1",
        },
    ]
    responses = await router.call_batch(items)
    assert [item.text for item in responses] == ["body-0", "body-1"]
    assert sleeper.calls == []
    assert len(transport.calls) == 4
    assert transport.calls[0].messages[0]["role"] == "system"
    assert transport.calls[1].messages[0]["role"] == "system"
    assert transport.calls[2].messages[-1]["content"] == "что думаешь?"
    assert transport.calls[3].messages[-1]["content"] == "ещё раз"
