"""Smoke-тесты AI-агента: импорт всех модулей, config, базовый клиент.

Запуск:  pytest -q tests/test_ai_agent_smoke.py
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ai_agent_imports() -> None:
    """Все модули AI-агента импортируются без падений."""
    import app.ai_agent  # noqa: F401
    import app.ai_agent.audit  # noqa: F401
    import app.ai_agent.client  # noqa: F401
    import app.ai_agent.config  # noqa: F401
    import app.ai_agent.safety  # noqa: F401


def test_db_models_import() -> None:
    """AISession / AIMessage / AIToolCall есть в app.models."""
    from app.models import (
        AIMessage,
        AIMessageRole,
        AISession,
        AISessionMode,
        AISessionStatus,
        AIToolCall,
        AIToolCallStatus,
    )

    # Базовая проверка enum-ов
    assert AISessionStatus.active.value == "active"
    assert AISessionMode.hitl_edit.value == "hitl_edit"
    assert AIMessageRole.tool.value == "tool"
    assert AIToolCallStatus.pending.value == "pending"

    # Колонки AISession
    cols = {c.name for c in AISession.__table__.columns}
    required = {
        "id",
        "chat_id",
        "started_at",
        "status",
        "mode",
        "model",
        "initial_query",
        "total_tokens_in",
        "total_tokens_out",
        "cost_rub",
        "step_count",
    }
    missing = required - cols
    assert not missing, f"AISession missing columns: {missing}"

    # Колонки AIToolCall (HITL flow)
    cols = {c.name for c in AIToolCall.__table__.columns}
    required = {
        "session_id",
        "openai_call_id",
        "tool_name",
        "args_json",
        "status",
        "result_json",
        "hitl_message_id",
    }
    missing = required - cols
    assert not missing, f"AIToolCall missing columns: {missing}"


def test_config_defaults() -> None:
    """Конфиг с пустым env — дефолты для aitunnel.ru + gpt-4o-mini."""
    from app.ai_agent.config import get_config

    # Очищаем env для теста дефолтов
    env_keys = [
        "ORCHESTRATOR_AI_BASE_URL",
        "ORCHESTRATOR_AI_API_KEY",
        "OPENAI_API_KEY",
        "ORCHESTRATOR_AI_MODEL",
        "AI_AGENT_MAX_TOKENS_PER_SESSION",
        "AI_AGENT_MAX_STEPS",
    ]
    saved = {k: os.environ.pop(k, None) for k in env_keys}
    try:
        cfg = get_config(repo_root=REPO_ROOT)
        assert cfg.base_url == "https://api.aitunnel.ru/v1"
        assert cfg.default_model == "gpt-4o-mini"
        assert cfg.pro_model == "gpt-4o"
        assert cfg.code_model == "claude-opus-4.1"
        assert cfg.max_tokens_per_session == 200_000
        assert cfg.max_steps == 30
        assert cfg.max_tokens_per_day == 2_000_000
        assert cfg.is_configured is False  # ключ пуст
    finally:
        # Возвращаем env
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_config_from_env() -> None:
    """Конфиг читает env-переменные."""
    from app.ai_agent.config import get_config

    with patch.dict(
        os.environ,
        {
            "ORCHESTRATOR_AI_BASE_URL": "https://example.com/v1/",
            "ORCHESTRATOR_AI_API_KEY": "sk-test-1234567890",
            "ORCHESTRATOR_AI_MODEL": "custom-model",
            "AI_AGENT_MAX_STEPS": "50",
        },
    ):
        cfg = get_config(repo_root=REPO_ROOT)
        assert cfg.base_url == "https://example.com/v1"  # rstrip("/")
        assert cfg.default_model == "custom-model"
        assert cfg.max_steps == 50
        assert cfg.is_configured is True


def test_client_requires_config() -> None:
    """AIClient падает с понятной ошибкой если api_key пуст."""
    from app.ai_agent.client import AIClient, AIClientError
    from app.ai_agent.config import get_config

    with patch.dict(
        os.environ,
        {"ORCHESTRATOR_AI_API_KEY": "", "OPENAI_API_KEY": ""},
    ):
        cfg = get_config(repo_root=REPO_ROOT)
        with pytest.raises(AIClientError, match="не сконфигурирован"):
            AIClient(cfg)


def test_audit_json_safe() -> None:
    """audit._json_safe умеет в datetime/bytes/sets без падения."""
    from datetime import datetime

    from app.ai_agent.audit import _json_safe

    obj = {
        "a": 1,
        "b": "str",
        "c": datetime(2026, 5, 23, 12, 0),
        "d": b"bytes",
        "e": {1, 2, 3},  # set → str
        "f": [1, [2, 3]],
        "g": None,
    }
    out = _json_safe(obj)
    import json

    json.dumps(out)  # должен сериализоваться без ошибок
    assert out["a"] == 1
    assert "2026-05-23" in out["c"]
