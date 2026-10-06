"""Устойчивость планировщика: один повтор при сбое сети и один повтор вопроса о параметре с умолчанием.

Модель - заглушка (без сети); проект - реалистичный сид из scripts/eval_seed.py.
"""
from __future__ import annotations

import json

import pytest

from app.services.action_layer import planner, planner_rules
from app.services.gpt_api import GptApiError
from tests.action_layer_support import env  # noqa: F401
from tests.test_action_layer_planner import OP, history_rows, reply, res, use
from tests.test_action_planner_flow import PID, live, st  # noqa: F401


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch):
    monkeypatch.setattr(planner, "RETRY_DELAY_S", 0)


def ask(question: str) -> dict:
    return {"steps": [], "needs_clarification": question, "explanation_ru": "Нужно уточнить."}


# --- сбой сети ---------------------------------------------------------------------------------

async def test_timeout_is_retried_once_and_logged_as_attempt_two(live) -> None:
    fake = use(TimeoutError("slow"), res(reply([st("montage.get_board")])))
    out = await planner.plan_from_text("покажи доску монтажа", operator=OP, project_id=PID)
    assert out.reply.steps and len(fake.calls) == 2
    assert out.meta["attempts"] == 2 and out.meta["transport_retries"] == 1
    rows = await history_rows(live, "planner_call")
    assert sorted((r.params["attempt"], r.outcome) for r in rows) == [(1, "transport_error"), (2, "ok")]


async def test_second_transport_failure_is_retryable_with_a_russian_message(live) -> None:
    fake = use(OSError("down"), OSError("down"), res(reply([st("montage.get_board")])))
    with pytest.raises(planner.PlannerFailed) as err:
        await planner.plan_from_text("покажи доску монтажа", operator=OP, project_id=PID)
    assert err.value.code == "transport_error" and err.value.retryable is True
    assert str(err.value) == "сеть/модель недоступны, повторите"
    assert len(fake.calls) == 2  # не больше одного повтора


async def test_provider_refusal_that_cannot_succeed_is_not_retried(live) -> None:
    denied = GptApiError("GPT HTTP 401", context={"status_code": 401, "retryable": False})
    fake = use(denied, res(reply([st("montage.get_board")])))
    with pytest.raises(planner.PlannerFailed) as err:
        await planner.plan_from_text("покажи доску монтажа", operator=OP, project_id=PID)
    assert err.value.retryable is False and len(fake.calls) == 1
    flaky = GptApiError("GPT HTTP 503", context={"status_code": 503, "retryable": True})
    fake = use(flaky, res(reply([st("montage.get_board")])))
    out = await planner.plan_from_text("покажи доску монтажа", operator=OP, project_id=PID)
    assert out.reply.steps and len(fake.calls) == 2


async def test_validation_and_safety_rejections_are_never_retried_as_transport(live) -> None:
    fake = use(res(reply([{"action_id": "shell.exec", "params": {}, "depends_on": []}])),
               res(reply([st("montage.get_board")])))
    with pytest.raises(planner.PlannerRejected):
        await planner.plan_from_text("покажи доску монтажа", operator=OP, project_id=PID)
    assert len(fake.calls) == 1


async def test_transport_retry_does_not_use_up_the_format_repair(live) -> None:
    fake = use(OSError("down"), res("не json"), res(reply([st("montage.get_board")])))
    out = await planner.plan_from_text("покажи доску монтажа", operator=OP, project_id=PID)
    assert out.reply.steps and len(fake.calls) == 3


# --- вопрос о параметре с умолчанием -----------------------------------------------------------

async def test_question_about_a_project_default_is_repaired_once(live) -> None:
    good = reply([st("montage.generate_scene_images", frame_numbers=[3, 4])], expl="Картинки.")
    fake = use(res(ask("Какой генератор и какое разрешение картинок использовать?")), res(good))
    out = await planner.plan_from_text("сгенерируй картинки для кадров 3 и 4", operator=OP, project_id=PID)
    assert out.reply.steps and out.reply.needs_clarification is None
    assert len(fake.calls) == 2 and out.meta["clarify_repairs"] == 1
    note = json.loads(fake.calls[1]["body"]["messages"][-1]["content"])
    names = {r["param"] for r in note["clarification_resolved"]}
    assert {"image_generator", "image_resolution"} <= names
    assert all(r["source"] == "default" for r in note["clarification_resolved"])
    assert sorted(r.outcome for r in await history_rows(live, "planner_call")) == ["clarify_autorepair", "ok"]
    # размер запроса не растёт: системный промпт и каталог те же
    assert fake.calls[1]["body"]["messages"][0] == fake.calls[0]["body"]["messages"][0]
    assert fake.calls[1]["body"]["messages"][1] == fake.calls[0]["body"]["messages"][1]


async def test_repair_happens_only_once(live) -> None:
    fake = use(res(ask("Какой генератор использовать?")), res(ask("Какое разрешение использовать?")),
               res(reply([st("montage.get_board")])))
    out = await planner.plan_from_text("сгенерируй картинки для кадров 3 и 4", operator=OP, project_id=PID)
    assert out.reply.steps == [] and "разрешение" in out.reply.needs_clarification
    assert len(fake.calls) == 2


async def test_selected_frame_question_is_repaired_from_context(live) -> None:
    fake = use(res(ask("Какой кадр имеется в виду?")),
               res(reply([st("montage.set_coverage", frame_number=3, type="coverage_plan", value="КРУПНЫЙ")])))
    out = await planner.plan_from_text("сделай этот кадр крупным планом", operator=OP, project_id=PID,
                                       selected_frame=3)
    assert out.reply.steps and len(fake.calls) == 2
    note = json.loads(fake.calls[1]["body"]["messages"][-1]["content"])
    sel = note["clarification_resolved"][0]
    assert sel["param"] == "selected" and sel["source"] == "context" and sel["value"]["number"] == 3


@pytest.mark.parametrize("request_text,question", [
    ("удали этот кадр", "Какой кадр удалить?"),                                  # цель удаления
    ("примени правки из очереди", "Сколько операций применить и какой генератор?"),  # сумма и лимит
    ("перегенерируй картинку", "Почему нужна перегенерация? Укажите причину."),   # причина
    ("сгенерируй картинки для кадра 3", "Вы уверены, что можно потратить кредиты на генератор?"),  # подтверждение
])
async def test_safety_relevant_questions_are_never_auto_answered(live, request_text, question) -> None:
    fake = use(res(ask(question)), res(reply([st("montage.get_board")])))
    out = await planner.plan_from_text(request_text, operator=OP, project_id=PID, selected_frame=3)
    assert out.reply.steps == [] and out.reply.needs_clarification and len(fake.calls) == 1


async def test_server_built_questions_are_not_repaired(live) -> None:
    # вопрос про шот строит сервер (shot_ambiguity), а не модель: повтора нет
    fake = use(res(reply([st("montage.delete_image", frame_number=4)])), res(reply([st("montage.get_board")])))
    out = await planner.plan_from_text("убери картинку кадра 4", operator=OP, project_id=PID)
    assert out.reply.needs_clarification and len(fake.calls) == 1


# --- чистые правила -----------------------------------------------------------------------------

FACTS = {"project_id": 7, "defaults": {"image_generator": "g1", "image_resolution": "2K",
                                       "video_generator": "v1", "video_resolution": "720p",
                                       "step_code": "script"},
         "selected": {"number": 5, "frame_id": 50, "scene_head_id": 49}}


def names(question: str, request: str = "", facts=FACTS) -> set[str]:
    return {r["param"] for r in planner_rules.resolve_clarification(question, request, facts)}


def test_resolve_clarification_picks_the_named_defaults() -> None:
    assert names("Какой генератор картинок?") == {"image_generator"}
    assert names("Какое разрешение видео?") == {"video_resolution"}
    assert names("Какой проект?") == {"project_id"}
    assert names("Каким методом разбирать аудио?") == {"method"}
    assert names("Какой кадр?", "поставь на этот кадр крупный план") == {"selected"}
    assert names("Какой кадр?", "поставь крупный план") == set()  # человек не сказал «этот»


def test_resolve_clarification_has_no_context_no_answer() -> None:
    assert names("Какой генератор картинок?", facts=None) == set()
    assert names("Какой генератор картинок?", facts={"project_id": None}) == set()
    assert names("Какое разрешение?", facts={"project_id": 7, "defaults": {}}) == set()
    assert names("Какой кадр?", "этот кадр", {"project_id": 7}) == set()


@pytest.mark.parametrize("question,request_text", [
    ("Какой кадр удалить?", ""), ("Какой генератор?", "удали и сгенерируй заново"),
    ("Сколько кредитов потратить?", ""), ("Укажите причину снятия блокировки", ""),
    ("Подтвердите, какой генератор", ""), ("Какой лимит операций?", ""), ("Точно этот кадр?", "этот кадр"),
])
def test_resolve_clarification_never_touches_safety_parameters(question, request_text) -> None:
    assert names(question, request_text) == set()
