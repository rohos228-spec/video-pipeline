"""Планировщик с предфильтром каталога: только показанные действия, журнал, уточнение."""
from __future__ import annotations

import json

import pytest

from app.services.action_layer import planner
from app.services.action_layer.spec import registry
from app.settings import settings
from tests.action_layer_support import env  # noqa: F401
from tests.test_action_layer_planner import (  # noqa: F401
    OP, history_rows, reply, res, step, stubs, use)


async def test_prompt_offers_only_filtered_catalog_and_records_counts(stubs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "action_layer_planner_max_catalog", 24)
    fake = use(res(reply([step("montage.get_board", {"project_id": 1})])))
    out = await planner.plan_from_text("покажи состояние доски монтажа", operator=OP)
    user = json.loads(fake.calls[0]["body"]["messages"][1]["content"])
    ids = {a["id"] for a in user["catalog"]}
    assert "montage.get_board" in ids and "project.stop" in ids
    assert len(ids) < len(registry) and len(ids) <= 24
    assert all({"id", "t", "risk", "p"} == set(a) for a in user["catalog"])
    assert out.meta["catalog_offered"] == len(ids)
    rows = await history_rows(stubs, "planner_call")
    assert rows[0].params["catalog_offered"] == len(ids)
    assert rows[0].params["catalog_total"] == len(registry)
    assert rows[0].params["prompt_tokens"] == 1000 and rows[0].params["prompt_chars"] > 0


async def test_action_not_offered_is_rejected_even_if_it_exists(stubs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "action_layer_planner_max_catalog", 24)
    fake = use(res(reply([step("zz.paid", {"project_id": 1, "n": 1})])))
    with pytest.raises(planner.PlannerRejected) as err:
        await planner.plan_from_text("покажи состояние доски монтажа", operator=OP)
    assert err.value.code == "action_not_offered" and len(fake.calls) == 1
    rows = await history_rows(stubs, "planner_call")
    assert rows[0].outcome == "rejected" and rows[0].params["catalog_offered"] > 0


async def test_no_match_sends_small_safe_set_with_hint_and_allows_clarification(
        stubs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "action_layer_planner_max_catalog", 24)
    fake = use(res(reply([], clar="Что именно сделать?")))
    out = await planner.plan_from_text("запушь в гитхаб", operator=OP)
    user = json.loads(fake.calls[0]["body"]["messages"][1]["content"])
    assert len(user["catalog"]) <= 6 and "hint" in user
    assert all(a["risk"] in ("read", "local_safe") for a in user["catalog"])
    assert out.reply.needs_clarification
    rows = await history_rows(stubs, "planner_call")
    assert rows[0].outcome == "clarify" and rows[0].params["catalog_no_match"] is True


def test_validate_reply_without_offered_set_checks_full_registry(stubs) -> None:
    ok = planner.validate_reply(json.dumps(reply([step()])), registry, max_steps=5, project_id=1)
    assert ok.steps[0]["action_id"] == "zz.read"
