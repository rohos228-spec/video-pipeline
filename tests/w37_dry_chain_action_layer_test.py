"""W37: сухой стенд полной цепочки нод через слой действий (поддельные провайдеры, без сети, без денег).

Цепочка берётся из графа по умолчанию (``default_graph``): по одному поддельному действию на рабочую ноду, шаги связаны
``depends_on`` в порядке графа. Платные ноды «тратят» кредиты у поддельного провайдера, слой действий резервирует и
сверяет. Проверяется не качество нод, а обвязка: порядок, одно подтверждение, согласованность журналов расходов,
остановка на сбое без повторных запусков и ограниченность циклов «отклонить - перегенерировать».
БД свежая, вне репозитория (tmp_path фикстуры ``env``), реальные ключи и .env не читаются.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models import Project
from app.orchestrator.default_graph import default_graph
from app.services import regen_budget
from app.services.action_layer import executor, store
from app.services.action_layer.spec import ActionSpec, CostEstimate, ensure_builtin_actions, registry
from app.services.kie_catalog import CREDIT_USD
from app.services.spend_guard import SpendLedger
from app.settings import settings
from tests.action_layer_support import env  # noqa: F401

BASE = "/api/action-layer"
FRAMES = 3
# Нода графа -> (цена единицы в кредитах, сколько единиц работы). Ноды без записи не платят.
PRICE: dict[str, tuple[float, int]] = {
    "plan": (1.0, 1), "split": (0.0, 1), "assemble": (0.0, 1), "script": (2.0, 1), "excel_gpt": (1.5, 1), "hero": (3.0, 1), "items": (2.0, 1),
    "image_prompts": (1.0, 1), "images": (4.0, FRAMES), "animation_prompts": (1.0, 1), "videos": (20.0, FRAMES),
    "audio": (5.0, 1), "music": (12.0, 1), "sfx_plan": (1.0, 1), "sfx_gen": (2.0, FRAMES),
}
NOT_IN_DRY_CHAIN = {"topic", "storage", "publish"}      # конфиг-нода и публикация в сеть: сюда не входят


class FakeProviders:
    """Один на тест: журнал вызовов в порядке прихода и общий счёт «в кабинете»."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.cabinet_credits = 0.0
        self.fail_on: str | None = None


def chain_nodes() -> list[dict]:
    nodes, edges = default_graph()
    by_id = {n["id"]: n for n in nodes}
    nxt = {e["source"]: e["target"] for e in edges}
    cur = next(n["id"] for n in nodes if n["type"] == "topic")
    order = []
    while cur in nxt:
        cur = nxt[cur]
        order.append(by_id[cur])
    return [n for n in order if n["type"] not in NOT_IN_DRY_CHAIN]


def action_id(node: dict) -> str:
    return f"test.chain_{node['id'].removeprefix('n_')}"


def build_specs(prov: FakeProviders, nodes: list[dict]) -> list[ActionSpec]:
    schema = {"type": "object", "properties": {"project_id": {"type": "integer", "minimum": 1}},
              "required": ["project_id"], "additionalProperties": False}
    specs = []
    for node in nodes:
        price, units = PRICE[node["type"]]
        cost = price * units

        def make(nid=node["id"], cost=cost):
            async def run(ctx, params):
                prov.calls.append(nid)
                if prov.fail_on == nid:
                    raise RuntimeError(f"поддельный провайдер: сбой на {nid}")
                prov.cabinet_credits += cost
                return {"fake": True, "credits_spent": cost} if cost else {"fake": True}
            return run

        specs.append(ActionSpec(
            id=action_id(node), title_ru=f"ТЕСТ цепочки: {node['id']}", description_ru="Поддельная нода сухого стенда.",
            params_schema=schema, executor=make(), risk="paid" if cost else "local_safe", spends_money=bool(cost), idempotent=False,
            scope="test", tags=("test",),
            estimator=lambda p, c=cost: CostEstimate(c * 0.5, c * 1.5, "оценка сухого стенда")))
    return specs


@pytest_asyncio.fixture
async def chain(env, monkeypatch):  # noqa: F811
    ensure_builtin_actions()
    monkeypatch.setattr(settings, "spend_project_cap_usd", 1000.0)
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 1000.0)
    monkeypatch.setattr(settings, "action_layer_session_cap_credits", 100000.0)
    prov = FakeProviders()
    nodes = chain_nodes()
    specs = build_specs(prov, nodes)
    for spec in specs:
        registry.register(spec)
    env.prov, env.nodes = prov, nodes
    env.project_id = await env.add_project(frames=FRAMES)
    yield env
    for spec in specs:
        registry.unregister(spec.id)


async def make_plan(env) -> str:
    steps = []
    for i, node in enumerate(env.nodes):
        steps.append({"action_id": action_id(node), "params": {"project_id": env.project_id},
                      "depends_on": [i - 1] if i else []})
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": steps, "request_text": "сухая цепочка"})
    assert r.status_code == 200, r.text
    pv = r.json()["preview"]
    assert pv["ok"] and pv["needs_approval"] and pv["risk_max"] == "paid"
    return r.json()["plan_id"]


async def start(env, plan_id: str) -> None:
    a = await env.client.post(f"{BASE}/plans/{plan_id}/approve")
    assert a.status_code == 200, a.text
    r = await env.client.post(f"{BASE}/plans/{plan_id}/run")
    assert r.status_code == 200 and r.json()["status"] == "running", r.text
    await executor.wait(plan_id)


async def view(env, plan_id: str) -> dict:
    r = await env.client.get(f"{BASE}/plans/{plan_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def history(env, plan_id: str) -> list[dict]:
    r = await env.client.get(f"{BASE}/history", params={"plan_id": plan_id, "limit": 500})
    assert r.status_code == 200, r.text
    return list(reversed(r.json()["items"]))


def expected_total() -> float:
    return sum(PRICE[n["type"]][0] * PRICE[n["type"]][1] for n in chain_nodes())    # excel_gpt в графе дважды


def test_chain_follows_the_default_graph() -> None:
    types = [n["type"] for n in chain_nodes()]
    assert types[0] == "plan"
    assert types.index("images") < types.index("videos") < types.index("audio") < types.index("music") \
        < types.index("sfx_plan") < types.index("sfx_gen")
    assert all(t in PRICE for t in types), [t for t in types if t not in PRICE]
    assert types[-2:] == ["sfx_gen", "assemble"]                  # сборка локальная: платной оценки у неё нет
    assert not NOT_IN_DRY_CHAIN & set(types)


async def test_whole_chain_runs_once_in_graph_order(chain) -> None:
    pid = await make_plan(chain)
    await start(chain, pid)
    v = await view(chain, pid)
    assert v["status"] == "succeeded", [(s["action_id"], s["status"], s["error"]) for s in v["steps"]]
    assert [s["action_id"] for s in v["steps"]] == [action_id(n) for n in chain.nodes]
    assert all(s["status"] == "succeeded" and s["attempts"] == 1 for s in v["steps"])   # ни одного повторного захода
    # каждая нода вызвана у провайдера ровно один раз и строго по порядку графа
    assert chain.prov.calls == [n["id"] for n in chain.nodes]
    rows = await history(chain, pid)
    events = [r["event"] for r in rows]
    assert events.count("approval_consumed") == 1 and events.count("plan_started") == 1
    assert events.count("step_started") == len(chain.nodes) == events.count("step_succeeded")
    started = [r["action_id"] for r in rows if r["event"] == "step_started"]
    assert started == [action_id(n) for n in chain.nodes]


async def test_spend_ledgers_agree_after_the_chain(chain) -> None:
    pid = await make_plan(chain)
    await start(chain, pid)
    v = await view(chain, pid)
    total = expected_total()
    assert chain.prov.cabinet_credits == pytest.approx(total)
    assert sum(s["cost"]["actual"] or 0.0 for s in v["steps"]) == pytest.approx(total)
    assert v["actual_credits"] == pytest.approx(total)
    assert v["spent_credits"] == pytest.approx(total)
    async with chain.factory() as s:
        net = (await s.execute(select(func.coalesce(func.sum(store.ActionSpend.credits), 0.0)).where(
            store.ActionSpend.plan_id == pid))).scalar_one()
        led = (await s.execute(select(func.coalesce(func.sum(SpendLedger.credits), 0.0),
                                      func.coalesce(func.sum(SpendLedger.usd), 0.0)).where(
            SpendLedger.run_id == f"plan:{pid}"))).one()
    assert net == pytest.approx(total)                           # резерв + поправки = факт плана
    assert led[0] == pytest.approx(total)                        # общий журнал расходов (append-only) сходится с кабинетом
    assert led[1] == pytest.approx(total * CREDIT_USD)
    assert chain.prov.cabinet_credits * CREDIT_USD == pytest.approx(led[1])


async def test_failure_stops_the_chain_and_nothing_restarts(chain) -> None:
    chain.prov.fail_on = "n_videos"
    pid = await make_plan(chain)
    await start(chain, pid)
    v = await view(chain, pid)
    assert v["status"] == "failed", v["status"]
    ids = [n["id"] for n in chain.nodes]
    cut = ids.index("n_videos")
    assert chain.prov.calls == ids[:cut + 1]                     # после сбойной ноды ни одна следующая не вызвана
    by_status = {s["action_id"]: s["status"] for s in v["steps"]}
    assert by_status[action_id(chain.nodes[cut])] == "failed"
    assert all(st == "succeeded" for a, st in list(by_status.items())[:cut])
    assert all(st in ("skipped", "cancelled", "pending") for a, st in list(by_status.items())[cut + 1:])
    # факт = только то, что реально ушло в кабинет до сбоя; сбойная нода денег не взяла
    done = sum(PRICE[n["type"]][0] * PRICE[n["type"]][1] for n in chain.nodes[:cut])
    assert chain.prov.cabinet_credits == pytest.approx(done)
    # повторный запуск того же плана отказывает (план закрыт), провайдера не трогает
    before = list(chain.prov.calls)
    again = await chain.client.post(f"{BASE}/plans/{pid}/run")
    assert again.status_code in (403, 409), again.text
    assert chain.prov.calls == before
    events = [r["event"] for r in await history(chain, pid)]
    assert events.count("plan_started") == 1


async def test_second_plan_after_a_failure_is_a_new_decision_not_an_automatic_retry(chain) -> None:
    chain.prov.fail_on = "n_images"
    pid = await make_plan(chain)
    await start(chain, pid)
    assert (await view(chain, pid))["status"] == "failed"
    calls_after_fail = len(chain.prov.calls)
    await executor.wait(pid)
    assert len(chain.prov.calls) == calls_after_fail              # сам по себе ничего не перезапускается
    chain.prov.fail_on = None
    new_id = await make_plan(chain)                               # новый план и новое подтверждение оператора
    assert new_id != pid
    r = await chain.client.post(f"{BASE}/plans/{new_id}/run")
    assert r.status_code == 403                                   # без подтверждения не стартует
    await start(chain, new_id)
    assert (await view(chain, new_id))["status"] == "succeeded"


def _project() -> Project:
    return Project(topic="t", slug="regen-loop", meta={})


def test_reject_regenerate_loop_is_bounded_per_frame() -> None:
    p = _project()
    allowed_rounds = 0
    for _ in range(100):                                          # «отклонить -> перегенерировать» без человека
        verdict = regen_budget.charge(p, "images", [1])
        if verdict.blocked:
            break
        allowed_rounds += 1
    per_frame, per_run = regen_budget.limits()
    assert verdict.blocked == ["1"], "цикл не остановился"
    assert allowed_rounds == min(per_frame, per_run) <= 12
    again = regen_budget.charge(p, "images", [1])                 # после остановки остаётся остановленным
    assert again.blocked == ["1"]
    assert regen_budget.reset(p, "images") is True                # снять может только человек (reset)
    assert regen_budget.charge(p, "images", [1]).blocked == []


def test_reject_loop_over_many_frames_is_bounded_per_run_and_in_money() -> None:
    p = _project()
    rounds = 0
    for r in range(200):
        verdict = regen_budget.charge(p, "video", [(r % 12) + 1 + 100 * (r // 12)])   # каждый раз новый кадр
        if verdict.blocked:
            break
        rounds += 1
    assert verdict.run_exhausted or verdict.usd_exhausted, "ни счётчик раундов, ни деньги не остановили цикл"
    per_frame, per_run = regen_budget.limits()
    assert rounds <= max(per_run, 1) and rounds < 200
    usd_cap, _ = regen_budget.usd_limits()
    assert not usd_cap or verdict.usd_spent <= usd_cap + regen_budget.estimate_round_usd("video", 1)
