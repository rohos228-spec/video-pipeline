"""Agent loop: user → LLM(tools) → выполнение tools → … → финальный ответ.

Долговечность: input_items сохраняются в agent_sessions каждую итерацию —
рестарт бэкенда не теряет контекст. Интерактив: present_choice ставит ход на
паузу (waiting_choice), продолжение — через resume_with_choice.
Параллельность: один активный turn на сессию (in-memory guard).
Отмена: stop_agent(uuid).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from app.models import AgentSessionStatus
from app.services import gpt_api
from app.services.agent import journal, sessions
from app.services.agent.registry import (
    AUTONOMY_MAX_RISK,
    execute_tool,
    get_tool,
    risk_allowed,
    tool_schemas,
)

# Импорт регистрирует tools (side-effect декораторов).
from app.services.agent import tools_read as _tools_read  # noqa: F401
from app.services.agent import tools_ui as _tools_ui  # noqa: F401
from app.services.agent import tools_write as _tools_write  # noqa: F401
from app.services.agent.tools_ui import PresentChoiceRequested

SYSTEM_PROMPT = (
    "Ты — встроенный LLM-агент пайплайна генерации вертикальных видео "
    "(video-pipeline). Управляешь проектами через tools. Правила:\n"
    "1) Сначала наблюдай (read-tools), потом предлагай/делай действия.\n"
    "2) Статусы и счётчики бери из get_project/get_run_nodes/tail_log, "
    "не выдумывай.\n"
    "3) Если данных не хватает — вызови tool, а не гадай.\n"
    "4) Ошибки шагов объясняй по step_failure и текстам ошибок нод.\n"
    "5) Перед действием с побочками (run_step/pause/reset) или когда нужен "
    "выбор пользователя (вариант промпта, стиль, подтверждение) — вызывай "
    "present_choice: пользователь увидит кнопки. Не спрашай в тексте то, "
    "что можно показать кнопками.\n"
    "6) Отвечай по-русски, коротко и по делу; числа — только из tools.\n"
    "7) Опасные действия (wipe/reset/delete) только если явно просил "
    "пользователь и автономия позволяет."
)

MAX_ITERATIONS = 10

_running_sessions: set[str] = set()
_stop_flags: set[str] = set()

OnEvent = Callable[[str, dict[str, Any]], Awaitable[None]]


def stop_agent(session_uuid: str) -> None:
    _stop_flags.add(session_uuid)


def _extract_fc_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        o
        for o in raw.get("output") or []
        if isinstance(o, dict) and o.get("type") == "function_call"
    ]


async def _handle_tool_calls(
    sid: int,
    autonomy: str,
    res: gpt_api.GptChatResult,
    emit: OnEvent,
) -> tuple[dict[str, str], dict[str, Any] | None]:
    """Выполнить tool calls. Возвращает (results, choice_card|None).

    choice_card не None — ход на паузе (present_choice): echo без output.
    """
    results: dict[str, str] = {}
    choice_card: dict[str, Any] | None = None
    for tc in res.tool_calls:
        name = tc["name"]
        cid = tc["call_id"]
        spec = get_tool(name)
        risk = spec.risk if spec else "read"
        try:
            args_obj = json.loads(tc["arguments"] or "{}")
        except json.JSONDecodeError:
            args_obj = {}

        if spec is not None and not risk_allowed(risk, autonomy):
            out = json.dumps(
                {
                    "error": (
                        f"tool {name} запрещён при автономии "
                        f"{autonomy} (риск {risk})"
                    )
                },
                ensure_ascii=False,
            )
            await journal.log_action(
                session_id=sid, tool=name, risk=risk, args=args_obj,
                status="denied", result=out,
                project_id=_pid_of(args_obj),
            )
            await sessions.append_message(
                sid, role="tool",
                content=f"{name}: denied (autonomy {autonomy})",
                payload={"tool": name, "status": "denied"},
            )
            await emit("tool_denied", {"tool": name, "risk": risk})
            results[cid] = out
            continue

        # UI-tools: исполнение прерывает ход (карточка выбора).
        if spec is not None and "ui" in spec.tags:
            try:
                out = await spec.executor(args_obj)
            except PresentChoiceRequested as pc:
                card = dict(pc.card)
                card["_call_id"] = cid
                await emit("choice", {"card": card})
                await sessions.append_message(
                    sid, role="event",
                    content=f"Выбор: {card['question']}",
                    payload={"kind": "choice", "card": card},
                )
                return results, card
        else:
            await emit("tool_call", {"tool": name, "arguments": tc["arguments"]})
            out = await execute_tool(name, tc["arguments"])

        status = "ok"
        try:
            parsed_out = json.loads(out)
            if isinstance(parsed_out, dict) and parsed_out.get("error"):
                status = "error"
        except json.JSONDecodeError:
            pass
        await journal.log_action(
            session_id=sid, tool=name, risk=risk, args=args_obj,
            status=status, result=out,
            project_id=_pid_of(args_obj),
        )
        excerpt = out[:400]
        await sessions.append_message(
            sid, role="tool",
            content=f"{name}: {excerpt}",
            payload={"tool": name, "status": status},
        )
        await emit("tool_result", {"tool": name, "status": status, "out": excerpt})
        results[cid] = out
    return results, None


def _pid_of(args_obj: dict[str, Any]) -> int | None:
    raw = args_obj.get("project_id")
    return int(raw) if str(raw or "").isdigit() else None


async def _drive_turn(
    sess,
    items: list[dict[str, Any]],
    emit: OnEvent,
    max_iterations: int,
) -> dict[str, Any]:
    """Итерационный цикл LLM↔tools до финального текста / паузы / лимита."""
    sid = sess.id
    session_uuid = sess.uuid
    autonomy = sess.autonomy or "operator"
    max_risk = AUTONOMY_MAX_RISK.get(autonomy, "write")

    await sessions.save_state(sid, status=AgentSessionStatus.running, input_items=items)
    await emit("status", {"status": "running"})

    final_text = ""
    iterations = 0
    for i in range(max_iterations):
        iterations = i + 1
        if session_uuid in _stop_flags:
            final_text = "⏹ Остановлено пользователем."
            await sessions.append_message(sid, role="event", content=final_text)
            break
        res = await gpt_api.chat(
            prompt="",
            input_items=items,
            tools=tool_schemas(max_risk),
            max_retries=1,
        )
        if not res.tool_calls:
            final_text = res.text.strip()
            items.append({"role": "assistant", "content": final_text})
            await sessions.append_message(sid, role="assistant", content=final_text)
            await emit("assistant", {"text": final_text})
            break

        fc_items = _extract_fc_items(res.raw)
        results, choice_card = await _handle_tool_calls(sid, autonomy, res, emit)

        if choice_card is not None:
            # Пауза: echo function_call БЕЗ output — output придёт с ответом.
            items = items + fc_items
            await sessions.save_state(
                sid,
                status=AgentSessionStatus.waiting_choice,
                input_items=items,
                pending_choice=choice_card,
            )
            await emit("status", {"status": "waiting_choice"})
            return {
                "waiting_choice": True,
                "choice": choice_card,
                "session": session_uuid,
                "iterations": iterations,
            }

        items = gpt_api.responses_tool_followup_input(
            items, {"output": fc_items}, results
        )
        await sessions.save_state(sid, input_items=items)
    else:
        final_text = f"⚠️ Достигнут лимит {max_iterations} итераций — уточни задачу."
        await sessions.append_message(sid, role="assistant", content=final_text)
        await emit("assistant", {"text": final_text})

    await sessions.save_state(sid, status=AgentSessionStatus.idle, input_items=items)
    await emit("status", {"status": "idle"})
    return {"text": final_text, "iterations": iterations, "session": session_uuid}


async def run_agent_turn(
    session_uuid: str,
    user_text: str,
    *,
    on_event: OnEvent | None = None,
    max_iterations: int = MAX_ITERATIONS,
) -> dict[str, Any]:
    """Один ход: user_text → ответ агента (с tool calls внутри)."""

    async def _noop(_kind: str, _data: dict[str, Any]) -> None:
        return None

    emit = on_event or _noop

    sess = await sessions.get_session_by_uuid(session_uuid)
    if sess is None:
        return {"error": f"session {session_uuid} not found"}
    if session_uuid in _running_sessions:
        return {"error": "session is already running a turn"}

    _running_sessions.add(session_uuid)
    _stop_flags.discard(session_uuid)
    try:
        if sess.status == AgentSessionStatus.waiting_choice:
            return {
                "error": "session ждёт ответа на карточку выбора — "
                "сначала POST /answer"
            }
        await sessions.append_message(sess.id, role="user", content=user_text)
        items: list[dict[str, Any]] = list(sess.input_items or [])
        if not items:
            items.append({"role": "system", "content": SYSTEM_PROMPT})
        items.append({"role": "user", "content": user_text})
        return await _drive_turn(sess, items, emit, max_iterations)
    except Exception as e:  # noqa: BLE001
        logger.exception("agent turn failed: {}", e)
        await sessions.save_state(sess.id, status=AgentSessionStatus.error)
        await sessions.append_message(
            sess.id, role="event", content=f"Ошибка хода: {type(e).__name__}: {e}"
        )
        await emit("error", {"error": f"{type(e).__name__}: {e}"})
        return {"error": f"{type(e).__name__}: {e}", "session": session_uuid}
    finally:
        _running_sessions.discard(session_uuid)


async def resume_with_choice(
    session_uuid: str,
    selected_id: str,
    *,
    on_event: OnEvent | None = None,
    max_iterations: int = MAX_ITERATIONS,
) -> dict[str, Any]:
    """Продолжить ход после ответа пользователя на карточку выбора."""

    async def _noop(_kind: str, _data: dict[str, Any]) -> None:
        return None

    emit = on_event or _noop

    sess = await sessions.get_session_by_uuid(session_uuid)
    if sess is None:
        return {"error": f"session {session_uuid} not found"}
    if sess.status != AgentSessionStatus.waiting_choice or not sess.pending_choice:
        return {"error": "session не ждёт выбора"}
    if session_uuid in _running_sessions:
        return {"error": "session is already running a turn"}

    card = sess.pending_choice
    call_id = str(card.get("_call_id") or "")
    options = card.get("options") or []
    selected = next(
        (o for o in options if str(o.get("id")) == str(selected_id)), None
    )
    if selected is None:
        return {"error": f"option {selected_id!r} not in card", "options": options}

    _running_sessions.add(session_uuid)
    _stop_flags.discard(session_uuid)
    try:
        await sessions.append_message(
            sess.id, role="user",
            content=f"[выбор] {selected.get('label')}",
            payload={"choice_id": selected_id},
        )
        items: list[dict[str, Any]] = list(sess.input_items or [])
        items.append(
            gpt_api.make_function_call_output(
                call_id,
                json.dumps(
                    {
                        "selected_id": selected_id,
                        "selected_label": selected.get("label"),
                    },
                    ensure_ascii=False,
                ),
            )
        )
        await sessions.save_state(sess.id, pending_choice=None)
        return await _drive_turn(sess, items, emit, max_iterations)
    except Exception as e:  # noqa: BLE001
        logger.exception("agent resume failed: {}", e)
        await sessions.save_state(sess.id, status=AgentSessionStatus.error)
        return {"error": f"{type(e).__name__}: {e}", "session": session_uuid}
    finally:
        _running_sessions.discard(session_uuid)


async def stop_and_wait(session_uuid: str) -> None:
    stop_agent(session_uuid)
    for _ in range(50):
        if session_uuid not in _running_sessions:
            return
        await asyncio.sleep(0.1)
