"""Конфигурация API Tracker: параметры Supabase, пути хранения и дефолты."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Корень api-tracker
BASE_DIR = Path(__file__).resolve().parent.parent

# Облако по умолчанию выключено (A17): URL и ключ в исходники не зашиты, их задаёт владелец через
# окружение или .env (SUPABASE_URL, SUPABASE_KEY|SUPABASE_ANON_KEY) вместе с API_TRACKER_CLOUD_ENABLED=true.
# Пустые значения отключают облачную синхронизацию в cloud_sync.py (там проверка `not url or not key`).
DEFAULT_SUPABASE_URL = ""
DEFAULT_SUPABASE_KEY = ""


def _load_env_file() -> None:
    """Загрузить переменные из .env в api-tracker или корне проекта, если есть."""
    candidates = [
        BASE_DIR / ".env",
        BASE_DIR.parent / ".env",
    ]
    for env_path in candidates:
        if env_path.is_file():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("\"'")
                    if k and k not in os.environ:
                        os.environ[k] = v
            except Exception:
                pass


_load_env_file()


def cloud_enabled() -> bool:
    """Облако включено только явным API_TRACKER_CLOUD_ENABLED=true (по умолчанию выключено)."""
    return os.environ.get("API_TRACKER_CLOUD_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def get_supabase_url() -> str:
    """Пустая строка, пока облако не включено явно: cloud_sync тогда не делает соединений."""
    if not cloud_enabled():
        return ""
    return os.environ.get("SUPABASE_URL", "").strip() or DEFAULT_SUPABASE_URL


def get_supabase_key() -> str:
    if not cloud_enabled():
        return ""
    return os.environ.get("SUPABASE_KEY", "").strip() or os.environ.get("SUPABASE_ANON_KEY", "").strip() or DEFAULT_SUPABASE_KEY


def get_appdata_dir() -> Path:
    """Получить надежный глобальный путь к папке настроек пользователя."""
    if sys.platform == "win32":
        app_data = os.environ.get("APPDATA")
        if app_data:
            p = Path(app_data) / "API-Tracker"
            p.mkdir(parents=True, exist_ok=True)
            return p
    home = Path.home() / ".api_tracker"
    home.mkdir(parents=True, exist_ok=True)
    return home
