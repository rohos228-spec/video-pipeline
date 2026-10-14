"""W50 фаза «Интерфейс оператора»: кнопка «снять резерв» (C-9) со стороны API и автообновление журнала (U-10).

Сети и денег нет: исполнитель слоя действий подменён (как в w40_money_gates_test), база временная.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.action_layer import budget, service
from tests.action_layer_support import env  # noqa: F401
from tests.w40_money_gates_test import (  # noqa: F401  (autouse-фикстура чистки глобалов исполнителя)
    _clean_executor_globals,
    _end_orphan_watch,
    _unresolved_step,
)

URL = "/api/action-layer/reserves/release"


@pytest.mark.raw_client
async def test_release_reserve_endpoint_refuses_without_operator_token(env, monkeypatch) -> None:
    plan_id, _pid = await _unresolved_step(env, monkeypatch)
    await _end_orphan_watch()
    body = {"plan_id": plan_id, "step_index": 0, "reason": "сверил кабинет Kie: списаний нет"}
    r = await env.client.post(URL, json=body)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "operator_token_required"
    r = await env.client.post(URL, json=body, headers={"X-VP-Operator": "wrong-token"})
    assert r.status_code == 403
    async with env.factory() as s:  # резерв по-прежнему удержан
        rows = await budget.unresolved_reserves(s)
    assert [(x["plan_id"], x["held_credits"]) for x in rows] == [(plan_id, 5.0)]


async def test_release_reserve_endpoint_needs_a_reason_and_releases_once(env, monkeypatch) -> None:
    plan_id, _pid = await _unresolved_step(env, monkeypatch)
    await _end_orphan_watch()
    listed = (await env.client.get("/api/action-layer/reserves")).json()
    assert listed["total_held_credits"] == pytest.approx(5.0)
    assert [(x["plan_id"], x["step_index"]) for x in listed["items"]] == [(plan_id, 0)]

    r = await env.client.post(URL, json={"plan_id": plan_id, "step_index": 0, "reason": " "})
    assert r.status_code == 422  # без причины снять нельзя
    r = await env.client.post(URL, json={"plan_id": plan_id, "step_index": 7, "reason": "сверил кабинет"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_releasable"

    r = await env.client.post(URL, json={"plan_id": plan_id, "step_index": 0, "reason": "сверил кабинет Kie: списаний нет"})
    assert r.status_code == 200, r.text
    assert r.json()["released_credits"] == pytest.approx(5.0)
    assert (await env.client.get("/api/action-layer/reserves")).json()["items"] == []
    events = [h["event"] for h in (await service.history(plan_id=plan_id))["items"]]
    assert "reserve_released" in events
    # повтор: снимать уже нечего
    r = await env.client.post(URL, json={"plan_id": plan_id, "step_index": 0, "reason": "сверил кабинет Kie"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_releasable"


# --- web: контракт интерфейса (текстовые проверки исходников; браузер и сеть не нужны) -----------------

WEB = Path(__file__).resolve().parents[1] / "web" / "src"


def _src(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def test_journal_section_polls_while_open_and_pauses_when_hidden() -> None:
    src = _src("components/orchestrator/journal-section.tsx")
    assert "JOURNAL_REFRESH_MS" in src and "setInterval" in src
    assert 'document.visibilityState === "hidden"' in src  # скрытая вкладка не опрашивает сервер
    assert "clearInterval" in src and "abort()" in src  # при смене проекта и закрытии цикл останавливается


def test_publications_panel_has_revoke_button_calling_revoke_route() -> None:
    src = _src("components/orchestrator/publications-section.tsx")
    assert '"revoke"' in src and "Отозвать разрешение" in src
    assert "${base}/${platform}/${kind}" in src  # POST .../publications/{platform}/revoke


def test_reserve_button_calls_release_with_a_reason_and_confirmation() -> None:
    src = _src("components/orchestrator/reserves-section.tsx")
    assert "Снять резерв" in src and "window.confirm" in src and "releaseReserve" in src
    assert "reserves/release" in _src("lib/action-layer.ts")
    assert "<ReservesSection" in _src("components/orchestrator/orchestrator-panel.tsx")


def test_orchestrator_panel_starts_collapsed_on_phone_and_rows_wrap() -> None:
    src = _src("components/orchestrator/orchestrator-panel.tsx")
    assert "NARROW_QUERY" in src and "narrow && !touched" in src
    assert src.count("flex flex-wrap items-center gap-2 rounded-md") == 3  # строки подтверждения не выходят за экран


def test_board_shows_scene_ordinal_and_undo_split_button() -> None:
    src = _src("components/canvas/assemble-montage-board.tsx")
    assert "vo_scene_ordinal" in src and "undoSplitMontageScene" in src and "Отменить деление" in src
