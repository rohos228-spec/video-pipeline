"""ReAct loop для AI-агента.

Главный цикл:
1. LLM получает messages + tools → возвращает либо текст, либо tool_calls.
2. Для каждого tool_call:
    - safety check args.
    - если HITL-tool → шлём preview owner'у, ждём решения.
    - выполняем tool.
    - результат как tool message в history.
3. Если LLM вернул content без tool_calls → завершаем как final_answer.
4. Если LLM вызвал tool `final_answer` → завершаем с этим answer.
5. Лимит: max_steps, max_tokens_per_session.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from app.ai_agent.client import AIChatResponse, AIClient
from app.ai_agent.config import AIAgentConfig
from app.ai_agent.knowledge.builder import build_system_prompt
from app.ai_agent.session import RuntimeSession
from app.ai_agent.tools import ALL_TOOLS, get_openai_tools_schema
from app.ai_agent.tools._spec import ToolContext, ToolSpec
from app.models import AISessionMode

# Тип callback'а для HITL-апрува.
# Получает (tool_name, args_dict, runtime_session) → возвращает
# {"decision": "approved"|"rejected"|"clarified", "clarification": str | None}
HITLCallback = Callable[
    [str, dict[str, Any], RuntimeSession],
    Awaitable[dict[str, Any]],
]


# Опциональный callback для прогресс-сообщений в Telegram (редактирование
# сводки на каждом шаге). Принимает RuntimeSession.
ProgressCallback = Callable[[RuntimeSession], Awaitable[None]]


async def run_loop(
    session: RuntimeSession,
    client: AIClient,
    config: AIAgentConfig,
    *,
    hitl_callback: HITLCallback | None = None,
    progress_callback: ProgressCallback | None = None,
    on_step: Callable[[RuntimeSession, AIChatResponse], Awaitable[None]] | None = None,
    on_tool_call: Callable[[str, dict[str, Any], str, RuntimeSession], Awaitable[None]] | None = None,
) -> RuntimeSession:
    """Главный ReAct loop. Возвращает завершённую RuntimeSession.

    hitl_callback — для tools с is_hitl=True. Если None и есть HITL-tool —
    автоматический reject (для тестов / QA-режима).

    progress_callback — для редактирования прогресс-сообщения в TG.
    on_step / on_tool_call — для аудита (запись в БД).
    """
    # Подготовка system prompt и истории
    if not session.history:
        sys_prompt = build_system_prompt(
            config.repo_root, mode=session.mode.value
        )
        session.add_message("system", sys_prompt)
        session.add_message("user", session.initial_query)

    # Tools (read-only для QA, всё для остальных)
    include_edit = session.mode != AISessionMode.qa
    tools_schema = get_openai_tools_schema(include_edit=include_edit)

    while not session.is_done():
        # Проверки лимитов перед каждым шагом
        if session.cancelled:
            logger.info("ai_agent.loop: session #{} cancelled", session.db_id)
            break
        if session.step_count >= config.max_steps:
            session.final_answer = (
                f"⚠️ Лимит шагов исчерпан ({config.max_steps}). "
                "Задача не завершена. Попробуй разбить на меньшие куски."
            )
            session.finished = True
            break
        if (
            session.tokens_in + session.tokens_out
            >= config.max_tokens_per_session
        ):
            session.final_answer = (
                f"⚠️ Лимит токенов исчерпан "
                f"({session.tokens_in + session.tokens_out} / {config.max_tokens_per_session}). "
                "Задача не завершена."
            )
            session.finished = True
            break

        session.step_count += 1

        # LLM-вызов
        try:
            resp = await client.chat(
                messages=session.history,
                model=session.model,
                tools=tools_schema,
                tool_choice="auto",
                max_tokens=16384,
                temperature=0.2,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("ai_agent.loop: chat error: {}", e)
            session.final_answer = f"❌ Ошибка LLM: {e}"
            session.finished = True
            break

        session.update_cost_from_usage(
            resp.prompt_tokens, resp.completion_tokens, resp.cost_rub
        )

        if on_step:
            try:
                await on_step(session, resp)
            except Exception:  # noqa: BLE001
                logger.exception("ai_agent.loop: on_step callback failed")

        if progress_callback:
            try:
                await progress_callback(session)
            except Exception:  # noqa: BLE001
                logger.exception("ai_agent.loop: progress_callback failed")

        # Случай 1: LLM вернул text без tool_calls → завершаем
        if not resp.tool_calls:
            if resp.content:
                session.final_answer = resp.content
                # тоже добавим в историю как assistant
                session.add_message("assistant", resp.content)
            else:
                session.final_answer = "⚠️ LLM вернул пустой ответ."
            session.finished = True
            break

        # Случай 2: LLM вызвал один или несколько tools
        # Добавим assistant-сообщение с tool_calls (требует OpenAI протокол)
        session.add_message(
            "assistant", resp.content, tool_calls=resp.tool_calls
        )

        # Выполняем каждый tool по очереди
        for tc in resp.tool_calls:
            # cancel может прилететь из внешней task между шагами loop'а.
            # mypy --strict думает что unreachable (false positive из-за
            # session.is_done() в начале while), поэтому проверяем явно.
            if getattr(session, "cancelled", False):
                break
            tool_call_id = tc.get("id", "")
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            args_raw = fn.get("arguments", "{}")
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {}

            tool: ToolSpec | None = ALL_TOOLS.get(tool_name)

            if tool is None:
                tool_result: dict[str, Any] | str = {
                    "ok": False,
                    "error": f"unknown tool: {tool_name}",
                }
                session.add_tool_result(
                    tool_call_id, json.dumps(tool_result), tool_name=tool_name
                )
                if on_tool_call:
                    await on_tool_call(tool_name, args, tool_call_id, session)
                continue

            # Лог в БД (если on_tool_call настроен)
            if on_tool_call:
                try:
                    await on_tool_call(
                        tool_name, args, tool_call_id, session
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("ai_agent.loop: on_tool_call failed")

            # HITL для опасных tools
            if tool.is_hitl:
                if hitl_callback is None:
                    tool_result = {
                        "ok": False,
                        "error": "HITL callback not configured — этот tool требует подтверждения owner'а",
                    }
                else:
                    try:
                        decision = await hitl_callback(tool_name, args, session)
                    except asyncio.CancelledError:
                        session.cancelled = True
                        break
                    except Exception as e:  # noqa: BLE001
                        logger.exception(
                            "ai_agent.loop: hitl_callback failed: {}", e
                        )
                        decision = {
                            "decision": "rejected",
                            "clarification": f"HITL error: {e}",
                        }

                    if decision.get("decision") == "approved":
                        tool_result = await _execute_tool(
                            tool, args, config
                        )
                    elif decision.get("decision") == "clarified":
                        clarif = decision.get("clarification", "")
                        tool_result = {
                            "ok": False,
                            "rejected": True,
                            "owner_clarification": clarif,
                            "hint": (
                                "Owner отклонил эту правку и дал пояснение. "
                                "Прочитай clarification и попробуй другой подход."
                            ),
                        }
                    else:
                        # rejected
                        tool_result = {
                            "ok": False,
                            "rejected": True,
                            "reason": decision.get(
                                "reason", "owner rejected the action"
                            ),
                        }
            else:
                # Read-only — выполняем напрямую
                tool_result = await _execute_tool(tool, args, config)

            # Финальный tool — завершаем loop
            if tool.is_terminal and isinstance(tool_result, dict) and tool_result.get(
                "ok"
            ):
                # mypy: tool_result.get(...) возвращает object для dict[str, Any].
                # Явный приводим к str (LLM возвращает строку или None).
                answer_raw = tool_result.get("answer") if isinstance(tool_result, dict) else None
                session.final_answer = (
                    str(answer_raw) if answer_raw
                    else session.final_answer
                    or "(пустой ответ)"
                )
                session.finished = True
                # всё равно добавим tool result в историю для аудита
                session.add_tool_result(
                    tool_call_id,
                    json.dumps(tool_result, ensure_ascii=False),
                    tool_name=tool_name,
                )
                # выходим из выполнения tool_calls
                break

            # Сериализуем результат для tool message
            if isinstance(tool_result, dict):
                content_str = json.dumps(tool_result, ensure_ascii=False)
            else:
                content_str = str(tool_result)
            # Жёсткий лимит на размер tool message (защита от взрыва токенов)
            if len(content_str) > 20_000:
                content_str = content_str[:20_000] + '..."[TRUNCATED in tool result]"'

            session.add_tool_result(
                tool_call_id, content_str, tool_name=tool_name
            )

        # Конец итерации, цикл снова

    return session


async def _execute_tool(
    tool: ToolSpec, args: dict[str, Any], config: AIAgentConfig
) -> Any:
    """Запустить tool с safety-обёрткой и таймаутом."""
    ctx = ToolContext(
        repo_root=config.repo_root,
        tool_timeout_sec=config.tool_timeout_sec,
    )
    try:
        result = await asyncio.wait_for(
            tool.run(args, ctx), timeout=config.tool_timeout_sec + 10
        )
        return result
    except TimeoutError:
        return {"ok": False, "error": f"tool {tool.name} timeout"}
    except Exception as e:  # noqa: BLE001
        logger.exception("ai_agent.tool {} failed: {}", tool.name, e)
        return {"ok": False, "error": f"tool {tool.name} error: {e}"}


async def create_runtime_session(
    config: AIAgentConfig,
    *,
    chat_id: int,
    initial_query: str,
    model: str | None = None,
    mode: AISessionMode = AISessionMode.hitl_edit,
    db_id: int = 0,
    branch: str | None = None,
) -> RuntimeSession:
    """Создать runtime-сессию (без БД-записи — это делает caller)."""
    return RuntimeSession(
        db_id=db_id,
        chat_id=chat_id,
        model=model or config.default_model,
        mode=mode,
        initial_query=initial_query,
        branch=branch,
    )


__all__ = [
    "HITLCallback",
    "ProgressCallback",
    "create_runtime_session",
    "run_loop",
]
