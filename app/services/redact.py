"""Единый redact секретов (A16): текст, структуры, глобальный фильтр loguru.

Модуль без тяжёлых импортов (только ``re``): его безопасно импортировать из
``disk_guard``, ``observation``, ``log_sink``, хранилищ и роутеров.
Секрет заменяется на ``[redacted]``; обычный текст и числа не меняются.
"""
from __future__ import annotations

import re

PLACEHOLDER = "[redacted]"

# Имена полей, значение которых скрывается целиком (регистр и «-»/«_» не важны).
CREDENTIAL_FIELDS = frozenset({
    "authorization", "proxy_authorization", "cookie", "set_cookie",
    "api_key", "apikey", "access_token", "refresh_token", "id_token",
    "password", "secret", "client_secret", "token",
    "xi_api_key", "x_api_key", "x_auth_token", "signature", "x_amz_signature",
    "x_amz_credential", "x_amz_security_token", "bot_token", "telegram_bot_token",
    "service_role_key", "anon_key", "private_key", "session_token",
})
_CREDENTIAL_SUFFIXES = ("_api_key", "_access_token", "_refresh_token", "_secret",
                        "_password", "_signature", "_bot_token")

_VALUE = r"[^\s\"'<>;,}\]&]+"
_FIELD_NAMES = (r"api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|id[_-]?token|"
                r"password|passwd|secret|client[_-]?secret|cookie|token|signature|"
                r"xi-api-key|x-api-key|x-auth-token|x-amz-signature|x-amz-credential|"
                r"x-amz-security-token|service[_-]?role[_-]?key|private[_-]?key")

# Группа 1 или 2 (префикс) сохраняется, остальное заменяется.
_CREDENTIAL_TEXT = re.compile(
    r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"(\bauthorization[\"']?\s*[:=]\s*[\"']?(?:(?:Bearer|Basic|Token)\s+)?)" + _VALUE + r"|"
    r"(\b(?:" + _FIELD_NAMES + r")[\"']?\s*[:=]\s*[\"']?)" + _VALUE + r"|"
    # Telegram bot token: 8-10 цифр, двоеточие, 35 символов (допускает префикс bot в URL).
    r"(?<!\d)\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])|"
    # Supabase: JWT (anon/service_role) и новые ключи sb_secret_/sb_publishable_/sbp_.
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*|"
    r"\bsb_(?:secret|publishable)_[A-Za-z0-9_-]{8,}|"
    r"\bsbp_[A-Za-z0-9]{20,}|"
    # Google API key.
    r"\bAIza[0-9A-Za-z_-]{35}|"
    # GitHub.
    r"\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|"
    # OpenAI-подобные и Vibecode.
    r"\b(?:sk|vk)-[A-Za-z0-9_-]{8,}"
)


def _sub(m: re.Match) -> str:
    prefix = m.group(1) or m.group(2) or m.group(3) or ""
    return prefix + PLACEHOLDER


def redact_text(text: str) -> str:
    """Скрыть секреты в строке (логи, заметки, ошибки)."""
    if not text:
        return text
    return _CREDENTIAL_TEXT.sub(_sub, text)


def _is_credential_key(key) -> bool:
    k = str(key).lower().replace("-", "_")
    return k in CREDENTIAL_FIELDS or k.endswith(_CREDENTIAL_SUFFIXES)


def redact(value):
    """Числа проходят; секреты скрываются по имени поля и по известному синтаксису."""
    if isinstance(value, dict):
        return {str(k): PLACEHOLDER if _is_credential_key(k) else redact(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(str(value))


def _loguru_patcher(record) -> None:
    try:
        record["message"] = redact_text(record["message"])
        extra = record.get("extra")
        if extra:
            for k in list(extra):
                v = extra[k]
                if isinstance(v, str):
                    extra[k] = redact_text(v)
        exc = record.get("exception")
        if exc is not None and exc.value is not None:
            old = str(exc.value)
            new = redact_text(old)
            if new != old:
                try:
                    clean = type(exc.value)(new)
                except Exception:  # noqa: BLE001
                    clean = RuntimeError(new)
                record["exception"] = type(exc)(exc.type, clean, exc.traceback)
    except Exception:  # noqa: BLE001 - лог не должен падать из-за redact
        pass


def install_log_redaction() -> None:
    """Глобальный patcher loguru: сообщение, extra и текст исключения чистятся для всех приёмников."""
    from loguru import logger
    logger.configure(patcher=_loguru_patcher)
