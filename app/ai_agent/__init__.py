"""AI-агент для Telegram-бота video-pipeline.

Phase I плана. Через aitunnel.ru + gpt-4o-mini с function-calling.
Доступ только owner'у. На правки файлов — обязательный HITL-апрув.

Архитектура:
    app/ai_agent/
        config.py          — env + лимиты
        client.py          — OpenAI-compat клиент (chat.completions с tools)
        safety.py          — whitelist путей, secret-scan
        audit.py           — запись AISession/AIMessage/AIToolCall в БД
        session.py         — runtime state одной сессии
        loop.py            — ReAct loop (LLM → tool → LLM → ... → final_answer)
        knowledge/
            builder.py     — auto-build project_context для system prompt
        tools/
            __init__.py    — реестр всех tools
            fs.py          — read_file, list_dir, search_code, edit_file, write_file
            db.py          — db_query (только SELECT), describe_db
            git.py         — git_status, git_diff, git_branch, git_commit
            gh.py          — gh_pr_create, gh_pr_view, gh_pr_list
            quality.py     — run_ruff, run_pytest, run_mypy
            answer.py      — final_answer (terminal)

Запуск из Telegram:
    /ai <запрос>       — gpt-4o-mini, HITL-edit mode
    /ai pro <запрос>   — gpt-4o, HITL-edit mode
    /ai claude <запрос>— claude-opus-4.1
    /ai auto <запрос>  — auto-режим в feature-ветке agent/ai-<uuid>

См. .cursor/rules/50-ai-agent.mdc и AGENTS.md раздел 16.
"""

from app.ai_agent.config import AIAgentConfig, get_config
from app.ai_agent.safety import (
    SafetyError,
    check_path,
    redact_secrets,
    scan_for_secrets,
)

__all__ = [
    "AIAgentConfig",
    "SafetyError",
    "check_path",
    "get_config",
    "redact_secrets",
    "scan_for_secrets",
]
