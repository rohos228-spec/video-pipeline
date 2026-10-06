"""Все случаи фикстуры оценки через настоящий планировщик с «идеальной» моделью-заглушкой.

Заглушка отвечает ровно тем, что ждёт фикстура (шаги, вопрос или отказ). Проверяется не модель,
а всё вокруг неё: каталог показывает нужные действия, строгая проверка принимает ожидаемые
параметры на реалистичном сиде, защита от лишнего их не режет, загрузки и неоднозначный шот
превращаются в вопросы, а оценка (scripts/eval_action_planner.py) засчитывает результат.
Так видно, что ожидания фикстуры согласованы со схемами действий и с сидом.
"""
from __future__ import annotations

import json

import pytest_asyncio

from app.services import prompt_library
from app.services.action_layer import planner
from app.services.action_layer.spec import ensure_builtin_actions, registry
from app.settings import settings
from tests.action_layer_support import env  # noqa: F401
from tests.test_action_layer_planner import reply, res
from tests.test_action_planner_eval_script import script
from tests.test_action_planner_seed_context import CASES, load_seed

PID = 2
OP = "web_operator:oracle"


def dummy(sch: dict):
    """Минимальное допустимое значение по схеме (для обязательных полей, которых нет в фикстуре)."""
    if "enum" in sch:
        return sch["enum"][0]
    typ = sch.get("type")
    if typ == "integer":
        return max(1, int(sch.get("minimum", 1)))
    if typ == "number":
        return max(1.0, float(sch.get("minimum", 1)))
    if typ == "boolean":
        return True
    if typ == "array":
        return [dummy(sch.get("items") or {})] * max(1, int(sch.get("minItems", 0)))
    if typ == "object":
        return {k: dummy(v) for k, v in (sch.get("properties") or {}).items() if k in (sch.get("required") or [])}
    if "pattern" in sch and sch["pattern"].startswith("^[a-z0-9_]"):
        return "script"
    if sch.get("pattern", "").startswith("^project:"):
        return "project:2:n_excel_gpt_fw_shots:prompt"
    return "x" * max(1, int(sch.get("minLength", 1)))


def complete(value, sch: dict):
    """Дополняет значение обязательными полями схемы (рекурсивно), заданное не меняет."""
    if isinstance(value, dict) and sch.get("type") == "object":
        props = sch.get("properties") or {}
        out = {k: complete(v, props.get(k, {})) for k, v in value.items()}
        for key in sch.get("required", []):
            if key not in out:
                out[key] = dummy(props[key])
        return out
    if isinstance(value, list) and sch.get("type") == "array":
        return [complete(v, sch.get("items") or {}) for v in value]
    return value


class Const:
    """Заглушка модели: на любой запрос (в том числе повторный) один и тот же ответ."""

    def __init__(self, result) -> None:
        self.result = result

    async def __call__(self, **kwargs):
        return self.result


def oracle_reply(case: dict, variables: dict) -> dict:
    if case["outcome"] != "plan":
        return reply([], clar=" ".join(["Уточните, пожалуйста.", *case.get("clarify_contains", [])]), expl="")
    steps = []
    for i, s in enumerate(script().resolve_vars(case["steps"], variables)):
        schema = registry.get(s["action_id"]).params_schema
        params = dict(s["params"])
        for key in schema.get("required", []):
            if key == "project_id":
                params[key] = PID
            elif key not in params and s["action_id"] not in _FILLED_BY_SERVER.get(key, ()):
                params[key] = dummy(schema["properties"][key])
        props = schema.get("properties", {})
        params = {k: complete(v, props.get(k, {})) for k, v in params.items()}
        steps.append({"action_id": s["action_id"], "params": params, "depends_on": [i - 1] if i else []})
    return reply(steps, expl="Делаю.")


# параметры, которые сервер подставляет сам: оракул их не даёт (проверяем заодно подстановку)
_FILLED_BY_SERVER = {
    "image_generator": ("montage.generate_scene_images", "montage.improve_scene", "montage.apply_queue"),
    "image_resolution": ("montage.generate_scene_images", "montage.improve_scene", "montage.apply_queue"),
}


@pytest_asyncio.fixture
async def oracle_env(env, tmp_path, monkeypatch):  # noqa: F811
    ensure_builtin_actions()
    monkeypatch.setattr(prompt_library, "PROMPTS_ROOT", prompt_library.PROMPTS_ROOT)
    monkeypatch.setattr(settings, "vibecode_api_key", "vk-test-key")
    monkeypatch.setattr(settings, "vibecode_base_url", "https://vibecode.moe/v1")
    monkeypatch.setattr(settings, "action_layer_planner_max_catalog", 24)
    monkeypatch.setattr(settings, "action_layer_planner_calls_per_hour", 10000)
    # каждый вызов резервирует оценку в локальном бюджете: 250 вызовов упираются в потолок проекта (10 USD)
    monkeypatch.setattr(settings, "spend_project_cap_usd", 1000.0)
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 1000.0)
    await load_seed().seed_realistic(project_id=PID, factory=env.factory, prompts_dir=tmp_path / "prompts")
    yield env
    planner.set_transport(None)


async def test_every_fixture_case_is_scored_ok_for_the_expected_answer(oracle_env) -> None:
    m = script()
    variables = load_seed().expected_vars(PID)
    failed: list[str] = []
    for case in CASES:
        ctx = case.get("context") or {}
        planner.set_transport(Const(res(oracle_reply(case, variables))))
        steps, clar, code, expl = [], None, None, None
        try:
            out = await planner.plan_from_text(
                case["text"], operator=OP, project_id=PID, selected_scene=ctx.get("selected_scene"),
                selected_frame=ctx.get("selected_frame"),
                attachments=load_seed().fake_attachments(PID, ctx.get("attachments")))
            steps, clar, expl = out.reply.steps, out.reply.needs_clarification, out.reply.explanation_ru
        except (planner.PlannerRejected, planner.PlannerFailed) as exc:
            code = f"{exc.code}: {exc}"
        row = m.score_case(case, steps, clar, code, expl, variables)
        if not row["ok"]:
            failed.append(f"{case['id']}: {json.dumps(row, ensure_ascii=False, default=str)[:300]}")
    assert not failed, "\n".join(failed)


async def test_concurrent_requests_share_the_local_database_without_errors(oracle_env) -> None:
    """Как в `eval_action_planner.py --concurrency 4`: журнал и бюджет пишутся из нескольких запросов сразу."""
    m = script()
    variables = load_seed().expected_vars(PID)
    plans = [c for c in CASES if c["outcome"] == "plan" and not (c.get("context") or {}).get("attachments")][:48]
    replies = {c["text"]: res(oracle_reply(c, variables)) for c in plans}

    class ByText:  # ответ выбирается по тексту запроса, поэтому порядок завершения не важен
        async def __call__(self, **kw):
            user = json.loads(kw["body"]["messages"][1]["content"])["user_request"]
            return replies[user]

    planner.set_transport(ByText())

    async def one(case):
        try:
            out = await planner.plan_from_text(case["text"], operator=OP, project_id=PID)
            return {"id": case["id"], "ok": bool(out.reply.steps), "est_cost_usd": 0.0}
        except Exception as exc:  # noqa: BLE001
            return {"id": case["id"], "ok": False, "error": f"{type(exc).__name__}: {exc}", "est_cost_usd": 0.0}

    rows = await m.run_cases(plans, one, concurrency=4, budget=m.SpendBudget(10.0))
    bad = [r for r in rows if not r["ok"]]
    assert len(rows) == len(plans) and not bad, bad[:3]
