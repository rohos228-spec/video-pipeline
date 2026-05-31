"""Каталог голосов ElevenLabs и чтение выбора из Project.meta."""

from __future__ import annotations

from app.models import Project

DEFAULT_ELEVENLABS_VOICE_ID = "t6lBrEl93uCiLR1Lgm8v"

ELEVENLABS_VOICES: tuple[dict[str, str], ...] = (
    {
        "id": "TUQNWEvVPBLzMBSVDPUA",
        "name": "Алекс",
        "description": "эпичный голос",
    },
    {
        "id": "hLjwV7lYzk15SWLUmhEH",
        "name": "Маруся",
        "description": "милый голос, тёплый",
    },
    {
        "id": "MWyJiWDobXN8FX3CJTdE",
        "name": "Олег",
        "description": "средний дикторский голос",
    },
    {
        "id": "t6lBrEl93uCiLR1Lgm8v",
        "name": "Алиса",
        "description": "естественный голос",
    },
)

_VALID_IDS = frozenset(v["id"] for v in ELEVENLABS_VOICES)


def resolve_elevenlabs_voice_id(project: Project) -> str:
    """ID голоса из meta.node_step_params.audio или Алиса по умолчанию."""
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
