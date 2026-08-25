"""Каталог голосов ElevenLabs и чтение выбора из Project.meta."""

from __future__ import annotations

from app.models import Project

DEFAULT_ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Адам (Adam)

ELEVENLABS_VOICES: tuple[dict[str, str], ...] = (
    {
        "id": "pNInz6obpgDQGcFmaJgB",
        "name": "Адам (Adam)",
        "description": "глубокий, эпичный голос рассказчика (рекомендуется)",
    },
    {
        "id": "JBFqnCBsd6RMkjVDRZzb",
        "name": "Джордж (George)",
        "description": "тёплый, харизматичный сторителлер",
    },
    {
        "id": "nPczCjzI2devNBz1zQrb",
        "name": "Брайан (Brian)",
        "description": "бархатный, солидный диктор",
    },
    {
        "id": "IKne3meq5aSn9XLyUdCD",
        "name": "Чарли (Charlie)",
        "description": "уверенный, энергичный голос",
    },
    {
        "id": "TX3LPaxmHKxFdv7VOQHJ",
        "name": "Лиам (Liam)",
        "description": "молодой, современный голос",
    },
    {
        "id": "Xb7hH8MSUJpSbSDYk0k2",
        "name": "Алиса (Alice)",
        "description": "чёткий, выразительный женский голос",
    },
    {
        "id": "EXAVITQu4vr4xnSDxMaL",
        "name": "Сара (Sarah)",
        "description": "уверенный, зрелый женский голос",
    },
    {
        "id": "hpp4J3VqNfWAUOO0d1Us",
        "name": "Белла (Bella)",
        "description": "тёплый, яркий женский голос",
    },
    {
        "id": "pFZP5JQG7iQjIQuC4Bku",
        "name": "Лили (Lily)",
        "description": "бархатный, кинематографичный женский голос",
    },
)

_VALID_IDS = frozenset(v["id"] for v in ELEVENLABS_VOICES)


def resolve_elevenlabs_voice_id(project: Project) -> str:
    """ID голоса из meta.node_step_params.audio или Адам по умолчанию."""
    meta = getattr(project, "meta", None) or {}
    raw = meta.get("node_step_params")
    if not isinstance(raw, dict):
        return DEFAULT_ELEVENLABS_VOICE_ID
    audio = raw.get("audio")
    if not isinstance(audio, dict):
        return DEFAULT_ELEVENLABS_VOICE_ID
    vid = audio.get("elevenlabs_voice_id")
    if isinstance(vid, str) and vid in _VALID_IDS:
        return vid
    return DEFAULT_ELEVENLABS_VOICE_ID
