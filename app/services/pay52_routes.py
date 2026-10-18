"""Локальная маршрутизация моделей, которые реально открыты ключами 52pay.

Ключ пустой — маршрут не срабатывает, запрос остаётся на vibecode/kie.
Общие модели Клода (есть и на низком, и на высоком ключе) идут на высокий.
"""

from __future__ import annotations

from dataclasses import dataclass

# catalog id или id из /v1/models → (поле Settings, id в запросе)
_TEXT: dict[str, tuple[str, str]] = {
    "gpt-5.5": ("pay52_gpt_api_key", "gpt-5.5"),
    "gpt-5.6-sol": ("pay52_gpt_api_key", "gpt-5.6-sol"),
    "gpt-5-6-sol": ("pay52_gpt_api_key", "gpt-5.6-sol"),
    "gpt-5.6-terra": ("pay52_gpt_api_key", "gpt-5.6-terra"),
    "claude-fable-5": ("pay52_claude_high_api_key", "claude-fable-5"),
    "claude-haiku-4-5": ("pay52_claude_low_api_key", "claude-haiku-4-5-20251001"),
    "claude-haiku-4-5-20251001": ("pay52_claude_low_api_key", "claude-haiku-4-5-20251001"),
    "claude-sonnet-4-6": ("pay52_claude_high_api_key", "claude-sonnet-4-6"),
    "claude-sonnet-5": ("pay52_claude_high_api_key", "claude-sonnet-5"),
    "claude-sonnet-5-5": ("pay52_claude_high_api_key", "claude-sonnet-5-5"),
    "claude-opus-5": ("pay52_claude_high_api_key", "claude-opus-5"),
    "claude-opus-5-5": ("pay52_claude_high_api_key", "claude-opus-5-5"),
    "glm-5.2": ("pay52_glm_api_key", "glm-5.2"),
    "glm-5.3": ("pay52_glm_api_key", "glm-5.3"),
    "glm-5.3-flash": ("pay52_glm_api_key", "glm-5.3-flash"),
}

# Видео этих ключей не совпадает с kie/outsee id в пикере. Карта для явного вызова.
_VIDEO: dict[str, str] = {
    "seedance-2.0-mini-ref": "pay52_seedance_mini_api_key",
    "seedance 2.0 fast 海外": "pay52_seedance_faces_api_key",
}


@dataclass(frozen=True)
class Pay52TextRoute:
    api_model: str
    api_key: str
    url: str

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


def resolve_text(model: str | None) -> Pay52TextRoute | None:
    raw = (model or "").strip()
    spec = _TEXT.get(raw) or _TEXT.get(raw.lower())
    if not spec:
        return None
    from app.settings import settings

    field, api_model = spec
    key = str(getattr(settings, field, "") or "").strip()
    if not key:
        return None
    base = (settings.pay52_base_url or "https://api.52pay.com/v1").strip().rstrip("/")
    return Pay52TextRoute(api_model=api_model, api_key=key, url=f"{base}/chat/completions")


def video_api_key(model: str | None) -> str:
    raw = (model or "").strip()
    from app.settings import settings

    field = _VIDEO.get(raw) or _VIDEO.get(raw.lower())
    if not field:
        return ""
    return str(getattr(settings, field, "") or "").strip()
