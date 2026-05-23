"""Конфигурация AI-агента: модели, лимиты, URL.

Читает из env-переменных (см. `.env.example`).

ВАЖНО: проект использует Pydantic BaseSettings в app.settings, который
загружает .env В СВОЙ объект settings, но НЕ патчит os.environ. Поэтому
нам нужно загрузить .env самостоятельно через python-dotenv (он уже
есть как зависимость pydantic-settings). Делаем это один раз при импорте
модуля — load_dotenv с override=False (не перетираем shell-export).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Загружаем .env в os.environ — иначе get_config() не видит наши флаги
# при запуске не через pydantic (например `python -c "..."` или прямой
# import из тестов).
try:
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH, override=False)
except ImportError:
    # python-dotenv не установлен — env должны быть в shell.
    pass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class AIAgentConfig:
    """Иммутабельный конфиг AI-агента."""

    # Провайдер
    base_url: str
    api_key: str

    # Модели
    default_model: str
    pro_model: str
    code_model: str

    # Лимиты (защита от runaway-сессий)
    max_tokens_per_session: int
    max_steps: int
    max_tokens_per_day: int
    hitl_timeout_sec: int
    idle_timeout_sec: int
    tool_timeout_sec: int

    # Telegram
    owner_chat_id: int

    # Autoreply: AI отвечает на ЛЮБОЙ текст owner'а в personal chat
    # (когда нет pending input'а в bot.py). По умолчанию выключено —
    # включи через .env AI_AGENT_AUTOREPLY=true.
    autoreply_enabled: bool

    # Каталог репо (root). Все file-операции должны быть внутри.
    repo_root: Path

    @property
    def is_configured(self) -> bool:
        """True если ключ задан и можно начать сессию."""
        return bool(self.api_key and self.api_key.startswith("sk-"))


def get_config(repo_root: Path | None = None) -> AIAgentConfig:
    """Собрать конфиг из env. Дефолты — для aitunnel.ru + gpt-4o-mini."""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]

    base_url = _env_str(
        "ORCHESTRATOR_AI_BASE_URL", "https://api.aitunnel.ru/v1/"
    ).rstrip("/")

    # Fallback на OPENAI_* для совместимости.
    api_key = _env_str("ORCHESTRATOR_AI_API_KEY", "") or _env_str(
        "OPENAI_API_KEY", ""
    )

    autoreply_raw = _env_str("AI_AGENT_AUTOREPLY", "false").lower()
    autoreply_enabled = autoreply_raw in ("1", "true", "yes", "on")

    return AIAgentConfig(
        base_url=base_url,
        api_key=api_key,
        default_model=_env_str("ORCHESTRATOR_AI_MODEL", "gpt-4o-mini"),
        pro_model=_env_str("ORCHESTRATOR_AI_PRO_MODEL", "gpt-4o"),
        code_model=_env_str("AI_AGENT_CODE_MODEL", "claude-opus-4.1"),
        max_tokens_per_session=_env_int(
            "AI_AGENT_MAX_TOKENS_PER_SESSION", 200_000
        ),
        max_steps=_env_int("AI_AGENT_MAX_STEPS", 30),
        max_tokens_per_day=_env_int(
            "AI_AGENT_MAX_TOKENS_PER_DAY", 2_000_000
        ),
        hitl_timeout_sec=_env_int("AI_AGENT_HITL_TIMEOUT_SEC", 1800),
        idle_timeout_sec=_env_int("AI_AGENT_IDLE_TIMEOUT_SEC", 3600),
        tool_timeout_sec=_env_int("AI_AGENT_TOOL_TIMEOUT_SEC", 120),
        owner_chat_id=_env_int("TELEGRAM_OWNER_CHAT_ID", 0),
        autoreply_enabled=autoreply_enabled,
        repo_root=repo_root,
    )
