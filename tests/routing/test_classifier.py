"""Детерминированные правила до LLM и порог fast-классификатора."""

from __future__ import annotations

import pytest

from app.orchestrator.routing.model_router import LiveRouter, RouterExhausted
from app.orchestrator.routing.transport import ProviderTimeout
from tests.routing.fakes import FakeTransport, ok


@pytest.mark.parametrize(
    ("text", "kwargs", "tier", "mode"),
    [
        ("да", {}, "fast", "task"),
        ("подтверждаю", {}, "fast", "task"),
        ("ок", {}, "fast", "task"),
        ("yes", {}, "fast", "task"),
        ("стой", {}, "fast", "interrupt"),
        ("отмена", {}, "fast", "interrupt"),
        ("суммируй последний шаг", {}, "fast", "task"),
        ("кратко перескажи ход критика", {}, "fast", "task"),
        ("только посмотри логи, ничего не меняй", {}, "fast", "task"),
        ("какой статус проекта?", {}, "fast", "task"),
        ("сколько кадров в сцене 3?", {}, "fast", "task"),
        ("classify this short note", {}, "fast", "task"),
        ("read-only: список нод", {}, "fast", "task"),
        ("Составь план запуска кадров", {}, "strong", "task"),
        ("Сделай ревью чекпоинта", {}, "strong", "task"),
        ("Напиши промпт ноды", {}, "strong", "task"),
        ("Собери отчёт по проекту", {}, "strong", "task"),
        ("Draft a plan to resume", {}, "strong", "task"),
        ("Please review the verdict", {}, "strong", "task"),
        ("Update the prompt section", {}, "strong", "task"),
        ("Need a report of spend", {}, "strong", "task"),
        ("а" * 801, {}, "strong", "task"),
        ("продолжи", {"agent_role": "planner"}, "strong", "task"),
        ("ок", {"agent_role": "critic"}, "strong", "task"),
        ("запусти", {"risk": "medium"}, "strong", "task"),
        ("поменяй температуру", {"mutating": True}, "strong", "task"),
        ("стой, переделай план", {}, "strong", "interrupt"),
        ("что думаешь?", {}, None, "append"),
        ("поправь тон ответа", {}, None, "steer"),
    ],
)
async def test_deterministic_rules(config, text, kwargs, tier, mode):
    transport = FakeTransport([ok("should-not-run")])
    router = LiveRouter(config, transport=transport)
    decision = await router.classify(text, **kwargs)
    assert decision.mode == mode
    assert decision["mode"] == mode
    if tier is None:
        assert decision.deterministic is False
        assert decision.tier == "strong"
        assert transport.calls, "спорный tier зовёт классификатор"
    else:
        assert decision.deterministic is True
        assert decision.tier == tier
        assert decision["tier"] == tier
        assert decision.confidence == 1.0
        assert transport.calls == []


async def test_length_boundary_is_800(config):
    transport = FakeTransport([ok('{"tier":"fast","mode":"task","confidence":0.99}')])
    router = LiveRouter(config, transport=transport)
    short = await router.classify("а" * 800)
    assert short.deterministic is False
    assert short.tier == "fast"
    long = await router.classify("б" * 801)
    assert long.tier == "strong"
    assert long.deterministic is True
    assert len(transport.calls) == 1


async def test_classifier_threshold(config):
    low = FakeTransport([ok('{"tier":"fast","mode":"task","confidence":0.79}')])
    router = LiveRouter(config, transport=low)
    decision = await router.classify("продолжи")
    assert decision.deterministic is False
    assert decision.tier == "strong"
    assert decision.confidence == pytest.approx(0.79)

    high = FakeTransport([ok('{"tier":"fast","mode":"steer","confidence":0.8}')])
    router = LiveRouter(config, transport=high)
    decision = await router.classify("продолжи")
    assert decision.tier == "fast"
    assert decision.mode == "steer"
    assert decision.confidence == pytest.approx(0.8)


async def test_question_without_task_keeps_append_mode(config):
    transport = FakeTransport(
        [ok('{"tier":"fast","mode":"task","confidence":0.95}')]
    )
    router = LiveRouter(config, transport=transport)
    decision = await router.classify("что думаешь?")
    assert decision.mode == "append"
    assert decision.tier == "fast"
    assert decision.deterministic is False


async def test_auto_call_uses_last_user_message_not_system(config):
    transport = FakeTransport([ok("pong", model="gpt-5.6-sol")])
    router = LiveRouter(config, transport=transport)
    response = await router.call(
        "auto",
        [
            {"role": "system", "content": "составь план и отчёт " + ("х" * 900)},
            {"role": "user", "content": "да"},
        ],
    )
    assert response.tier == "strong"
    assert response.deterministic is True
    assert transport.calls[0].model == "gpt-5.6-sol"
    assert len(transport.calls) == 1


async def test_garbage_classifier_falls_back_to_strong(config):
    transport = FakeTransport([ok("не json")])
    router = LiveRouter(config, transport=transport)
    decision = await router.classify("ещё раз")
    assert decision.tier == "strong"
    assert decision.deterministic is False
    assert decision.confidence == 0.0


async def test_classifier_timeout_does_not_escape_as_success_tier(config, sleeper):
    transport = FakeTransport(
        [ProviderTimeout("t")] * 3 + [ProviderTimeout("t")] * 3
    )
    router = LiveRouter(config, transport=transport, sleeper=sleeper)
    with pytest.raises(RouterExhausted):
        await router.classify("продолжи")
