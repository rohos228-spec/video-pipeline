"""Параллельные батчи с разносом 2–5 с, без сериализации."""

from __future__ import annotations

import random

from app.orchestrator.routing.model_router import LiveRouter
from tests.routing.fakes import FakeTransport, ok


async def test_batch_stagger_is_between_2_and_5_and_parallel(config, sleeper):
    transport = FakeTransport([ok(f"r{i}") for i in range(3)])
    router = LiveRouter(
        config,
        transport=transport,
        sleeper=sleeper,
        rng=random.Random(0),
    )
    batch = [
        {"tier": "strong", "messages": [{"role": "user", "content": f"m{i}"}]}
        for i in range(3)
    ]
    responses = await router.call_batch(batch)
    assert [r.text for r in responses] == ["r0", "r1", "r2"]
    assert len(sleeper.calls) == 2
    gap = sleeper.calls[0]
    assert 2 <= gap <= 5
    assert sleeper.calls[1] == gap * 2
    assert transport.max_in_flight == 3


async def test_single_item_batch_does_not_sleep(config, sleeper):
    transport = FakeTransport([ok("only")])
    router = LiveRouter(config, transport=transport, sleeper=sleeper)
    responses = await router.call_batch(
        [{"tier": "strong", "messages": [{"role": "user", "content": "один"}]}]
    )
    assert responses[0].text == "only"
    assert sleeper.calls == []
